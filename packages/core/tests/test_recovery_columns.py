"""Incomplete table projections are conflicts, never raw SQL errors or empty evidence."""

from uuid import uuid4

import apsw
import pytest
from test_recovery_inventory import inventory_fixture, model_call

from bees_core.models import Action, Task
from bees_core.recovery import RecoveryInventoryService
from bees_core.storage.store import IntegrityError, NotFoundError

COLUMNS = {
    "tasks": "id agent_id revision status",
    "runs": "id task_id revision status created_at updated_at started_at finished_at",
    "actions": "id run_id revision tool_name status created_at updated_at unknown_acknowledged_at",
    "model_calls": "id run_id revision status created_at updated_at unknown_acknowledged_at",
    "executor_commands": "id agent_id tool_name executor_id computer_id status error_code",
    "executors": "id kind",
    "computers": "id agent_id executor_id",
}
METHOD_TABLES = {
    "inspect": ("tasks", "runs", "actions", "model_calls"),
    "actions": tuple(COLUMNS),
    "model_calls": ("tasks", "runs", "model_calls"),
    "runs": ("tasks", "runs"),
}
# Actions do not depend on the model-call store.
METHOD_TABLES["actions"] = tuple(t for t in COLUMNS if t != "model_calls")
CASES = [
    (m, t, c) for m, tables in METHOD_TABLES.items() for t in tables for c in COLUMNS[t].split()
]


def substitute_table(connection, table, column):
    connection.execute(f"ALTER TABLE {table} RENAME TO preserved_records")
    names = [r[1] for r in connection.execute("PRAGMA table_info(preserved_records)")]
    columns = ",".join('"' + name + '"' for name in names if name != column)
    # Deliberately empty: incomplete stores must not be presented as empty history.
    connection.execute(f"CREATE TABLE {table} AS SELECT {columns} FROM preserved_records WHERE 0")


@pytest.mark.parametrize(("method", "table", "column"), CASES)
def test_recovery_refuses_each_missing_projection_column(
    tmp_path, monkeypatch, method, table, column
):
    db, store, agent, task, run = inventory_fixture(tmp_path)
    with store.transaction() as unit:
        unit.actions.create(Action(run_id=run.id, tool_name="fixture", status="outcome_unknown"))
        model_call(unit, agent, run)
        empty = unit.tasks.create(Task(agent_id=agent.id, title="Empty", objective="Empty"))
    query = getattr(RecoveryInventoryService(db), method)
    before = query(agent.id, task.id)
    kwargs = (
        [{}] if method == "inspect" else [{}, {"after": before.items[0].id}, {"after": uuid4()}]
    )
    queue_table = table in {"executor_commands", "executors", "computers"}
    if queue_table:
        kwargs.pop()  # Cursor ownership is checked before the command projection.
    with db.transaction() as connection:
        substitute_table(connection, table, column)
    with db.transaction(write=False) as connection:
        snapshot = connection.serialize("main")
    # Run without authorizer first so a raw SQL error or false success fails the test.
    with pytest.raises(IntegrityError, match="Invalid recovery history columns"):
        query(agent.id, task.id)
    original = db.connect
    traces = []

    def connect():
        connection = original()

        def authorize(operation, name, field, database, trigger):
            if operation == apsw.SQLITE_READ and name in {table, "preserved_records"}:
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
            for identity in (task.id, empty.id):
                for arguments in [{}] if queue_table and identity == empty.id else kwargs:
                    with pytest.raises(IntegrityError, match="Invalid recovery history columns"):
                        query(agent.id, identity, **arguments)
    assert any("PRAGMA query_only=ON" in sql for sql in traces)
    for owner, identity in [(uuid4(), task.id), (agent.id, uuid4())]:
        with pytest.raises(IntegrityError if table == "tasks" else NotFoundError):
            query(owner, identity)
    with db.transaction() as connection:
        assert connection.serialize("main") == snapshot
        connection.execute(f"DROP TABLE {table}")
        connection.execute(f"ALTER TABLE preserved_records RENAME TO {table}")
    assert query(agent.id, task.id).model_dump(exclude={"observed_at"}) == before.model_dump(
        exclude={"observed_at"}
    )


@pytest.mark.parametrize("method", ["inspect", "actions", "model_calls", "runs"])
def test_column_check_and_projection_share_snapshot_during_schema_change(
    tmp_path, monkeypatch, method
):
    db, _, agent, task, _ = inventory_fixture(tmp_path)
    service = RecoveryInventoryService(db)
    query = getattr(service, method)
    before = query(agent.id, task.id)
    original = service._require_history
    changed = False

    def interleave(connection, tables, **kwargs):
        nonlocal changed
        original(connection, tables, **kwargs)
        if tables == ("tasks",) and not changed:
            assert connection.execute("PRAGMA query_only").get == 1
            with db.transaction() as writer:
                writer.execute("ALTER TABLE tasks RENAME COLUMN status TO lost_status")
            changed = True

    with monkeypatch.context() as patch:
        patch.setattr(service, "_require_history", interleave)
        observed = query(agent.id, task.id)
    assert changed
    assert observed.model_dump(exclude={"observed_at"}) == before.model_dump(
        exclude={"observed_at"}
    )
    with pytest.raises(IntegrityError, match="Invalid recovery history columns"):
        query(agent.id, task.id)
    with db.transaction() as connection:
        connection.execute("ALTER TABLE tasks RENAME COLUMN lost_status TO status")
    assert query(agent.id, task.id).model_dump(exclude={"observed_at"}) == before.model_dump(
        exclude={"observed_at"}
    )
