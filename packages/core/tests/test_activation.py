"""CAS reactivation never grants tools, acknowledges unknown or resumes work."""

import asyncio
from uuid import uuid4

import httpx
import pytest
from pydantic import ValidationError
from test_tasks_execution import fixture_state

from bees_core.activation import ActivationError, AgentActivationService, ReactivationInput
from bees_core.models import Action, Run, Task
from bees_core.providers.errors import ProviderError
from bees_core.providers.service import ProviderService
from bees_core.safety import SafetyCommand, SafetyService
from bees_core.storage.store import NotFoundError, RevisionConflict


def activation_fixture(tmp_path):
    db, store, agent, _, _ = fixture_state(tmp_path)
    with store.transaction() as unit:
        task = unit.tasks.create(
            Task(
                agent_id=agent.id,
                title="Paused",
                objective="Private",
                status="paused",
                desired_state="paused",
            )
        )
        run = unit.runs.create(Run(task_id=task.id, status="paused"))
        action = unit.actions.create(
            Action(run_id=run.id, tool_name="fixture", status="outcome_unknown")
        )
        paused = unit.agents.update(agent.model_copy(update={"status": "paused"}), agent.revision)
    safety = SafetyService(db)
    stopped = safety.command(
        SafetyCommand(
            client_request_id=uuid4(), kind="stop", expected_revision=safety.current()["revision"]
        )
    )
    value = ReactivationInput(
        expected_revision=paused.revision,
        expected_safety_revision=stopped["revision"],
        consent="reactivate_paused_agent",
    )
    return db, store, paused, task, run, action, safety, value


def provider(db, callback=None):
    requests = []

    def handle(request):
        requests.append((request.method, request.url.path))
        assert request.method == "GET" and request.url.path == "/v1/models"
        if callback:
            callback()
        return httpx.Response(200, json={"data": [{"id": "fixture-text"}]})

    return ProviderService(db, transport=httpx.MockTransport(handle)), requests


def test_reactivation_changes_only_agent_status_and_revision(tmp_path):
    db, store, agent, task, run, action, safety, value = activation_fixture(tmp_path)
    providers, requests = provider(db)
    before = safety.current()
    receipt = asyncio.run(AgentActivationService(db, providers).reactivate(agent.id, value))
    assert receipt.revision == agent.revision + 1 and receipt.status == "active"
    assert receipt.global_status == "stopped" and receipt.provider_checked
    assert requests == [("GET", "/v1/models")]
    assert safety.current() == before
    with store.transaction(write=False) as unit:
        current = unit.agents.get(agent.id)
        assert (
            current.provider_config == agent.provider_config and current.metadata == agent.metadata
        )
        assert unit.tasks.get(task.id) == task and unit.runs.get(run.id) == run
        assert unit.actions.get(action.id) == action
        assert unit.actions.pending_unknown_count(task.id) == 1
        assert not unit.model_calls.list(run_id=run.id)


def test_missing_stale_archived_and_active_agents_are_rejected_before_probe(tmp_path):
    db, store, agent, _, _, _, _, value = activation_fixture(tmp_path)
    providers, requests = provider(db)
    service = AgentActivationService(db, providers)
    with pytest.raises(NotFoundError):
        asyncio.run(service.reactivate(uuid4(), value))
    with pytest.raises(RevisionConflict):
        asyncio.run(service.reactivate(agent.id, value.model_copy(update={"expected_revision": 1})))
    for status in ["archived", "active"]:
        with store.transaction() as unit:
            previous = unit.agents.get(agent.id)
            current = unit.agents.update(
                previous.model_copy(update={"status": status}), previous.revision
            )
        with pytest.raises(ActivationError, match="activation_not_paused"):
            asyncio.run(
                service.reactivate(
                    agent.id, value.model_copy(update={"expected_revision": current.revision})
                )
            )
    assert requests == []


def test_restored_missing_configuration_requires_human_selection_before_probe(tmp_path):
    db, store, agent, _, _, _, _, value = activation_fixture(tmp_path)
    with store.transaction() as unit:
        blank = unit.agents.update(agent.model_copy(update={"provider_config": {}}), agent.revision)
    providers, requests = provider(db)
    with pytest.raises(ActivationError, match="activation_configuration_required"):
        asyncio.run(
            AgentActivationService(db, providers).reactivate(
                agent.id, value.model_copy(update={"expected_revision": blank.revision})
            )
        )
    assert requests == []


def test_running_or_changed_global_stop_blocks_activation_without_probe(tmp_path):
    db, _, agent, _, _, _, safety, value = activation_fixture(tmp_path)
    providers, requests = provider(db)
    service = AgentActivationService(db, providers)
    with pytest.raises(RevisionConflict):
        asyncio.run(
            service.reactivate(agent.id, value.model_copy(update={"expected_safety_revision": 1}))
        )
    running = safety.command(
        SafetyCommand(
            client_request_id=uuid4(),
            kind="resume",
            expected_revision=value.expected_safety_revision,
        )
    )
    with pytest.raises(ActivationError, match="activation_stop_required"):
        asyncio.run(
            service.reactivate(
                agent.id, value.model_copy(update={"expected_safety_revision": running["revision"]})
            )
        )
    assert requests == []


def test_preflight_failure_leaves_agent_and_global_stop_unchanged(tmp_path):
    db, store, agent, _, _, _, safety, value = activation_fixture(tmp_path)
    calls = []

    def fail(request):
        calls.append(request.method)
        return httpx.Response(503, json={"private": "PRIVATE_UPSTREAM"})

    providers = ProviderService(db, transport=httpx.MockTransport(fail))
    before = safety.current()
    with pytest.raises(ProviderError):
        asyncio.run(AgentActivationService(db, providers).reactivate(agent.id, value))
    with store.transaction(write=False) as unit:
        assert unit.agents.get(agent.id) == agent
    assert safety.current() == before and calls == ["GET"]


def test_configuration_changed_during_probe_is_never_activated(tmp_path):
    db, store, agent, _, _, _, _, value = activation_fixture(tmp_path)

    def change():
        with store.transaction() as unit:
            unit.agents.update(agent.model_copy(update={"purpose": "Changed"}), agent.revision)

    providers, _ = provider(db, change)
    with pytest.raises(ProviderError) as failure:
        asyncio.run(AgentActivationService(db, providers).reactivate(agent.id, value))
    assert failure.value.code == "state_conflict"
    with store.transaction(write=False) as unit:
        assert unit.agents.get(agent.id).status == "paused"


def test_global_resume_during_probe_prevents_commit(tmp_path):
    db, store, agent, _, _, _, safety, value = activation_fixture(tmp_path)

    def resume():
        safety.command(
            SafetyCommand(
                client_request_id=uuid4(),
                kind="resume",
                expected_revision=value.expected_safety_revision,
            )
        )

    providers, _ = provider(db, resume)
    with pytest.raises(RevisionConflict):
        asyncio.run(AgentActivationService(db, providers).reactivate(agent.id, value))
    with store.transaction(write=False) as unit:
        assert unit.agents.get(agent.id).status == "paused"


def test_two_concurrent_requests_only_commit_once(tmp_path):
    db, store, agent, _, _, _, safety, value = activation_fixture(tmp_path)

    async def race():
        ready = asyncio.Event()
        entered = 0

        async def handle(request):
            nonlocal entered
            assert request.method == "GET"
            entered += 1
            if entered == 2:
                ready.set()
            await ready.wait()
            return httpx.Response(200, json={"data": [{"id": "fixture-text"}]})

        service = AgentActivationService(
            db, ProviderService(db, transport=httpx.MockTransport(handle))
        )
        return await asyncio.gather(
            service.reactivate(agent.id, value),
            service.reactivate(agent.id, value),
            return_exceptions=True,
        )

    results = asyncio.run(race())
    assert sum(not isinstance(result, Exception) for result in results) == 1
    assert any(isinstance(result, (RevisionConflict, ProviderError)) for result in results)
    with store.transaction(write=False) as unit:
        assert unit.agents.get(agent.id).revision == agent.revision + 1
    assert safety.current()["status"] == "stopped"


def test_lost_response_retry_uses_original_cas_and_never_activates_twice(tmp_path):
    db, store, agent, _, _, _, _, value = activation_fixture(tmp_path)
    providers, requests = provider(db)
    service = AgentActivationService(db, providers)
    asyncio.run(
        service.reactivate(agent.id, value)
    )  # Simulate a committed reply not observed by caller.
    with pytest.raises(RevisionConflict):
        asyncio.run(AgentActivationService(db, providers).reactivate(agent.id, value))
    with store.transaction(write=False) as unit:
        current = unit.agents.get(agent.id)
        assert current.status == "active" and current.revision == agent.revision + 1
    assert len(requests) == 1


def test_consent_and_revision_contract_are_strict():
    for changes in [
        {"consent": "implicit"},
        {"expected_revision": True},
        {"expected_safety_revision": 0},
        {"extra": "PRIVATE"},
    ]:
        with pytest.raises(ValidationError):
            ReactivationInput.model_validate(
                {
                    "expected_revision": 1,
                    "expected_safety_revision": 1,
                    "consent": "reactivate_paused_agent",
                }
                | changes
            )
