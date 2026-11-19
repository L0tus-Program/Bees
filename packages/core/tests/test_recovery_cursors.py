"""A corrupt cursor must not turn remaining evidence into an empty page."""

from uuid import uuid4

import apsw
import pytest
from test_recovery_inventory import PRIVATE, inventory_fixture, model_call
from test_recovery_projection import public, replace_value, restore

from bees_core.models import Action, Run
from bees_core.recovery import RecoveryInventoryService
from bees_core.storage.store import IntegrityError, NotFoundError


@pytest.mark.parametrize("method", ["actions", "model_calls", "runs"])
@pytest.mark.parametrize("value", [None, PRIVATE, "2026-01-01T00:00:00", b"private-date"])
def test_invalid_cursor_is_readonly_scoped_and_restorable(tmp_path, monkeypatch, method, value):
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
    before = query(agent.id, task.id, after=first.id)
    assert [item.id for item in before.items] == [second.id]
    with db.transaction() as connection:
        replace_value(connection, method, "created_at", value)
        # Corrupt only the cursor, retaining the next record's original date.
        connection.execute(
            f"UPDATE {method} SET created_at=(SELECT created_at FROM preserved_records "
            f"WHERE preserved_records.id={method}.id) WHERE id!=?",
            (str(first.id),),
        )
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
                query(agent.id, task.id, after=first.id)
            assert PRIVATE not in str(error.value)
            assert error.value.__suppress_context__
        for owner, identity, cursor in [
            (uuid4(), task.id, first.id),
            (agent.id, uuid4(), first.id),
            (agent.id, task.id, uuid4()),
        ]:
            with pytest.raises(NotFoundError):
                query(owner, identity, after=cursor)
    assert any("PRAGMA query_only=ON" in sql for sql in traces)
    with db.transaction() as connection:
        assert connection.serialize("main") == snapshot
        restore(connection, method)
    assert public(query(agent.id, task.id, after=first.id)) == public(before)


@pytest.mark.parametrize("method", ["actions", "model_calls", "runs"])
def test_cursor_does_not_validate_unpublished_fields(tmp_path, method):
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
        connection.execute(f"UPDATE {method} SET status=? WHERE id=?", (PRIVATE, str(first.id)))
    page = query(agent.id, task.id, after=first.id)
    assert [item.id for item in page.items] == [second.id]
    with pytest.raises(IntegrityError):
        query(agent.id, task.id)
