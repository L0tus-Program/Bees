"""Canonical recovery snapshot without reading private payloads or dispatching."""

import json
from typing import get_args
from uuid import uuid4

import apsw
import pytest
from pydantic import ValidationError
from test_tasks_execution import fixture_state

from bees_core.models import Action, ActionStatus, ModelCall, Run, Task, utc_now
from bees_core.recovery import RecoveryCounts, RecoveryInventoryService
from bees_core.storage.store import ModelCalls, NotFoundError

PRIVATE = "private-payload-path-owner-token"


def inventory_fixture(tmp_path):
    db, store, agent, _, _ = fixture_state(tmp_path)
    task = Task(agent_id=agent.id, title="Diagnóstico", objective=PRIVATE, status="paused")
    run = Run(task_id=task.id, status="paused")
    with store.transaction() as unit:
        unit.tasks.create(task)
        unit.runs.create(run)
    return db, store, agent, task, run


def model_call(unit, agent, run, ordinal=1):
    request = {"messages": [{"role": "user", "content": PRIVATE}], "tools": [], "stream": False}
    config = agent.provider_config
    call = ModelCall(
        run_id=run.id,
        ordinal=ordinal,
        provider_config=config,
        request=request,
        snapshot={"private": PRIVATE},
        request_hash="0" * 64,
        task_control_revision=0,
        agent_revision=1,
        lease_generation=1,
    )
    return unit.model_calls.create(
        call.model_copy(
            update={
                "request_hash": ModelCalls.request_digest(
                    call.provider_config, call.request, call.snapshot
                )
            }
        )
    )


def test_empty_inventory_has_closed_scope_and_never_grants_recovery(tmp_path):
    db, _, agent, task, _ = inventory_fixture(tmp_path)
    result = RecoveryInventoryService(db).inspect(agent.id, task.id)
    assert result.runs_total == 1 and result.actions.total == result.model_calls.total == 0
    assert result.task_revision == task.revision and result.task_status == "paused"
    assert result.executor_evidence == "not_inspected"
    assert result.scope == "all_canonical_task_history"
    assert PRIVATE not in result.model_dump_json()
    assert set(result.model_dump()) == {
        "agent_id",
        "task_id",
        "task_revision",
        "task_status",
        "observed_at",
        "scope",
        "executor_evidence",
        "runs_total",
        "actions",
        "model_calls",
    }


def test_all_states_acknowledgement_and_authorizations_keep_their_meaning(tmp_path):
    db, store, agent, task, run = inventory_fixture(tmp_path)
    with store.transaction() as unit:
        for status in get_args(ActionStatus):
            unit.actions.create(
                Action(
                    run_id=run.id,
                    tool_name="private.tool",
                    status=status,
                    parameters={"private": PRIVATE},
                    metadata={"owner": PRIVATE},
                )
            )
        unit.actions.create(Action(run_id=run.id, tool_name="tool.intent", status="confirmed"))
        unit.actions.create(
            Action(
                run_id=run.id,
                tool_name="private.tool",
                status="outcome_unknown",
                unknown_acknowledged_at=utc_now(),
            )
        )
        call = model_call(unit, agent, run)
    with db.transaction() as connection:
        connection.execute(
            "UPDATE model_calls SET status='outcome_unknown',unknown_acknowledged_at=? WHERE id=?",
            (utc_now().isoformat(), str(call.id)),
        )
    result = RecoveryInventoryService(db).inspect(agent.id, task.id)
    assert result.actions.total == 10 and result.actions.confirmed == 2
    assert result.actions.outcome_unknown == 2
    assert result.actions.unknown_acknowledged == result.actions.unknown_unacknowledged == 1
    assert result.model_calls.outcome_unknown == result.model_calls.unknown_acknowledged == 1
    assert result.model_calls.confirmed == 0
    assert PRIVATE not in result.model_dump_json() and "tool.intent" not in result.model_dump_json()


def test_full_history_exceeds_detail_run_action_and_call_limits(tmp_path):
    db, store, agent, task, run = inventory_fixture(tmp_path)
    with store.transaction() as unit:
        for ordinal in range(1, 1003):
            model_call(unit, agent, run, ordinal)
        for _ in range(1002):
            unit.runs.create(Run(task_id=task.id))
        for _ in range(102):
            unit.actions.create(Action(run_id=run.id, tool_name="fixture", status="confirmed"))
        unit.actions.create(Action(run_id=run.id, tool_name="fixture", status="outcome_unknown"))
    result = RecoveryInventoryService(db).inspect(agent.id, task.id)
    assert result.runs_total == 1003 and result.model_calls.prepared == 1002
    assert result.actions.total == 103 and result.actions.outcome_unknown == 1


def test_agent_task_isolation_precedes_inventory(tmp_path, monkeypatch):
    db, _, agent, task, _ = inventory_fixture(tmp_path)
    service = RecoveryInventoryService(db)
    monkeypatch.setattr(service, "_counts", lambda *a, **k: pytest.fail("Foreign inventory read"))
    for owner, item in [(uuid4(), task.id), (agent.id, uuid4())]:
        with pytest.raises(NotFoundError):
            service.inspect(owner, item)


def test_one_readonly_snapshot_survives_concurrent_canonical_update(tmp_path, monkeypatch):
    db, store, agent, task, run = inventory_fixture(tmp_path)
    with store.transaction() as unit:
        call = model_call(unit, agent, run)
    service = RecoveryInventoryService(db)
    original = service._counts
    updated = False

    def interleave(connection, task_id, *, models):
        nonlocal updated
        assert connection.execute("PRAGMA query_only").get == 1
        with pytest.raises(apsw.ReadOnlyError):
            connection.execute("UPDATE tasks SET status='cancelled' WHERE id=?", (task_id,))
        if not updated:
            updated = True
            with db.transaction() as writer:
                writer.execute(
                    "UPDATE model_calls SET status='outcome_unknown' WHERE id=?", (str(call.id),)
                )
        return original(connection, task_id, models=models)

    monkeypatch.setattr(service, "_counts", interleave)
    result = service.inspect(agent.id, task.id)
    assert result.model_calls.prepared == 1 and result.model_calls.outcome_unknown == 0
    monkeypatch.setattr(service, "_counts", original)
    assert service.inspect(agent.id, task.id).model_calls.outcome_unknown == 1


def test_inventory_never_reads_payload_owner_binding_or_journal_columns(tmp_path, monkeypatch):
    db, store, agent, task, run = inventory_fixture(tmp_path)
    with store.transaction() as unit:
        unit.actions.create(
            Action(run_id=run.id, tool_name="fixture", parameters={"secret": PRIVATE})
        )
        model_call(unit, agent, run)
    original = db.connect
    connections = []

    def connect():
        connection = original()

        def authorize(operation, table, column, database, trigger):
            if operation == apsw.SQLITE_READ and column in {
                "parameters_json",
                "result_json",
                "execution_binding_json",
                "metadata_json",
                "request_json",
                "response_json",
                "snapshot_json",
                "model_config_json",
                "objective",
            }:
                return apsw.SQLITE_DENY
            return apsw.SQLITE_OK

        connection.set_authorizer(authorize)
        connections.append(connection)
        return connection

    monkeypatch.setattr(db, "connect", connect)
    result = RecoveryInventoryService(db).inspect(agent.id, task.id)
    assert result.actions.total == result.model_calls.total == 1
    assert PRIVATE not in json.dumps(result.model_dump(mode="json"))


def test_counts_reject_false_totals_and_acknowledgement_as_confirmation():
    for fields in [
        {"total": 1},
        {"outcome_unknown": 1, "total": 1},
        {"confirmed": True, "total": 1},
        {"total": -1},
        {"extra": 1},
    ]:
        with pytest.raises(ValidationError):
            RecoveryCounts(**fields)


def test_model_states_are_counted_without_publishing_or_opening_response(tmp_path):
    db, store, agent, task, run = inventory_fixture(tmp_path)
    statuses = [
        "prepared",
        "dispatch_started",
        "confirmed",
        "failed_no_effect",
        "outcome_unknown",
        "cancelled",
    ]
    calls = []
    with store.transaction() as unit:
        for ordinal, status in enumerate(statuses, 1):
            calls.append((model_call(unit, agent, run, ordinal), status))
    response = json.dumps(
        {"message": {"role": "assistant", "content": PRIVATE}, "finish_reason": "stop"}
    )
    with db.transaction() as connection:
        for call, status in calls:
            connection.execute(
                "UPDATE model_calls SET status=?,response_json=? WHERE id=?",
                (status, response if status == "confirmed" else None, str(call.id)),
            )
    result = RecoveryInventoryService(db).inspect(agent.id, task.id)
    assert result.model_calls.total == 6 and result.actions.total == 0
    for status in statuses:
        assert getattr(result.model_calls, status) == 1
    assert result.model_calls.unknown_unacknowledged == 1
    assert PRIVATE not in result.model_dump_json()
