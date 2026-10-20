"""Ocorrência→fila atômica; modelo controlado e processo POSIX real."""

import hashlib
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from uuid import uuid4

import httpx
import pytest
from test_tasks_execution import command
from test_tool_cycle import state as state

from bees_core.approvals import ApprovalError
from bees_core.models import Conversation
from bees_core.resource_tools import ResourceToolService
from bees_core.routine_scheduler import RoutineScheduler
from bees_core.routines import (
    CreateRoutine,
    RoutineDefinition,
    RoutineService,
    SetRoutineStatus,
    command_hash,
)
from bees_core.skills import CreateSkill, EnableSkill, SkillDefinition, SkillService
from bees_core.storage.database import Database
from bees_core.tasks import TaskError


@pytest.fixture
def schedule(state):
    from test_routines import instant

    command(state.tasks, state.agent, state.task, "cancel")
    with state.store.transaction() as unit:
        destination = unit.conversations.create(Conversation(agent_id=state.agent.id))
    service = SkillService(state.database)
    skill = service.create(
        state.agent.id,
        CreateSkill(
            client_request_id=uuid4(),
            definition=SkillDefinition(
                name="Rotina de texto",
                purpose="Normalizar entrada própria.",
                inputs=[{"name": "texto", "description": "Texto próprio"}],
                steps=[{"title": "Normalizar", "instruction": "Use trim e confira a saída."}],
                resources={"environment": "own", "tools": ["text.normalize"]},
                output="Texto normalizado.",
                completion="Resultado da ferramenta conferido.",
            ),
        ),
    )
    service.enable(
        state.agent.id,
        skill.id,
        EnableSkill(client_request_id=uuid4(), expected_revision=1, version=1, enabled=True),
    )
    now = instant("2026-01-01T09:00:15+00:00")
    routines = RoutineService(state.database, clock=lambda: now - timedelta(hours=1))
    definition = RoutineDefinition(
        name="Conferir diariamente",
        instructions="Normalize o texto e confira.",
        schedule={"frequency": "daily", "time": "09:00"},
        timezone="UTC",
        skill_id=skill.id,
        skill_version=1,
        inputs={"texto": "  Olá  "},
        environment="own",
        destination={"conversation_id": destination.id},
        notification="always",
        execution_mode="scheduled",
    )
    routine = routines.create(
        state.agent.id, CreateRoutine(client_request_id=uuid4(), definition=definition, active=True)
    )
    scheduler = RoutineScheduler(state.database, clock=lambda: now)
    return scheduler, routines, routine, definition, now, destination


def occurrences(state, schedule):
    return schedule[1].occurrences(state.agent.id, schedule[2].id)


def own_task(state, schedule):
    occurrence = next(x for x in occurrences(state, schedule) if x.task_id)
    state.task = state.tasks.detail(state.agent.id, occurrence.task_id).task
    return occurrence


def count_tasks(state):
    with state.store.transaction(write=False) as unit:
        return unit.agents._context.connection.execute("SELECT count(*) FROM tasks").get


def complete(state, schedule, *, decision="allow_once"):
    own_task(state, schedule)
    assert state.work() and state.work()
    resources = ResourceToolService(state.database, state.resource.identity)
    pending = resources.list(state.agent.id, task_id=state.task.id)
    if pending:
        resources.decide(
            state.agent.id,
            pending[0].id,
            {"client_request_id": uuid4(), "expected_revision": 1, "decision": decision},
        )
    assert state.work() and state.work()
    assert state.detail().task.status == "completed"
    schedule[0].synchronize(now=schedule[4])


def test_single_task_common_worker_own_approval_result_and_exactly_one_notice(state, schedule):
    scheduler, _, routine, _, now, destination = schedule
    assert scheduler.tick() == 2
    occurrence = own_task(state, schedule)
    assert occurrence.status == "running" and state.task.routine_id == routine.id
    assert scheduler.tick() == 0 and count_tasks(state) == 2
    assert (
        state.task.metadata["routine_execution"]["definition"]["instructions"]
        == routine.instructions
    )
    complete(state, schedule)
    assert state.starts() == 1 and len(state.requests) == 2
    assert occurrences(state, schedule)[0].status == "completed"
    reopened = RoutineScheduler(Database(state.database.path), clock=lambda: now)
    assert reopened.tick() == 0 and count_tasks(state) == 2
    with state.store.transaction(write=False) as unit:
        notices = unit.messages.list(conversation_id=destination.id)
        assert len(notices) == 1 and notices[0].metadata["task_id"] == str(state.task.id)
        assert "Olá" not in notices[0].content


def test_two_dispatchers_link_only_one_task(state, schedule):
    scheduler, routines, routine, _, now, _ = schedule
    [occurrence] = routines.evaluate(state.agent.id, routine.id, expected_revision=1, now=now)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: scheduler.dispatch(occurrence.id), range(2)))
    assert sorted(results) == [False, True] and count_tasks(state) == 2
    assert occurrences(state, schedule)[0].task_id is not None


def test_crash_inside_transaction_rolls_back_queue_and_occurrence_then_retry_is_safe(
    state, schedule
):
    scheduler, routines, routine, _, now, _ = schedule
    [occurrence] = routines.evaluate(state.agent.id, routine.id, expected_revision=1, now=now)
    script = """import os,sys
from datetime import datetime
from bees_core.storage.database import Database
from bees_core.routine_scheduler import RoutineScheduler
s=RoutineScheduler(Database(sys.argv[1]))
s._link_task=lambda *args:os._exit(23)
s.dispatch(sys.argv[2],now=datetime.fromisoformat(sys.argv[3]))
"""
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            script,
            str(state.database.path),
            str(occurrence.id),
            now.isoformat(),
        ],
        capture_output=True,
        timeout=15,
    )
    assert result.returncode == 23 and count_tasks(state) == 1
    assert occurrences(state, schedule)[0].status == "pending"
    assert scheduler.dispatch(occurrence.id) and count_tasks(state) == 2
    assert not scheduler.dispatch(occurrence.id)


def test_crash_after_commit_and_restart_does_not_create_another_task(state, schedule):
    scheduler, routines, routine, _, now, _ = schedule
    [occurrence] = routines.evaluate(state.agent.id, routine.id, expected_revision=1, now=now)
    script = """import os,sys
from datetime import datetime
from bees_core.storage.database import Database
from bees_core.routine_scheduler import RoutineScheduler
s=RoutineScheduler(Database(sys.argv[1]))
assert s.dispatch(sys.argv[2],now=datetime.fromisoformat(sys.argv[3]))
os._exit(24)
"""
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            script,
            str(state.database.path),
            str(occurrence.id),
            now.isoformat(),
        ],
        capture_output=True,
        timeout=15,
    )
    assert result.returncode == 24 and count_tasks(state) == 2
    assert not RoutineScheduler(Database(state.database.path)).dispatch(occurrence.id, now=now)
    assert scheduler.tick() == 0 and count_tasks(state) == 2


def test_old_preparatory_schedule_never_becomes_operational_and_old_request_hash_survives(
    state, schedule
):
    scheduler, routines, _, definition, now, _ = schedule
    preparatory = definition.model_copy(update={"execution_mode": "prepare"})
    value = CreateRoutine(client_request_id=uuid4(), definition=preparatory, active=True)
    old = value.model_dump(mode="json")
    assert old["proposal_source"] is None
    old.pop("proposal_source")
    old["definition"].pop("execution_mode")
    old["definition"].pop("runtime_binding")
    assert (
        command_hash(value)
        == hashlib.sha256(
            json.dumps(old, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
    )
    routine = routines.create(state.agent.id, value)
    scheduler.tick()
    assert routines.occurrences(state.agent.id, routine.id) == []
    assert routines.create(state.agent.id, value).id == routine.id
    assert routines.get(state.agent.id, routine.id).next_run_at <= now


@pytest.mark.parametrize("change", ["pause", "cancel", "revoke"])
def test_pause_cancel_or_revoke_before_effect_does_not_use_old_approval(state, schedule, change):
    scheduler, routines, routine, _, _, _ = schedule
    scheduler.tick()
    own_task(state, schedule)
    assert state.work() and state.work()
    resources = ResourceToolService(state.database, state.resource.identity)
    approval = resources.list(state.agent.id, task_id=state.task.id)[0]
    resources.decide(
        state.agent.id,
        approval.id,
        {"client_request_id": uuid4(), "expected_revision": 1, "decision": "allow_once"},
    )
    if change == "revoke":
        state.tools.set_grant(
            state.agent.id,
            state.plugin.id,
            "text.normalize",
            {"expected_revision": state.grant.revision, "enabled": False},
        )
    else:
        routines.set_status(
            state.agent.id,
            routine.id,
            SetRoutineStatus(
                client_request_id=uuid4(),
                expected_revision=1,
                status="paused" if change == "pause" else "cancelled",
            ),
        )
    assert state.work() and state.starts() == 0
    assert state.detail().task.status != "completed"
    with pytest.raises(ApprovalError):
        resources.decide(
            state.agent.id,
            approval.id,
            {"client_request_id": uuid4(), "expected_revision": 2, "decision": "allow_once"},
        )


def test_saved_rule_reused_only_for_same_routine_revision_inputs_and_resource(state, schedule):
    scheduler, routines, routine, _, now, _ = schedule
    scheduler.tick()
    complete(state, schedule, decision="allow_rule")
    assert state.starts() == 1
    scheduler.tick(now=now + timedelta(days=1))
    own_task(state, schedule)
    state.requests.clear()
    assert state.work() and state.work() and state.work()
    assert state.detail().task.status == "completed" and state.starts() == 2
    assert (
        ResourceToolService(state.database, state.resource.identity).list(
            state.agent.id, task_id=state.task.id
        )
        == []
    )
    scheduler.synchronize(now=now + timedelta(days=1))
    assert len(routines.occurrences(state.agent.id, routine.id)) == 2


def test_unknown_preserved_and_next_occurrence_skipped_without_automatic_generation(
    state, schedule
):
    scheduler, _, _, _, now, _ = schedule
    scheduler.tick()
    own_task(state, schedule)

    def timeout(_):
        raise httpx.ReadTimeout("Controlled unknown")

    state.worker.providers.transport = httpx.MockTransport(timeout)
    assert state.work()
    assert state.detail().unknown_model_calls == 1
    scheduler.synchronize(now=now)
    assert occurrences(state, schedule)[0].status == "outcome_unknown"
    scheduler.tick(now=now + timedelta(days=1))
    assert any(
        item.reason == "overlap" and item.status == "skipped"
        for item in occurrences(state, schedule)
    )
    assert count_tasks(state) == 2 and not state.work() and state.starts() == 0


def test_offline_resource_has_explicit_reason_and_never_creates_task(state, schedule, monkeypatch):
    from bees_core.executor_transport.contracts import TransportError

    def unavailable(*_):
        raise TransportError("executor_journal_missing")

    monkeypatch.setattr("bees_core.routine_scheduler.available_runtime", unavailable)
    schedule[0].tick()
    assert count_tasks(state) == 1 and not state.requests
    [item] = occurrences(state, schedule)
    assert item.status == "skipped" and item.reason == "resource_unavailable"


def test_human_skill_api_cannot_replay_scheduler_submission(state, schedule):
    from bees_core.skill_context import SkillTaskInput
    from bees_core.skill_tasks import SkillTaskService

    schedule[0].tick()
    own_task(state, schedule)
    binding = state.task.metadata["skill_execution"]
    with pytest.raises(TaskError, match="idempotency_conflict"):
        SkillTaskService(state.database, state.resource, state.plugin.id).create(
            state.agent.id,
            SkillTaskInput(
                client_request_id=state.task.submission_key,
                skill_id=binding["skill_id"],
                skill_version=binding["version"],
                inputs=binding["inputs"],
            ),
        )


def test_pause_before_first_generation_is_persistent_and_has_no_model_call(state, schedule):
    scheduler, routines, routine, _, _, _ = schedule
    scheduler.tick()
    own_task(state, schedule)
    routines.set_status(
        state.agent.id,
        routine.id,
        SetRoutineStatus(client_request_id=uuid4(), expected_revision=1, status="paused"),
    )
    assert state.work() and not state.requests and state.starts() == 0
    assert (
        state.detail().task.status == "paused"
        and state.detail().runs[-1].error == "routine_unavailable"
    )
    assert occurrences(state, schedule)[0].metadata["task_status"] == "paused"


def test_long_downtime_has_one_skipped_window_notice_and_one_task_without_catchup(state, schedule):
    scheduler, _, _, _, now, destination = schedule
    future = now.replace(year=2046)
    scheduler.tick(now=future)
    assert len(occurrences(state, schedule)) == 2 and count_tasks(state) == 2
    assert scheduler.tick(now=future) == 0
    with state.store.transaction(write=False) as unit:
        assert len(unit.messages.list(conversation_id=destination.id)) == 1


def test_global_stop_does_not_create_occurrences_or_tasks(state, schedule):
    from bees_core.safety import SafetyCommand, SafetyService

    service = SafetyService(state.database)
    first = service.current()
    stopped = service.command(
        SafetyCommand(client_request_id=uuid4(), expected_revision=first["revision"], kind="stop")
    )
    assert (
        schedule[0].tick() == 0 and occurrences(state, schedule) == [] and count_tasks(state) == 1
    )
    service.command(
        SafetyCommand(
            client_request_id=uuid4(), expected_revision=stopped["revision"], kind="resume"
        )
    )
    assert schedule[0].tick() == 2 and count_tasks(state) == 2


def test_new_deny_after_approval_blocks_effect_without_repeating_generation(state, schedule):
    from bees_core.policies import PolicyService

    schedule[0].tick()
    own_task(state, schedule)
    assert state.work() and state.work()
    resources = ResourceToolService(state.database, state.resource.identity)
    approval = resources.list(state.agent.id, task_id=state.task.id)[0]
    resources.decide(
        state.agent.id,
        approval.id,
        {"client_request_id": uuid4(), "expected_revision": 1, "decision": "allow_once"},
    )
    PolicyService(state.database).create(
        state.agent.id,
        {
            "name": "Bloqueio atual",
            "effect": "deny",
            "scope": {"tool_name": "text.normalize", "action": "transform_resource"},
        },
    )
    assert state.work() and state.starts() == 0 and len(state.requests) == 1


def test_resource_change_requires_new_human_revision_without_silent_retarget(
    state, schedule, monkeypatch, tmp_path
):
    from bees_core.executor_transport.client import ProcessTextResource

    scheduler, _, routine, _, _, _ = schedule
    replacement = ProcessTextResource.initialize(
        tmp_path / "replacement", state.resource.identity.installation_id, str(state.agent.id)
    )
    monkeypatch.setattr(
        "bees_core.routine_scheduler.available_runtime", lambda *_: (replacement, state.plugin.id)
    )
    scheduler.tick()
    assert count_tasks(state) == 1 and not state.requests
    assert occurrences(state, schedule)[0].reason == "runtime_changed"
    assert routine.definition.runtime_binding.resource_id != replacement.identity.resource_id


def test_new_ask_is_not_overridden_by_a_saved_rule_from_previous_occurrence(state, schedule):
    from bees_core.policies import PolicyService

    scheduler, _, _, _, now, _ = schedule
    scheduler.tick()
    complete(state, schedule, decision="allow_rule")
    PolicyService(state.database).create(
        state.agent.id,
        {
            "name": "Nova revisão ask",
            "effect": "ask",
            "scope": {"tool_name": "text.normalize", "action": "transform_resource"},
        },
    )
    scheduler.tick(now=now + timedelta(days=1))
    own_task(state, schedule)
    state.requests.clear()
    assert state.work() and state.work()
    assert state.detail().task.status == "waiting_approval" and state.starts() == 1


def test_resource_upgrade_requires_explicit_reselection_before_activation(
    state, schedule, monkeypatch, tmp_path
):
    from bees_core.executor_transport.client import ProcessTextResource
    from bees_core.routines import ReviseRoutine
    from bees_core.storage.store import RevisionConflict

    _, routines, routine, _, _, _ = schedule
    replacement = ProcessTextResource.initialize(
        tmp_path / "upgrade", state.resource.identity.installation_id, str(state.agent.id)
    )
    monkeypatch.setattr(
        "bees_core.tool_runtime.available_runtime", lambda *_: (replacement, state.plugin.id)
    )
    with pytest.raises(RevisionConflict):
        routines.revise(
            state.agent.id,
            routine.id,
            ReviseRoutine(
                client_request_id=uuid4(), expected_revision=1, definition=routine.definition
            ),
        )
    selected = routine.definition.model_copy(update={"runtime_binding": None})
    revised = routines.revise(
        state.agent.id,
        routine.id,
        ReviseRoutine(client_request_id=uuid4(), expected_revision=1, definition=selected),
    )
    assert (
        revised.status == "paused"
        and str(revised.definition.runtime_binding.resource_id) == replacement.identity.resource_id
    )
    active = routines.set_status(
        state.agent.id,
        routine.id,
        SetRoutineStatus(client_request_id=uuid4(), expected_revision=2, status="active"),
    )
    assert active.revision == 3 and count_tasks(state) == 1 and not state.requests
