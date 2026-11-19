"""Persisted status corruption is a conflict, never a plausible inventory."""

from typing import get_args
from uuid import uuid4

import apsw
import pytest
from test_recovery_inventory import PRIVATE, inventory_fixture, model_call

from bees_core.models import Action, ActionStatus, ModelCall
from bees_core.recovery import RecoveryInventoryService
from bees_core.storage.store import IntegrityError, NotFoundError

INVALID = [
    (table, status)
    for table in ("actions", "model_calls")
    for status in (PRIVATE, None, 7, b"confirmed")
] + [("model_calls", status) for status in ("awaiting_approval", "ready")]


@pytest.mark.parametrize(("table", "status"), INVALID)
def test_invalid_status_refuses_full_inventory_without_repair(tmp_path, monkeypatch, table, status):
    db, store, agent, task, run = inventory_fixture(tmp_path)
    with store.transaction() as unit:
        unit.actions.create(Action(run_id=run.id, tool_name="fixture", status="confirmed"))
        model_call(unit, agent, run)
    service = RecoveryInventoryService(db)
    before = service.inspect(agent.id, task.id)
    # Model external corruption without weakening the production schema/connection.
    with db.transaction() as connection:
        connection.execute(f"ALTER TABLE {table} RENAME TO preserved_records")
        connection.execute(f"CREATE TABLE {table} AS SELECT * FROM preserved_records")
        connection.execute(f"UPDATE {table} SET status=?", (status,))
    with db.transaction(write=False) as connection:
        snapshot = connection.serialize("main")
    original = db.connect
    traces = []

    def connect():
        connection = original()

        def authorize(operation, name, column, database, trigger):
            if operation in {apsw.SQLITE_INSERT, apsw.SQLITE_UPDATE, apsw.SQLITE_DELETE}:
                return apsw.SQLITE_DENY
            if operation == apsw.SQLITE_READ and name in {"actions", "model_calls"}:
                if column not in {"status", "run_id", "unknown_acknowledged_at", ""}:
                    return apsw.SQLITE_DENY
            return apsw.SQLITE_OK

        connection.set_authorizer(authorize)
        connection.set_exec_trace(lambda c, sql, bindings: traces.append(sql) or True)
        return connection

    with monkeypatch.context() as patch:
        patch.setattr(db, "connect", connect)
        for _ in range(2):
            with pytest.raises(IntegrityError, match="^Invalid recovery status$"):
                service.inspect(agent.id, task.id)
        for owner, identity in [(uuid4(), task.id), (agent.id, uuid4())]:
            with pytest.raises(NotFoundError):
                service.inspect(owner, identity)
    assert any("PRAGMA query_only=ON" in sql for sql in traces)
    with db.transaction() as connection:
        assert connection.serialize("main") == snapshot
        connection.execute(f"DROP TABLE {table}")
        connection.execute(f"ALTER TABLE preserved_records RENAME TO {table}")
    assert service.inspect(agent.id, task.id).model_dump(
        exclude={"observed_at"}
    ) == before.model_dump(exclude={"observed_at"})


def test_every_permitted_status_still_has_its_own_count(tmp_path):
    db, store, agent, task, run = inventory_fixture(tmp_path)
    model_states = get_args(ModelCall.model_fields["status"].annotation)
    with store.transaction() as unit:
        for status in get_args(ActionStatus):
            unit.actions.create(Action(run_id=run.id, tool_name="fixture", status=status))
        calls = [model_call(unit, agent, run, n) for n in range(1, len(model_states) + 1)]
    with db.transaction() as connection:
        for call, status in zip(calls, model_states, strict=True):
            connection.execute(
                "UPDATE model_calls SET status=?,response_json=? WHERE id=?",
                (status, "{}" if status == "confirmed" else None, str(call.id)),
            )
    result = RecoveryInventoryService(db).inspect(agent.id, task.id)
    assert result.actions.total == len(get_args(ActionStatus))
    assert result.model_calls.total == len(model_states)
    assert all(getattr(result.actions, status) == 1 for status in get_args(ActionStatus))
    assert all(getattr(result.model_calls, status) == 1 for status in model_states)
    assert result.model_calls.awaiting_approval == result.model_calls.ready == 0


def test_invalid_model_state_beyond_detail_limit_is_not_hidden(tmp_path):
    db, store, agent, task, run = inventory_fixture(tmp_path)
    with store.transaction() as unit:
        calls = [model_call(unit, agent, run, n) for n in range(1, 1003)]
    with db.transaction() as connection:
        connection.execute("PRAGMA ignore_check_constraints=ON")
        connection.execute("UPDATE model_calls SET status='ready' WHERE id=?", (str(calls[-1].id),))
    with pytest.raises(IntegrityError, match="^Invalid recovery status$"):
        RecoveryInventoryService(db).inspect(agent.id, task.id)


@pytest.mark.parametrize("table", ["actions", "model_calls"])
def test_acknowledgement_groups_preserve_counts_and_validate_full_unknown_history(tmp_path, table):
    db, store, agent, task, run = inventory_fixture(tmp_path)
    with store.transaction() as unit:
        if table == "actions":
            records = [
                unit.actions.create(Action(run_id=run.id, tool_name="fixture")) for _ in range(1002)
            ]
        else:
            records = [model_call(unit, agent, run, n) for n in range(1, 1003)]
    stamps = (None, "2026-01-01T00:00:00+00:00", "2026-01-02T00:00:00-03:00")
    with db.transaction() as connection:
        # Extra schema columns must not shadow a grouping alias and hide dates.
        connection.execute(f"ALTER TABLE {table} ADD COLUMN ack TEXT")
        for n, record in enumerate(records):
            connection.execute(
                f"UPDATE {table} SET status='outcome_unknown',unknown_acknowledged_at=? WHERE id=?",
                (stamps[n % 3], str(record.id)),
            )
    service = RecoveryInventoryService(db)
    before = service.inspect(agent.id, task.id)
    counts = getattr(before, table)
    assert counts.total == counts.outcome_unknown == 1002
    assert counts.unknown_acknowledged == 668
    assert counts.unknown_unacknowledged == 334
    query = getattr(service, table)
    page = query(agent.id, task.id, limit=1)
    assert not page.items[0].unknown_acknowledged
    page2 = query(agent.id, task.id, after=page.next_cursor, limit=1)
    assert page2.items[0].unknown_acknowledged
    assert all(item.status == "outcome_unknown" for item in [*page.items, *page2.items])
    with db.transaction() as connection:
        # External corruption must bypass the production immutability trigger
        # through a replacement table, preserving the original for restoration.
        connection.execute(f"ALTER TABLE {table} RENAME TO preserved_records")
        connection.execute(f"CREATE TABLE {table} AS SELECT * FROM preserved_records")
        connection.execute(
            f"UPDATE {table} SET unknown_acknowledged_at=? WHERE id=?",
            (PRIVATE, str(records[-1].id)),
        )
    # A corrupt later item is outside the page but inside the full inventory.
    assert query(agent.id, task.id, limit=1).items == page.items
    with pytest.raises(IntegrityError, match="^Invalid recovery projection$"):
        service.inspect(agent.id, task.id)
    with db.transaction() as connection:
        connection.execute(f"DROP TABLE {table}")
        connection.execute(f"ALTER TABLE preserved_records RENAME TO {table}")
    assert service.inspect(agent.id, task.id).model_dump(
        exclude={"observed_at"}
    ) == before.model_dump(exclude={"observed_at"})
