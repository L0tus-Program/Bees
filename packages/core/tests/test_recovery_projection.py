"""Invalid public rows are sanitized conflicts, never repaired or partly returned."""

from uuid import uuid4

import apsw
import pytest
from test_recovery_actions import command_fixture
from test_recovery_inventory import PRIVATE, inventory_fixture, model_call

from bees_core.models import Action, Run
from bees_core.recovery import RecoveryInventoryService
from bees_core.storage.store import IntegrityError, NotFoundError

METHODS = ("inspect", "actions", "model_calls", "runs")
NONCANONICAL_IDS = (
    "ABCDEF01-2345-4678-9ABC-DEF012345678",
    "abcdef01234546789abcdef012345678",
    "{abcdef01-2345-4678-9abc-def012345678}",
    "urn:uuid:abcdef01-2345-4678-9abc-def012345678",
    b"abcdef01-2345-4678-9abc-def012345678",
)
CASES = (
    [
        (method, table, column, value)
        for method, table in (
            ("actions", "actions"),
            ("model_calls", "model_calls"),
            ("runs", "runs"),
        )
        for column, value in (
            ("id", PRIVATE),
            ("revision", 0),
            ("revision", "1"),
            ("status", PRIVATE),
            ("status", None),
            ("created_at", PRIVATE),
            ("updated_at", "2026-01-01T00:00:00"),
        )
    ]
    + [
        (method, method, "id", value)
        for method in ("actions", "model_calls", "runs")
        for value in NONCANONICAL_IDS
    ]
    + [
        (method, table, "unknown_acknowledged_at", value)
        for method, table in (
            ("inspect", "actions"),
            ("inspect", "model_calls"),
            ("actions", "actions"),
            ("model_calls", "model_calls"),
        )
        for value in (PRIVATE, "2026-01-01T00:00:00", b"invalid-date")
    ]
    + [(method, "tasks", "revision", 0) for method in METHODS]
    + [
        ("inspect", "tasks", "status", PRIVATE),
        ("actions", "actions", "tool_name", ""),
        ("runs", "runs", "started_at", PRIVATE),
        ("runs", "runs", "finished_at", PRIVATE),
    ]
)


def replace_value(connection, table, column, value):
    # External corruption is modeled in a replacement table without weakening
    # production constraints. The original evidence remains available for restore.
    connection.execute(f"ALTER TABLE {table} RENAME TO preserved_records")
    # Expressions remove affinity, allowing genuinely malformed types as well.
    names = [row[1] for row in connection.execute("PRAGMA table_info(preserved_records)")]
    fields = ",".join(f'+"{name}" AS "{name}"' for name in names)
    connection.execute(f"CREATE TABLE {table} AS SELECT {fields} FROM preserved_records")
    connection.execute(f'UPDATE {table} SET "{column}"=?', (value,))


def restore(connection, table):
    connection.execute(f"DROP TABLE {table}")
    connection.execute(f"ALTER TABLE preserved_records RENAME TO {table}")


def public(result):
    return result.model_dump(exclude={"observed_at"})


@pytest.mark.parametrize(("method", "table", "column", "value"), CASES)
def test_invalid_projection_is_readonly_scoped_and_restorable(
    tmp_path, monkeypatch, method, table, column, value
):
    db, store, agent, task, run = inventory_fixture(tmp_path)
    with store.transaction() as unit:
        unit.actions.create(Action(run_id=run.id, tool_name="fixture"))
        model_call(unit, agent, run)
    query = getattr(RecoveryInventoryService(db), method)
    before = query(agent.id, task.id)
    with db.transaction() as connection:
        replace_value(connection, table, column, value)
        if column == "unknown_acknowledged_at":
            connection.execute(f"UPDATE {table} SET status='outcome_unknown'")
    with db.transaction(write=False) as connection:
        snapshot = connection.serialize("main")
    original = db.connect
    traces = []

    def connect():
        connection = original()

        def authorize(operation, name, field, database, trigger):
            if operation in {apsw.SQLITE_INSERT, apsw.SQLITE_UPDATE, apsw.SQLITE_DELETE}:
                return apsw.SQLITE_DENY
            if operation == apsw.SQLITE_READ and (
                name == "preserved_records"
                or field
                in {
                    "parameters_json",
                    "result_json",
                    "metadata_json",
                    "request_json",
                    "response_json",
                    "snapshot_json",
                    "provider_config_json",
                    "checkpoint_json",
                    "arguments_json",
                    "objective",
                    "error",
                }
            ):
                return apsw.SQLITE_DENY
            return apsw.SQLITE_OK

        connection.set_authorizer(authorize)
        connection.set_exec_trace(lambda c, sql, bindings: traces.append(sql) or True)
        return connection

    with monkeypatch.context() as patch:
        patch.setattr(db, "connect", connect)
        for _ in range(2):
            with pytest.raises(IntegrityError, match="^Invalid recovery projection$") as error:
                query(agent.id, task.id)
            assert PRIVATE not in str(error.value)
            assert error.value.__suppress_context__
        for owner, identity in [(uuid4(), task.id), (agent.id, uuid4())]:
            with pytest.raises(NotFoundError):
                query(owner, identity)
    assert any("PRAGMA query_only=ON" in sql for sql in traces)
    with db.transaction() as connection:
        assert connection.serialize("main") == snapshot
        restore(connection, table)
    assert public(query(agent.id, task.id)) == public(before)


@pytest.mark.parametrize("value", [PRIVATE, b"confirmed", 7])
def test_invalid_command_projection_is_not_published_or_repaired(tmp_path, value):
    db, agent, task, _, _, _ = command_fixture(tmp_path)
    service = RecoveryInventoryService(db)
    before = service.actions(agent, task)
    with db.transaction() as connection:
        replace_value(connection, "executor_commands", "status", value)
    with db.transaction(write=False) as connection:
        snapshot = connection.serialize("main")
    for _ in range(2):
        with pytest.raises(IntegrityError, match="^Invalid recovery projection$"):
            service.actions(agent, task)
    with db.transaction() as connection:
        assert connection.serialize("main") == snapshot
        restore(connection, "executor_commands")
    assert public(service.actions(agent, task)) == public(before)


@pytest.mark.parametrize(
    "method,column,value",
    [
        (method, column, value)
        for method in ("actions", "model_calls", "runs")
        for column, value in (
            ("status", PRIVATE),
            ("id", "ABCDEF01-2345-4678-9ABC-DEF012345678"),
            ("unknown_acknowledged_at", PRIVATE),
        )
        if method != "runs" or column != "unknown_acknowledged_at"
    ],
)
def test_projection_validation_stays_within_requested_page(tmp_path, method, column, value):
    db, store, agent, task, run = inventory_fixture(tmp_path)
    with store.transaction() as unit:
        if method == "actions":
            first = unit.actions.create(Action(run_id=run.id, tool_name="one"))
            second = unit.actions.create(Action(run_id=run.id, tool_name="two"))
        elif method == "model_calls":
            first, second = model_call(unit, agent, run), model_call(unit, agent, run, 2)
        else:
            first, second = run, unit.runs.create(Run(task_id=task.id))
    query = getattr(RecoveryInventoryService(db), method)
    with db.transaction() as connection:
        connection.execute("PRAGMA ignore_check_constraints=ON")
        connection.execute(f'UPDATE {method} SET "{column}"=? WHERE id=?', (value, str(second.id)))
    first_page = query(agent.id, task.id, limit=1)
    assert [item.id for item in first_page.items] == [first.id]
    assert first_page.has_more and first_page.next_cursor == first.id
    with pytest.raises(IntegrityError, match="^Invalid recovery projection$"):
        query(agent.id, task.id, after=first_page.next_cursor, limit=1)
    with pytest.raises(NotFoundError):
        query(agent.id, task.id, after=uuid4())
    for limit in [0, 101, True, "50"]:
        with pytest.raises(ValueError):
            query(agent.id, task.id, limit=limit)
