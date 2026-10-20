"""Execução manual humana, recibo durável e caminho POSIX comum."""

import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from uuid import uuid4

import httpx
import pytest
from test_routine_scheduler import complete, count_tasks, occurrences, own_task
from test_routine_scheduler import schedule as schedule
from test_tool_cycle import state as state

from bees_core.executor_transport.contracts import TransportError
from bees_core.routine_context import validate_routine_task
from bees_core.routine_manual import ManualRoutineInput, ManualRoutineService
from bees_core.routines import ReviseRoutine, SetRoutineStatus
from bees_core.safety import SafetyCommand, SafetyService
from bees_core.storage.store import NotFoundError, RevisionConflict
from bees_core.tasks import TaskError
from bees_core.tools import digest


@pytest.fixture
def manual(state, schedule):
    _, routines, routine, definition, now, _ = schedule
    routine = routines.revise(
        state.agent.id,
        routine.id,
        ReviseRoutine(
            client_request_id=uuid4(),
            expected_revision=1,
            definition=definition.model_copy(
                update={"execution_mode": "prepare", "runtime_binding": None}
            ),
        ),
    )
    return (
        ManualRoutineService(state.database, clock=lambda: now),
        routine,
        ManualRoutineInput(client_request_id=uuid4(), expected_revision=2),
    )


def test_manual_paused_prepare_does_not_change_revision_cursor_or_enable_timer(
    state, schedule, manual
):
    service, routine, value = manual
    before = schedule[1].get(state.agent.id, routine.id)
    result = service.execute(state.agent.id, routine.id, value)
    assert result.occurrence.task_id and result.occurrence.metadata["origin"] == "manual"
    assert str(result.runtime_binding.resource_id) == state.resource.identity.resource_id
    assert schedule[1].get(state.agent.id, routine.id) == before
    assert schedule[0].tick() == 0
    assert len(occurrences(state, schedule)) == 1 and count_tasks(state) == 2
    assert state.starts() == 0 and state.requests == []
    assert service.receipt(state.agent.id, routine.id, value.client_request_id) == result


def test_concurrent_same_intent_is_one_task_and_other_intent_cannot_overlap(
    state, schedule, manual
):
    service, routine, value = manual
    with ThreadPoolExecutor(max_workers=2) as pool:
        receipts = list(
            pool.map(lambda _: service.execute(state.agent.id, routine.id, value), range(2))
        )
    assert receipts[0] == receipts[1] and count_tasks(state) == 2
    with pytest.raises(RevisionConflict):
        service.execute(
            state.agent.id,
            routine.id,
            ManualRoutineInput(client_request_id=uuid4(), expected_revision=2),
        )
    assert len(occurrences(state, schedule)) == 1


def test_manual_common_worker_actual_effect_restart_and_one_notice(state, schedule, manual):
    from bees_core.storage.database import Database
    from bees_core.tool_cycle import ToolTaskWorker

    service, routine, value = manual
    result = service.execute(state.agent.id, routine.id, value)
    state.worker = ToolTaskWorker(
        Database(state.database.path),
        state.resource,
        state.plugin.id,
        transport=state.worker.providers.transport,
    )
    complete(state, schedule)
    assert state.starts() == 1 and len(state.requests) == 2
    assert occurrences(state, schedule)[0].status == "completed"
    assert occurrences(state, schedule)[0].metadata == {
        "origin": "manual",
        "task_status": "completed",
    }
    assert service.execute(state.agent.id, routine.id, value) == result
    assert service.receipt(state.agent.id, routine.id, value.client_request_id) == result
    with state.store.transaction(write=False) as unit:
        assert (
            unit.agents._context.connection.execute(
                "SELECT count(*) FROM messages WHERE source='routine_notification'"
            ).get
            == 1
        )


@pytest.mark.parametrize("change", ["pause", "cancel", "revoke"])
def test_current_authority_blocks_manual_after_own_approval(state, schedule, manual, change):
    service, routine, value = manual
    service.execute(state.agent.id, routine.id, value)
    own_task(state, schedule)
    assert state.work() and state.work()
    state.approve()
    if change == "revoke":
        state.tools.set_grant(
            state.agent.id,
            state.plugin.id,
            "text.normalize",
            {"expected_revision": state.grant.revision, "enabled": False},
        )
    else:
        schedule[1].set_status(
            state.agent.id,
            routine.id,
            SetRoutineStatus(
                client_request_id=uuid4(),
                expected_revision=2,
                status="paused" if change == "pause" else "cancelled",
            ),
        )
    assert state.work() and state.starts() == 0
    assert state.detail().task.status != "completed"


def test_receipt_and_replay_survive_edit_offline_and_do_not_create_another_task(
    state, schedule, manual, monkeypatch
):
    service, routine, value = manual
    result = service.execute(state.agent.id, routine.id, value)
    schedule[1].set_status(
        state.agent.id,
        routine.id,
        SetRoutineStatus(client_request_id=uuid4(), expected_revision=2, status="cancelled"),
    )

    def offline(*_):
        raise TransportError("executor_journal_missing")

    monkeypatch.setattr("bees_core.routine_manual.available_runtime", offline)
    assert service.execute(state.agent.id, routine.id, value) == result
    assert service.receipt(state.agent.id, routine.id, value.client_request_id) == result
    assert count_tasks(state) == 2
    with pytest.raises(RevisionConflict):
        service.execute(
            state.agent.id, routine.id, value.model_copy(update={"expected_revision": 3})
        )
    with pytest.raises(NotFoundError):
        service.receipt(state.agent.id, uuid4(), value.client_request_id)


def test_global_stop_and_stale_revision_have_no_manual_side_effect(state, schedule, manual):
    service, routine, value = manual
    with pytest.raises(RevisionConflict):
        service.execute(
            state.agent.id, routine.id, value.model_copy(update={"expected_revision": 1})
        )
    safety = SafetyService(state.database)
    safety.command(
        SafetyCommand(
            client_request_id=uuid4(), expected_revision=safety.current()["revision"], kind="stop"
        )
    )
    with pytest.raises(TaskError, match="global_stop"):
        service.execute(state.agent.id, routine.id, value)
    assert occurrences(state, schedule) == [] and count_tasks(state) == 1


def test_forged_manual_context_without_human_command_is_not_authority(state, schedule, manual):
    service, routine, value = manual
    result = service.execute(state.agent.id, routine.id, value)
    task = state.tasks.detail(state.agent.id, result.occurrence.task_id).task
    for replacement in (
        {"manual_request_id": str(uuid4())},
        {"runtime_binding": {}},
        {"extra": "allow"},
    ):
        binding = task.metadata["routine_execution"] | replacement
        candidate = task.model_copy(
            update={
                "metadata": task.metadata
                | {"routine_execution": binding, "routine_execution_hash": digest(binding)}
            }
        )
        with (
            state.store.transaction(write=False) as unit,
            pytest.raises(TaskError, match="routine_unavailable"),
        ):
            validate_routine_task(unit, candidate)


def test_saved_rule_reused_across_explicit_manual_occurrences_of_same_revision(
    state, schedule, manual
):
    service, routine, value = manual
    service.execute(state.agent.id, routine.id, value)
    complete(state, schedule, decision="allow_rule")
    later = ManualRoutineService(state.database, clock=lambda: schedule[4] + timedelta(seconds=1))
    later.execute(
        state.agent.id,
        routine.id,
        ManualRoutineInput(client_request_id=uuid4(), expected_revision=2),
    )
    own_task(state, schedule)
    state.requests.clear()
    assert state.work() and state.work() and state.work()
    assert state.detail().task.status == "completed" and state.starts() == 2
    schedule[0].synchronize()


def test_crash_during_manual_transaction_rolls_back_everything_and_same_request_recovers(
    state, schedule, manual
):
    service, routine, value = manual
    script = """import os,sys
from datetime import datetime
from bees_core.storage.database import Database
from bees_core.routine_manual import ManualRoutineService,ManualRoutineInput
from bees_core.routine_scheduler import RoutineScheduler
RoutineScheduler._link_task=lambda *args:os._exit(23)
s=ManualRoutineService(Database(sys.argv[1]),clock=lambda:datetime.fromisoformat(sys.argv[5]))
s.execute(sys.argv[2],sys.argv[3],ManualRoutineInput.model_validate_json(sys.argv[4]))
"""
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            script,
            str(state.database.path),
            str(state.agent.id),
            str(routine.id),
            value.model_dump_json(),
            schedule[4].isoformat(),
        ],
        capture_output=True,
        timeout=15,
    )
    assert result.returncode == 23 and count_tasks(state) == 1
    assert occurrences(state, schedule) == []
    with pytest.raises(NotFoundError):
        service.receipt(state.agent.id, routine.id, value.client_request_id)
    assert service.execute(state.agent.id, routine.id, value).occurrence.task_id
    assert count_tasks(state) == 2


def test_unknown_manual_result_blocks_new_request_without_repeating_model_or_effect(
    state, schedule, manual
):
    service, routine, value = manual
    receipt = service.execute(state.agent.id, routine.id, value)
    own_task(state, schedule)

    def lost(_):
        raise httpx.ReadTimeout("Controlled unknown")

    state.worker.providers.transport = httpx.MockTransport(lost)
    assert state.work() and state.detail().unknown_model_calls == 1
    schedule[0].synchronize()
    assert occurrences(state, schedule)[0].status == "outcome_unknown"
    assert service.execute(state.agent.id, routine.id, value) == receipt
    with pytest.raises(RevisionConflict):
        ManualRoutineService(state.database, clock=lambda: schedule[4] + timedelta(days=1)).execute(
            state.agent.id,
            routine.id,
            ManualRoutineInput(client_request_id=uuid4(), expected_revision=2),
        )
    assert count_tasks(state) == 2 and state.starts() == 0 and not state.work()
