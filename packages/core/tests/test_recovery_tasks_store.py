"""The task authority itself must be canonical before any recovery lookup."""

from uuid import uuid4

import apsw
import pytest
from test_recovery_inventory import inventory_fixture, model_call

from bees_core.models import Action
from bees_core.recovery import RecoveryInventoryService
from bees_core.storage.store import IntegrityError, NotFoundError


@pytest.mark.parametrize("method", ["inspect", "actions", "model_calls", "runs"])
@pytest.mark.parametrize("substitute", ["missing", "view", "empty_view", "foreign_view"])
def test_recovery_requires_task_table_without_reading_substitute(
    tmp_path, monkeypatch, method, substitute
):
    db, store, agent, task, run = inventory_fixture(tmp_path)
    with store.transaction() as unit:
        unit.actions.create(Action(run_id=run.id, tool_name="fixture", status="outcome_unknown"))
        model_call(unit, agent, run)
    query = getattr(RecoveryInventoryService(db), method)
    before = query(agent.id, task.id)
    cursor_args = [{}] if method == "inspect" else [{}, {"after": uuid4()}]
    if method != "inspect":
        cursor_args.append({"after": before.items[0].id})
    foreign = uuid4()
    with db.transaction() as connection:
        connection.execute("ALTER TABLE tasks RENAME TO preserved_tasks")
        if substitute in {"view", "empty_view"}:
            condition = " WHERE 0" if substitute == "empty_view" else ""
            connection.execute("CREATE VIEW tasks AS SELECT * FROM preserved_tasks" + condition)
        elif substitute == "foreign_view":
            # A substitute could forge ownership even though real rows are intact.
            connection.execute(
                "CREATE VIEW tasks AS SELECT id,'"
                + str(foreign)
                + "' AS agent_id,revision,status FROM preserved_tasks"
            )
    with db.transaction(write=False) as connection:
        snapshot = connection.serialize("main")
    with pytest.raises(IntegrityError, match="Invalid recovery history store"):
        query(agent.id, task.id)
    original = db.connect
    traces = []

    def connect():
        connection = original()

        def authorize(operation, table, column, database, trigger):
            if operation == apsw.SQLITE_READ and table in {"tasks", "preserved_tasks"}:
                return apsw.SQLITE_DENY
            if operation in {apsw.SQLITE_INSERT, apsw.SQLITE_UPDATE, apsw.SQLITE_DELETE}:
                return apsw.SQLITE_DENY
            return apsw.SQLITE_OK

        connection.set_authorizer(authorize)
        connection.set_exec_trace(lambda c, sql, bindings: traces.append(sql) or True)
        return connection

    with monkeypatch.context() as patch:
        patch.setattr(db, "connect", connect)
        for _ in range(2):
            for owner, identity in [(agent.id, task.id), (foreign, task.id), (agent.id, uuid4())]:
                for kwargs in cursor_args:
                    with pytest.raises(IntegrityError, match="Invalid recovery history store"):
                        query(owner, identity, **kwargs)
    assert any("PRAGMA query_only=ON" in sql for sql in traces)
    with db.transaction() as connection:
        assert connection.serialize("main") == snapshot
        if substitute != "missing":
            connection.execute("DROP VIEW tasks")
        connection.execute("ALTER TABLE preserved_tasks RENAME TO tasks")
    assert query(agent.id, task.id).model_dump(exclude={"observed_at"}) == before.model_dump(
        exclude={"observed_at"}
    )
    with pytest.raises(NotFoundError):
        query(foreign, task.id)
    with pytest.raises(NotFoundError):
        query(agent.id, uuid4())
