"""History pages must reject lost stores before reading records or cursors."""

from uuid import uuid4

import apsw
import pytest
from test_recovery_inventory import inventory_fixture, model_call

from bees_core.models import Action, Task
from bees_core.recovery import RecoveryInventoryService
from bees_core.storage.store import IntegrityError, NotFoundError


@pytest.mark.parametrize(
    ("page", "table"),
    [
        ("actions", "actions"),
        ("actions", "runs"),
        ("model_calls", "model_calls"),
        ("model_calls", "runs"),
        ("runs", "runs"),
    ],
)
@pytest.mark.parametrize("substitute", ["missing", "view", "empty_view"])
def test_pages_require_canonical_history_before_cursor_without_reading_or_repairing(
    tmp_path, monkeypatch, page, table, substitute
):
    db, store, agent, task, run = inventory_fixture(tmp_path)
    with store.transaction() as unit:
        unit.actions.create(Action(run_id=run.id, tool_name="fixture", status="outcome_unknown"))
        model_call(unit, agent, run)
        empty = unit.tasks.create(Task(agent_id=agent.id, title="Empty", objective="Empty"))
    query = getattr(RecoveryInventoryService(db), page)
    before = query(agent.id, task.id)
    cursor = before.items[0].id
    with db.transaction() as connection:
        connection.execute(f"ALTER TABLE {table} RENAME TO preserved_records")
        if substitute != "missing":
            condition = " WHERE 0" if substitute == "empty_view" else ""
            connection.execute(f"CREATE VIEW {table} AS SELECT * FROM preserved_records{condition}")
        schema = connection.execute(
            "SELECT type,name,sql FROM sqlite_schema ORDER BY name"
        ).fetchall()
        tables = [
            "preserved_records" if n == table else n for n in ("runs", "actions", "model_calls")
        ]
        history = {
            n: connection.execute(f"SELECT * FROM {n} ORDER BY id").fetchall() for n in tables
        }
    # Run without authorizer too: base accepts a view or leaks a raw SQL error.
    with pytest.raises(IntegrityError, match="Invalid recovery history store"):
        query(agent.id, task.id)
    original = db.connect
    checked = []

    def connect():
        connection = original()

        def authorize(operation, read_table, column, database, trigger):
            if operation == apsw.SQLITE_READ and read_table in {table, "preserved_records"}:
                return apsw.SQLITE_DENY
            if operation in {apsw.SQLITE_INSERT, apsw.SQLITE_UPDATE, apsw.SQLITE_DELETE}:
                return apsw.SQLITE_DENY
            return apsw.SQLITE_OK

        connection.set_authorizer(authorize)
        connection.set_exec_trace(lambda c, sql, bindings: checked.append(sql) or True)
        return connection

    with monkeypatch.context() as patch:
        patch.setattr(db, "connect", connect)
        for _ in range(2):
            for identity, after in [
                (task.id, None),
                (task.id, cursor),
                (task.id, uuid4()),
                (empty.id, None),
            ]:
                with pytest.raises(IntegrityError, match="Invalid recovery history store"):
                    query(agent.id, identity, after=after)
        for owner, identity in [(uuid4(), task.id), (agent.id, uuid4())]:
            with pytest.raises(NotFoundError):
                query(owner, identity, after=cursor)
    assert any("PRAGMA query_only=ON" in sql for sql in checked)
    with db.transaction() as connection:
        assert (
            connection.execute("SELECT type,name,sql FROM sqlite_schema ORDER BY name").fetchall()
            == schema
        )
        assert {
            n: connection.execute(f"SELECT * FROM {n} ORDER BY id").fetchall() for n in tables
        } == history
        if substitute != "missing":
            connection.execute(f"DROP VIEW {table}")
        connection.execute(f"ALTER TABLE preserved_records RENAME TO {table}")
    assert query(agent.id, task.id).model_dump(exclude={"observed_at"}) == before.model_dump(
        exclude={"observed_at"}
    )
    assert query(agent.id, task.id, after=cursor).items == []
    assert query(agent.id, empty.id).items == []
    with pytest.raises(NotFoundError):
        query(agent.id, task.id, after=uuid4())
