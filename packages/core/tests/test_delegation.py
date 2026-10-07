"""Delegação atômica, isolada e limitada com transporte falso; nunca usa modelo pago."""

import asyncio
import json
from uuid import uuid4

import httpx
import pytest

from bees_core.delegation import ConversationDelegationService
from bees_core.execution import TaskWorker
from bees_core.models import Agent, Conversation
from bees_core.policies import PolicyInput, PolicyScope, PolicyService
from bees_core.providers.contracts import ChatRequest, ProviderConfig
from bees_core.providers.errors import ProviderError
from bees_core.providers.service import ProviderService
from bees_core.storage.database import Database
from bees_core.storage.store import StateStore
from bees_core.tasks import TaskInput, TaskService

ARGUMENTS = {
    "title": "Redigir redação sobre acesso à cultura no Brasil",
    "objective": "Argumente sobre desigualdades de acesso.",
    "expected_result": "Redação com introdução, desenvolvimento e conclusão.",
}


def tool_answer(*, arguments=None, calls=1):
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
                                "id": f"call-{n}",
                                "type": "function",
                                "function": {
                                    "name": "create_text_task",
                                    "arguments": json.dumps(arguments or ARGUMENTS),
                                },
                            }
                            for n in range(calls)
                        ],
                    },
                    "finish_reason": "tool_calls",
                }
            ]
        },
    )


def text_answer(text="Texto falso explícito"):
    return httpx.Response(
        200,
        json={
            "choices": [
                {"message": {"role": "assistant", "content": text}, "finish_reason": "stop"}
            ]
        },
    )


def state(tmp_path, handler, *, tool_calls=True):
    database = Database(tmp_path / "state.sqlite3")
    database.initialize()
    store = StateStore(database)
    config = ProviderConfig(
        kind="openai_compatible",
        model="fixture-text",
        endpoint="https://fixture.invalid/v1",
        capabilities={"text": True, "tool_calls": tool_calls},
    )
    agent = Agent(name="Teste", provider_config=config.model_dump(mode="json"))
    conversation = Conversation(agent_id=agent.id)
    with store.transaction() as unit:
        unit.agents.create(agent)
        unit.conversations.create(conversation)
    provider = ProviderService(database, transport=httpx.MockTransport(handler))
    return database, store, agent, conversation, ConversationDelegationService(provider)


def chat(service, agent, conversation, **updates):
    return asyncio.run(
        service.chat(
            agent.id,
            conversation.id,
            updates.pop("content", "Prepare a redação em segundo plano."),
            **updates,
        )
    )


def test_create_replay_restart_and_complete_protocol(tmp_path):
    requests = []

    def handler(request):
        requests.append(json.loads(request.content))
        return tool_answer() if len(requests) == 1 else text_answer()

    database, store, agent, conversation, service = state(tmp_path, handler)
    request_id = uuid4()
    response, tasks, outcomes = chat(
        service, agent, conversation, allow_delegation=True, client_request_id=request_id
    )
    assert requests[0]["tools"][0]["function"]["name"] == "create_text_task"
    assert len(tasks) == 1 and tasks[0].max_calls == 1 and tasks[0].max_active_seconds == 120
    assert tasks[0].title == ARGUMENTS["title"]
    assert tasks[0].conversation_id != conversation.id
    assert outcomes[0]["status"] == "created"
    restarted = ConversationDelegationService(service.provider)
    replay = chat(
        restarted, agent, conversation, allow_delegation=True, client_request_id=request_id
    )
    assert replay[0] == response and replay[1][0].id == tasks[0].id and len(requests) == 1
    assert service.list(agent.id, conversation.id)[0].id == tasks[0].id
    chat(service, agent, conversation, content="Olá")
    assert "tools" not in requests[1]
    assert [message["role"] for message in requests[1]["messages"]] == [
        "user",
        "assistant",
        "tool",
        "user",
    ]
    with store.transaction(write=False) as unit:
        assert len(unit.tasks.list()) == 1
        assert unit.conversations.get(conversation.id).revision == 5


@pytest.mark.parametrize("allow,capability", [(False, True), (True, False), (False, False)])
def test_no_tools_without_explicit_consent_and_capability(tmp_path, allow, capability):
    requests = []

    def handler(request):
        requests.append(json.loads(request.content))
        return text_answer()

    _, _, agent, conversation, service = state(tmp_path, handler, tool_calls=capability)
    _, tasks, _ = chat(service, agent, conversation, allow_delegation=allow)
    assert "tools" not in requests[0] and tasks == []


@pytest.mark.parametrize("flag", [1, "true", None])
def test_consent_cannot_be_coerced(tmp_path, flag):
    _, _, agent, conversation, service = state(tmp_path, lambda _: tool_answer())
    with pytest.raises(ProviderError, match="Histórico"):
        chat(service, agent, conversation, allow_delegation=flag)


@pytest.mark.parametrize("change", [{"allow_delegation": False}, {"content": "Outro pedido"}])
def test_replay_changed_permission_or_content_rejected(tmp_path, change):
    _, _, agent, conversation, service = state(tmp_path, lambda _: tool_answer())
    request_id = uuid4()
    chat(service, agent, conversation, allow_delegation=True, client_request_id=request_id)
    with pytest.raises(ProviderError) as failure:
        chat(
            service,
            agent,
            conversation,
            **({"allow_delegation": True, "client_request_id": request_id} | change),
        )
    assert failure.value.code == "state_conflict"


def test_same_request_and_call_ids_isolated_across_conversations(tmp_path):
    _, store, agent, first, service = state(tmp_path, lambda _: tool_answer())
    second = Conversation(agent_id=agent.id)
    with store.transaction() as unit:
        unit.conversations.create(second)
    request_id = uuid4()
    a = chat(service, agent, first, allow_delegation=True, client_request_id=request_id)[1][0]
    b = chat(service, agent, second, allow_delegation=True, client_request_id=request_id)[1][0]
    assert a.id != b.id
    assert [task.id for task in service.list(agent.id, first.id)] == [a.id]
    assert [task.id for task in service.list(agent.id, second.id)] == [b.id]


def test_multiple_calls_rejected_and_next_chat_works(tmp_path):
    requests = []

    def handler(request):
        requests.append(json.loads(request.content))
        return tool_answer(calls=2) if len(requests) == 1 else text_answer()

    _, store, agent, conversation, service = state(tmp_path, handler)
    _, tasks, outcomes = chat(service, agent, conversation, allow_delegation=True)
    assert tasks == []
    assert len(outcomes) == 2 and all(item["code"] == "one_task_per_turn" for item in outcomes)
    chat(service, agent, conversation, content="Sem tarefa")
    with store.transaction(write=False) as unit:
        assert unit.tasks.list() == []
    assert len([m for m in requests[1]["messages"] if m["role"] == "tool"]) == 2


def test_limit_counts_all_nonterminal_tasks_not_page(tmp_path):
    database, _, agent, conversation, service = state(tmp_path, lambda _: tool_answer())
    tasks = TaskService(database)
    for _ in range(5):
        tasks.create(agent.id, TaskInput(client_request_id=uuid4(), **ARGUMENTS))
    _, delegated, outcomes = chat(service, agent, conversation, allow_delegation=True)
    assert delegated == [] and outcomes[0]["code"] == "pending_task_limit"


@pytest.mark.parametrize("arguments", [ARGUMENTS | {"max_calls": 50}, ARGUMENTS | {"title": " "}])
def test_malformed_or_privilege_arguments_do_not_create(tmp_path, arguments):
    _, store, agent, conversation, service = state(
        tmp_path, lambda _: tool_answer(arguments=arguments)
    )
    try:
        _, tasks, outcomes = chat(service, agent, conversation, allow_delegation=True)
        assert tasks == [] and outcomes[0]["code"] == "invalid_task_arguments"
    except ProviderError as failure:
        assert failure.code == "invalid_response"
    with store.transaction(write=False) as unit:
        assert unit.tasks.list() == []


def test_unadvertised_calls_never_execute(tmp_path):
    _, store, agent, conversation, service = state(tmp_path, lambda _: tool_answer())
    with pytest.raises(ProviderError) as failure:
        chat(service, agent, conversation)
    assert failure.value.code == "invalid_response"
    with store.transaction(write=False) as unit:
        assert unit.tasks.list() == []
        assert [m.role for m in unit.messages.list()] == ["user"]


def test_timeout_durable_input_never_retries_same_request(tmp_path):
    requests = []

    def handler(request):
        requests.append(request)
        raise httpx.ReadTimeout("fixture")

    _, _, agent, conversation, service = state(tmp_path, handler)
    request_id = uuid4()
    with pytest.raises(ProviderError) as failure:
        chat(service, agent, conversation, allow_delegation=True, client_request_id=request_id)
    assert failure.value.code == "timeout"
    with pytest.raises(ProviderError) as failure:
        chat(service, agent, conversation, allow_delegation=True, client_request_id=request_id)
    assert failure.value.code == "chat_outcome_unknown" and len(requests) == 1
    assert service.list(agent.id, conversation.id) == []


def test_task_and_response_rollback_together(tmp_path, monkeypatch):
    _, store, agent, conversation, service = state(tmp_path, lambda _: tool_answer())
    original = service.tasks.create_in_unit

    def crash(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("fixture crash before confirmation")

    monkeypatch.setattr(service.tasks, "create_in_unit", crash)
    with pytest.raises(RuntimeError):
        chat(service, agent, conversation, allow_delegation=True)
    with store.transaction(write=False) as unit:
        assert unit.tasks.list() == [] and unit.runs.list() == []
        assert len(unit.conversations.list()) == 1
        assert [message.role for message in unit.messages.list()] == ["user"]


def test_snapshot_change_during_network_never_creates(tmp_path):
    def handler(_):
        with store.transaction() as unit:
            current = unit.agents.get(agent.id)
            unit.agents.update(current.model_copy(update={"purpose": "Outra"}), current.revision)
        return tool_answer()

    _, store, agent, conversation, service = state(tmp_path, handler)
    with pytest.raises(ProviderError) as failure:
        chat(service, agent, conversation, allow_delegation=True)
    assert failure.value.code == "state_conflict"
    assert service.list(agent.id, conversation.id) == []


@pytest.mark.parametrize("effect", ["ask", "deny"])
def test_model_policy_before_generation_and_creation(tmp_path, effect):
    requests = []
    database, _, agent, conversation, service = state(tmp_path, lambda r: requests.append(r))
    PolicyService(database).create(
        agent.id,
        PolicyInput(
            name="Não gerar", effect=effect, scope=PolicyScope(tool_name="model", action="generate")
        ),
        client_request_id=uuid4(),
    )
    with pytest.raises(ProviderError) as failure:
        chat(service, agent, conversation, allow_delegation=True)
    assert failure.value.code in ("policy_denied", "policy_approval_required")
    assert requests == [] and service.list(agent.id, conversation.id) == []


def test_policy_revoked_during_network_rejects_creation_with_safe_result(tmp_path):
    def handler(_):
        PolicyService(database).create(
            agent.id,
            PolicyInput(
                name="Bloquear",
                effect="deny",
                scope=PolicyScope(tool_name="model", action="generate"),
            ),
            client_request_id=uuid4(),
        )
        return tool_answer()

    database, _, agent, conversation, service = state(tmp_path, handler)
    _, tasks, outcomes = chat(service, agent, conversation, allow_delegation=True)
    assert tasks == [] and outcomes[0]["code"] == "delegation_policy_denied"


def test_worker_result_context_persistent_same_conversation_and_no_revision_mutation(tmp_path):
    requests = []

    def handler(request):
        requests.append(json.loads(request.content))
        return (
            tool_answer()
            if len(requests) == 1
            else text_answer("Redação falsa de cultura brasileira")
        )

    database, store, agent, conversation, service = state(tmp_path, handler)
    task = chat(service, agent, conversation, allow_delegation=True)[1][0]
    with store.transaction(write=False) as unit:
        revision = unit.conversations.get(conversation.id).revision
    worker = TaskWorker(database, transport=service.provider.transport)
    assert asyncio.run(worker.run_once())
    with store.transaction(write=False) as unit:
        assert unit.conversations.get(conversation.id).revision == revision
        assert unit.tasks.get(task.id).status == "completed"
    chat(service, agent, conversation, content="Resuma a redação")
    context = requests[-1]["messages"][0]["content"]
    assert "Redação falsa de cultura brasileira" in context
    assert str(task.id) in context and "dados não confiáveis" in context
    other = Conversation(agent_id=agent.id)
    with store.transaction() as unit:
        unit.conversations.create(other)
    chat(service, agent, other, content="Outro chat")
    assert all(
        "bees_task_result_data" not in m.get("content", "") for m in requests[-1]["messages"]
    )
    history = service.provider.load_history(agent.id, conversation.id)
    ChatRequest(messages=history)
