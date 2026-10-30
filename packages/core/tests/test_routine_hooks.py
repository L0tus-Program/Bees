"""Webhook de rotina: segredo único, intervalo mínimo, rotação, revogação e corpo ignorado."""

from datetime import timedelta
from uuid import uuid4

import pytest
from test_agent_routines import setup as setup  # noqa: F401  (fixture)

from bees_core.routine_hooks import CreateHook, HookError, RoutineHookService
from bees_core.routines import CreateRoutine, RoutineDefinition, RoutineService
from bees_core.storage.store import RevisionConflict


def routine(database, agent, destination, skill, now):
    definition = RoutineDefinition(
        name="Ao receber evento",
        instructions="Resuma a fonte.",
        schedule={"frequency": "daily", "time": "09:00"},
        timezone="UTC",
        skill_id=skill.id,
        skill_version=1,
        inputs={"url": "https://example.org/a"},
        environment="none",
        destination={"conversation_id": destination.id},
        notification="always",
        execution_mode="agent",
    )
    return RoutineService(database, clock=lambda: now).create(
        agent.id, CreateRoutine(client_request_id=uuid4(), definition=definition, active=False)
    )


def test_hook_triggers_once_per_interval_and_rotation_revokes(setup):
    from test_routines import instant

    database, store, agent, destination, skill = setup
    now = [instant("2026-01-01T10:00:00+00:00")]
    created = routine(database, agent, destination, skill, now[0])
    hooks = RoutineHookService(database, clock=lambda: now[0])
    row, token = hooks.create(agent.id, created.id, CreateHook(min_interval_seconds=60))
    assert token.startswith("bh_") and token not in str(row)
    with pytest.raises(HookError, match="hook_not_found"):
        hooks.trigger("bh_" + "x" * 43)
    result = hooks.trigger(token)
    assert result["accepted"] and result["task_id"]
    with pytest.raises(HookError, match="hook_rate_limited"):
        hooks.trigger(token)
    with store.transaction(write=False) as unit:
        task = unit.tasks.get(result["task_id"])
    assert task.metadata["execution_kind"] == "agent_task"
    # Ocorrência ainda em curso: um novo disparo é recusado sem segunda tarefa.
    now[0] += timedelta(minutes=2)
    with pytest.raises(HookError, match="hook_rejected"):
        hooks.trigger(token)
    assert hooks.active(agent.id, created.id)["last_result"] == "RevisionConflict"
    rotated, new_token = hooks.create(agent.id, created.id, CreateHook())
    with pytest.raises(HookError, match="hook_not_found"):
        hooks.trigger(token)
    with pytest.raises(RevisionConflict):
        hooks.revoke(agent.id, created.id, rotated["revision"] + 1)
    hooks.revoke(agent.id, created.id, rotated["revision"])
    with pytest.raises(HookError, match="hook_not_found"):
        hooks.trigger(new_token)
    assert hooks.active(agent.id, created.id) is None
