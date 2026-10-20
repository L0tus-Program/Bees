"""Ciclo controlado de modelo HTTP + processo POSIX real, sem modelo pago."""

import asyncio
import json
import os
from dataclasses import dataclass
from uuid import uuid4

import httpx
import pytest
from test_tasks_execution import answer, command, fixture_state

from bees_core.executor_transport.contracts import TransportError
from bees_core.executor_transport.journal import Journal
from bees_core.executor_transport.resource_authority import ResourceToolAuthority
from bees_core.providers.contracts import ChatRequest, ProviderConfig
from bees_core.providers.service import ProviderService
from bees_core.security.identity import IdentityService
from bees_core.storage.database import Database
from bees_core.tasks import TaskError, TaskInput, TaskService
from bees_core.tool_cycle import ToolTaskInput, ToolTaskService, ToolTaskWorker
from bees_core.tool_runtime import ToolQueueWorker, initialize_resource
from bees_core.tools import ToolService


def tool_answer(calls=None):
    calls = calls or [("call_1", "normalize_text", {"text": "  Olá  ", "operation": "trim"})]
    return httpx.Response(
        200,
        json={
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": identity,
                                "type": "function",
                                "function": {"name": name, "arguments": json.dumps(arguments)},
                            }
                            for identity, name, arguments in calls
                        ],
                    },
                    "finish_reason": "tool_calls",
                }
            ]
        },
    )


@dataclass
class Case:
    database: object
    store: object
    agent: object
    resource: object
    tools: object
    plugin: object
    grant: object
    tasks: object
    task: object
    requests: list
    worker: object

    def work(self):
        return asyncio.run(self.worker.run_once())

    def detail(self):
        return self.tasks.detail(self.agent.id, self.task.id)

    def approve(self):
        approvals = self.worker.resources.list(self.agent.id, task_id=self.task.id)
        assert len(approvals) == 1
        return self.worker.resources.decide(
            self.agent.id,
            approvals[0].id,
            {"client_request_id": uuid4(), "expected_revision": 1, "decision": "allow_once"},
        )

    def starts(self):
        journal = Journal(self.resource.directory, self.resource.identity)
        try:
            return journal.connection.execute(
                "SELECT count(*) FROM events WHERE status='started'"
            ).fetchone()[0]
        finally:
            journal.close()


@pytest.fixture
def state(tmp_path):
    if os.name != "posix":
        pytest.skip("O recurso real de processo inicial exige POSIX.")
    config = ProviderConfig(
        kind="openai_compatible",
        endpoint="https://fixture.test/v1",
        model="fixture-tools",
        capabilities={"text": True, "tool_calls": True},
    )
    database, store, agent, _, _ = fixture_state(tmp_path, config=config)
    identity = IdentityService(database)
    identity.setup(identity.issue_bootstrap(), "Pessoa de teste", "senha de teste 123456")
    resource = initialize_resource(database, agent.id)
    tools = ToolService(database)
    plugin = tools.install({"client_request_id": uuid4(), "manifest": tools.catalog()[0]})
    plugin = tools.update_plugin(plugin.id, {"expected_revision": 1, "enabled": True})
    grant = tools.set_grant(
        agent.id, plugin.id, "text.normalize", {"expected_revision": 0, "enabled": True}
    )
    tasks = ToolTaskService(database, resource, plugin.id)
    task = tasks.create(
        agent.id,
        ToolTaskInput(
            client_request_id=uuid4(),
            title="Conferir texto",
            objective="Transforme o texto e confira o resultado",
        ),
    )
    requests = []

    def respond(request):
        requests.append(json.loads(request.content))
        return tool_answer() if len(requests) == 1 else answer("Resultado conferido: Olá")

    worker = ToolTaskWorker(database, resource, plugin.id, transport=httpx.MockTransport(respond))
    return Case(
        database, store, agent, resource, tools, plugin, grant, tasks, task, requests, worker
    )


def test_proposal_wait_restart_approval_actual_effect_and_final_text(state):
    assert state.work()
    assert state.detail().task.status == "queued" and len(state.requests) == 1
    assert state.work()
    assert state.detail().task.status == "waiting_approval" and state.starts() == 0
    assert not state.work() and len(state.requests) == 1
    state.approve()
    # Composição nova após a decisão usa novo dono e o checkpoint canônico.
    previous = state.worker
    state.worker = ToolTaskWorker(
        Database(state.database.path),
        state.resource,
        state.plugin.id,
        transport=previous.providers.transport,
    )
    assert state.work() and state.starts() == 1 and len(state.requests) == 1
    assert state.work() and state.detail().task.status == "completed"
    assert state.starts() == 1 and len(state.requests) == 2
    with state.store.transaction(write=False) as unit:
        calls = unit.model_calls.list(run_id=state.detail().runs[-1].id)
        assert [call.status for call in calls] == ["confirmed", "confirmed"]
    history = ProviderService(state.database).load_history(
        state.agent.id, state.task.conversation_id
    )
    ChatRequest(messages=history)
    results = [json.loads(message.content) for message in history if message.role == "tool"]
    assert results[0]["status"] == "confirmed" and results[0]["output"] == {"text": "Olá"}
    assert "resource_id" not in results[0] and "parameters" not in results[0]


@pytest.mark.parametrize(
    "proposal,code",
    [
        (("bad", "shell", {"command": "anything"}), "tool_unavailable"),
        (
            (
                "bad",
                "normalize_text",
                {"text": "anything", "operation": "trim", "identity": "admin"},
            ),
            "invalid_tool_arguments",
        ),
        (
            ("bad", "normalize_text", {"text": "anything", "operation": "shell"}),
            "invalid_tool_arguments",
        ),
    ],
)
def test_unknown_or_invalid_proposal_has_canonical_rejection_and_zero_effect(state, proposal, code):
    transport = httpx.MockTransport(lambda request: tool_answer([proposal]))
    state.worker.providers.transport = transport
    assert state.work() and state.work()
    history = ProviderService(state.database).load_history(
        state.agent.id, state.task.conversation_id
    )
    ChatRequest(messages=history)
    result = json.loads(next(item.content for item in history if item.role == "tool"))
    assert result == {"status": "rejected", "code": code}
    assert state.starts() == 0


def test_legacy_task_endpoint_cannot_replay_tool_task_as_text_task(state):
    value = ToolTaskInput(client_request_id=uuid4(), title="Modo explícito", objective="Conferir")
    task = state.tasks.create(state.agent.id, value)
    assert state.tasks.create(state.agent.id, value) == task
    with pytest.raises(TaskError, match="idempotency_conflict"):
        TaskService(state.database).create(
            state.agent.id, TaskInput.model_validate(value.model_dump())
        )


def test_cancel_waiting_proposal_closes_the_history_without_authorizing_effect(state):
    assert state.work() and state.work()
    command(state.tasks, state.agent, state.task, "cancel")
    history = ProviderService(state.database).load_history(
        state.agent.id, state.task.conversation_id
    )
    ChatRequest(messages=history)
    assert (
        json.loads(next(item.content for item in history if item.role == "tool"))["status"]
        == "not_started"
    )
    assert state.starts() == 0 and not state.work()


def test_disabled_grant_is_not_offered_and_proposal_cannot_execute(state):
    state.tools.set_grant(
        state.agent.id,
        state.plugin.id,
        "text.normalize",
        {"expected_revision": 1, "enabled": False},
    )
    assert state.work() and "tools" not in state.requests[0]
    assert state.work() and state.starts() == 0
    history = ProviderService(state.database).load_history(
        state.agent.id, state.task.conversation_id
    )
    assert (
        json.loads(next(item.content for item in history if item.role == "tool"))["status"]
        == "rejected"
    )
    assert state.worker.resources.list(state.agent.id, task_id=state.task.id) == []


def test_revocation_after_model_proposal_blocks_effect_without_another_model_call(state):
    assert state.work()
    state.tools.set_grant(
        state.agent.id,
        state.plugin.id,
        "text.normalize",
        {"expected_revision": 1, "enabled": False},
    )
    assert state.work() and state.starts() == 0 and len(state.requests) == 1
    history = ProviderService(state.database).load_history(
        state.agent.id, state.task.conversation_id
    )
    ChatRequest(messages=history)
    assert (
        json.loads(next(item.content for item in history if item.role == "tool"))["status"]
        == "rejected"
    )


def test_last_model_call_still_resolves_its_tool_result_and_pauses_partial(state):
    with state.store.transaction() as unit:
        task = unit.tasks.get(state.task.id)
        unit.tasks.update(task.model_copy(update={"max_calls": 1}), task.revision)
    assert state.work() and state.work()
    assert state.detail().task.status == "waiting_approval"
    state.approve()
    assert state.work()
    detail = state.detail()
    assert detail.task.status == "paused" and detail.runs[-1].error == "tool_cycle_limit"
    assert detail.runs[-1].checkpoint["partial"] is True
    assert detail.task.calls_started == 1 and state.starts() == 1 and len(state.requests) == 1
    assert not state.work()
    ChatRequest(
        messages=ProviderService(state.database).load_history(
            state.agent.id, state.task.conversation_id
        )
    )


def test_batch_over_limit_has_closed_rejections_and_no_effect(state):
    proposals = [
        (f"call_{index}", "normalize_text", {"text": "sample", "operation": "trim"})
        for index in range(5)
    ]
    state.worker.providers.transport = httpx.MockTransport(lambda request: tool_answer(proposals))
    assert state.work() and state.work() and state.starts() == 0
    history = ProviderService(state.database).load_history(
        state.agent.id, state.task.conversation_id
    )
    ChatRequest(messages=history)
    assert len([item for item in history if item.role == "tool"]) == 5
    assert (
        state.detail().task.status == "paused"
        and state.detail().runs[-1].error == "tool_cycle_limit"
    )
    assert not state.work()


def test_unknown_effect_keeps_complete_block_and_never_repeats_after_restart(state, monkeypatch):
    assert state.work() and state.work()
    state.approve()

    def lost(*args):
        raise TransportError("executor_unavailable")

    monkeypatch.setattr(ResourceToolAuthority, "complete", lost)
    assert state.work()
    assert state.starts() == 1 and len(state.requests) == 1
    detail = state.detail()
    assert detail.unknown_tool_actions == 1 and detail.task.status == "paused"
    assert not state.work()
    reopened = ToolTaskWorker(
        Database(state.database.path),
        state.resource,
        state.plugin.id,
        transport=state.worker.providers.transport,
    )
    assert not asyncio.run(reopened.run_once())
    history = ProviderService(state.database).load_history(
        state.agent.id, state.task.conversation_id
    )
    ChatRequest(messages=history)
    assert json.loads(next(item.content for item in history if item.role == "tool")) == {
        "status": "outcome_unknown",
        "code": "tool_outcome_unknown",
    }
    assert state.starts() == 1 and len(state.requests) == 1


def test_lost_paid_generation_is_unknown_without_proposal_effect_or_retry(state):
    attempts = []

    def lost(request):
        attempts.append(request)
        raise httpx.ReadError("controlled lost response")

    state.worker.providers.transport = httpx.MockTransport(lost)
    assert state.work()
    detail = state.detail()
    assert detail.task.status == "paused" and detail.unknown_model_calls == 1
    assert not state.work() and len(attempts) == 1 and state.starts() == 0


def test_resume_waiting_proposal_closes_old_block_and_requires_new_approval(state):
    assert state.work() and state.work()
    old_approval = state.worker.resources.list(state.agent.id, task_id=state.task.id)[0]
    command(state.tasks, state.agent, state.task, "resume")
    assert state.worker.resources.view(state.agent.id, old_approval.id)["valid"] is False
    history = ProviderService(state.database).load_history(
        state.agent.id, state.task.conversation_id
    )
    ChatRequest(messages=history)
    assert (
        json.loads(next(item.content for item in history if item.role == "tool"))["status"]
        == "not_started"
    )
    assert state.work() and len(state.requests) == 2
    assert state.detail().task.status == "completed" and state.starts() == 0


def test_control_during_paid_proposal_cannot_authorize_effect(state):
    def pause_then_respond(request):
        command(state.tasks, state.agent, state.task, "pause")
        return tool_answer()

    state.worker.providers.transport = httpx.MockTransport(pause_then_respond)
    assert state.work() and state.detail().task.status == "paused"
    assert not state.work() and state.starts() == 0
    history = ProviderService(state.database).load_history(
        state.agent.id, state.task.conversation_id
    )
    ChatRequest(messages=history)
    assert (
        json.loads(next(item.content for item in history if item.role == "tool"))["status"]
        == "not_started"
    )


def test_latest_tool_block_cannot_be_silently_pruned_before_another_paid_call(state, monkeypatch):
    assert state.work() and state.work()
    state.approve()
    assert state.work() and state.starts() == 1
    monkeypatch.setattr(ProviderService, "HISTORY_MAX_MESSAGES", 1)
    assert state.work()
    detail = state.detail()
    assert detail.task.status == "paused"
    assert detail.runs[-1].error == "tool_result_too_large"
    assert detail.runs[-1].checkpoint["partial"] is True
    assert len(state.requests) == 1 and state.starts() == 1


def test_model_approval_does_not_authorize_proposed_tool(state):
    from bees_core.policies import PolicyService

    PolicyService(state.database).create(
        state.agent.id,
        {
            "name": "Perguntar geração",
            "effect": "ask",
            "scope": {"tool_name": "model", "action": "generate"},
        },
    )
    assert state.work()
    model_approval = state.worker.approvals.list(state.agent.id, task_id=state.task.id)[0]
    assert state.worker.resources.list(state.agent.id, task_id=state.task.id) == []
    state.worker.approvals.decide(
        state.agent.id,
        model_approval.id,
        {"client_request_id": uuid4(), "expected_revision": 1, "decision": "allow_once"},
    )
    assert state.work() and state.work()
    assert state.detail().task.status == "waiting_approval"
    assert state.starts() == 0 and len(state.requests) == 1
    assert len(state.worker.resources.list(state.agent.id, task_id=state.task.id)) == 1


def test_default_queue_worker_routes_the_explicit_task_to_its_canonical_resource(state):
    previous = state.worker
    state.worker = ToolQueueWorker(state.database, transport=previous.providers.transport)
    assert state.work() and state.work()
    approval = previous.resources.list(state.agent.id, task_id=state.task.id)[0]
    previous.resources.decide(
        state.agent.id,
        approval.id,
        {"client_request_id": uuid4(), "expected_revision": 1, "decision": "allow_once"},
    )
    assert state.work() and state.work()
    assert state.detail().task.status == "completed" and state.starts() == 1
    assert initialize_resource(state.database, state.agent.id).identity == state.resource.identity
    with state.store.transaction(write=False) as unit:
        assert (
            unit.agents._context.connection.execute("SELECT count(*) FROM host_link_invites").get
            == 0
        )
        assert unit.agents._context.connection.execute("SELECT count(*) FROM host_links").get == 0


def test_revoked_grant_during_ollama_preflight_prevents_paid_generation(state):
    command(state.tasks, state.agent, state.task, "cancel")
    with state.store.transaction() as unit:
        agent = unit.agents.get(state.agent.id)
        config = ProviderConfig(
            kind="ollama",
            endpoint="http://127.0.0.1:11434",
            model="fixture-tools",
            capabilities={"text": True, "tool_calls": True},
        )
        unit.agents.update(
            agent.model_copy(update={"provider_config": config.model_dump(mode="json")}),
            agent.revision,
        )
    state.task = state.tasks.create(
        state.agent.id,
        ToolTaskInput(client_request_id=uuid4(), title="Preflight", objective="Conferir"),
    )
    paths = []

    def respond(request):
        paths.append(request.url.path)
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "fixture-tools:latest"}]})
        if request.url.path == "/api/show":
            state.tools.set_grant(
                state.agent.id,
                state.plugin.id,
                "text.normalize",
                {"expected_revision": 1, "enabled": False},
            )
            return httpx.Response(200, json={"capabilities": ["completion", "tools"]})
        pytest.fail("Geração paga não deveria começar")

    state.worker.providers.transport = httpx.MockTransport(respond)
    assert state.work()
    assert paths == ["/api/tags", "/api/show"]
    assert state.detail().task.calls_started == 0 and state.starts() == 0
    assert state.detail().task.status == "paused"


def test_missing_database_is_not_created_by_resource_preparation(tmp_path):
    path = tmp_path / "missing.sqlite3"
    with pytest.raises(TransportError):
        initialize_resource(Database(path), uuid4())
    assert not path.exists()


def test_four_generation_ceiling_stops_a_repeated_rejected_proposal(state):
    attempts = []

    def respond(request):
        attempts.append(request)
        return tool_answer([(f"call_{len(attempts)}", "unknown_tool", {})])

    state.worker.providers.transport = httpx.MockTransport(respond)
    for _ in range(4):
        assert state.work() and state.work()
    detail = state.detail()
    assert detail.task.status == "paused" and detail.task.calls_started == 4
    assert (
        detail.runs[-1].error == "tool_cycle_limit"
        and detail.runs[-1].checkpoint["partial"] is True
    )
    assert not state.work() and len(attempts) == 4 and state.starts() == 0
    ChatRequest(
        messages=ProviderService(state.database).load_history(
            state.agent.id, state.task.conversation_id
        )
    )


def test_failed_tool_result_commit_cannot_repeat_confirmed_effect_or_paid_proposal(state):
    from datetime import timedelta

    from bees_core.models import utc_now
    from bees_core.storage.store import IntegrityError

    assert state.work() and state.work()
    state.approve()
    with state.database.transaction() as connection:
        connection.execute(
            "CREATE TRIGGER test_result_commit BEFORE INSERT ON messages WHEN NEW.role='tool' "
            "BEGIN SELECT RAISE(ABORT,'controlled result failure'); END"
        )
    with pytest.raises(IntegrityError):
        state.work()
    assert state.starts() == 1 and len(state.requests) == 1
    assert not state.work()
    with state.database.transaction() as connection:
        connection.execute("DROP TRIGGER test_result_commit")
    with state.store.transaction() as unit:
        unit.execution.recover_expired(now=utc_now() + timedelta(seconds=301))
    # Modelo e efeito já estão confirmados no core; só a projeção local faltou.
    assert state.detail().task.status == "queued"
    assert state.work() and state.starts() == 1 and len(state.requests) == 1
    ChatRequest(
        messages=ProviderService(state.database).load_history(
            state.agent.id, state.task.conversation_id
        )
    )
    assert state.work() and state.detail().task.status == "completed"
    assert state.starts() == 1 and len(state.requests) == 2
