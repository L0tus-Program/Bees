"""Fluxos reais de persistência com transporte HTTP inteiramente de teste."""

import asyncio
import json
from datetime import timedelta

import httpx
import pytest
from pydantic import ValidationError

from bees_core.models import (
    Action,
    Agent,
    Approval,
    Artifact,
    Conversation,
    Memory,
    Message,
    Policy,
    Routine,
    Run,
    Task,
    utc_now,
)
from bees_core.providers.contracts import (
    CapabilityRequirements,
    ChatMessage,
    ProviderCapabilities,
    ProviderConfig,
    ToolCall,
    ToolDefinition,
)
from bees_core.providers.errors import ProviderError
from bees_core.providers.secrets import EnvSecretResolver
from bees_core.providers.service import ProviderService
from bees_core.storage.database import Database
from bees_core.storage.store import RevisionConflict, StateStore

TEST_SECRET = "private-provider-test-key"


def remote_config(*, tool_calls=False) -> ProviderConfig:
    return ProviderConfig(
        kind="openai_compatible",
        endpoint="https://provider.test/v1",
        model="remote-test-model",
        secret_ref="env:BEES_TEST_MODEL_KEY",
        capabilities=ProviderCapabilities(tool_calls=tool_calls),
    )


def local_config(*, tool_calls=False) -> ProviderConfig:
    return ProviderConfig(
        kind="ollama",
        endpoint="http://127.0.0.1:11434",
        model="local-test-model",
        capabilities=ProviderCapabilities(tool_calls=tool_calls),
    )


def fixture_state(tmp_path, *, config=None):
    database = Database(tmp_path / "bees.sqlite3")
    database.initialize()
    store = StateStore(database)
    agent = Agent(
        name="Abelha", provider_config=(config or remote_config()).model_dump(mode="json")
    )
    conversation = Conversation(agent_id=agent.id)
    with store.transaction() as uow:
        uow.agents.create(agent)
        uow.conversations.create(conversation)
    return database, store, agent, conversation


def resolver() -> EnvSecretResolver:
    return EnvSecretResolver({"BEES_TEST_MODEL_KEY": TEST_SECRET})


def response(request: httpx.Request, content="Resposta de teste") -> httpx.Response:
    if request.url.path.endswith("/api/tags"):
        return httpx.Response(200, json={"models": [{"name": "local-test-model:latest"}]})
    if request.url.path.endswith("/api/show"):
        return httpx.Response(200, json={"capabilities": ["completion", "tools"]})
    if request.url.path.endswith("/api/chat"):
        return httpx.Response(
            200,
            json={
                "model": "local-test-model",
                "message": {"role": "assistant", "content": content},
                "done": True,
                "done_reason": "stop",
                "prompt_eval_count": 3,
                "eval_count": 2,
            },
        )
    return httpx.Response(
        200,
        json={
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": content},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
        },
    )


def service(database, handler=None, *, secrets=None) -> ProviderService:
    return ProviderService(
        database,
        resolver=resolver() if secrets is None else secrets,
        transport=httpx.MockTransport(response if handler is None else handler),
    )


def test_switch_provider_preserves_full_graph_and_survives_restart(tmp_path) -> None:
    database, store, agent, conversation = fixture_state(tmp_path)
    routine = Routine(agent_id=agent.id, name="Agenda", instructions="Pesquisar", schedule={})
    task = Task(
        agent_id=agent.id,
        conversation_id=conversation.id,
        routine_id=routine.id,
        title="Pesquisa",
        objective="Pesquisar",
    )
    run = Run(task_id=task.id)
    action = Action(run_id=run.id, tool_name="read")
    policy = Policy(agent_id=agent.id, name="Leitura", effect="allow")
    approval = Approval(action_id=action.id, policy_id=policy.id)
    memory = Memory(agent_id=agent.id, scope="agent", content="Preferência")
    artifact = Artifact(task_id=task.id, run_id=run.id, name="Relatório")
    message = Message(conversation_id=conversation.id, role="user", content="Histórico")
    records = {
        "routines": routine,
        "tasks": task,
        "runs": run,
        "actions": action,
        "policies": policy,
        "approvals": approval,
        "memories": memory,
        "artifacts": artifact,
        "messages": message,
    }
    with store.transaction() as uow:
        for repository, record in records.items():
            getattr(uow, repository).create(record)
    updated = service(database).set_config(
        agent.id, local_config(), expected_revision=agent.revision
    )
    assert updated.id == agent.id
    assert updated.revision == 2
    assert updated.provider_config["kind"] == "ollama"
    assert updated.created_at == agent.created_at
    reopened = Database(database.path)
    reopened.initialize()
    with StateStore(reopened).transaction(write=False) as uow:
        assert uow.agents.get(agent.id) == updated
        assert uow.conversations.get(conversation.id) == conversation
        for repository, record in records.items():
            assert getattr(uow, repository).get(record.id) == record
    assert service(reopened).load_history(agent.id, conversation.id)[0].content == "Histórico"


def test_config_switch_rejects_stale_revision(tmp_path) -> None:
    database, _, agent, _ = fixture_state(tmp_path)
    provider = service(database)
    provider.set_config(agent.id, local_config(), expected_revision=1)
    with pytest.raises(RevisionConflict):
        provider.set_config(agent.id, remote_config(), expected_revision=1)


def test_explicit_capability_requirement_refuses_switch_without_mutation(tmp_path) -> None:
    database, store, agent, _ = fixture_state(tmp_path)
    with pytest.raises(ProviderError) as error:
        service(database).set_config(
            agent.id,
            local_config(),
            expected_revision=1,
            requirements=CapabilityRequirements(tool_calls=True),
        )
    assert error.value.code == "unsupported_capability"
    with store.transaction(write=False) as uow:
        assert uow.agents.get(agent.id) == agent


def test_historical_tool_calls_block_incompatible_switch(tmp_path) -> None:
    database, store, agent, conversation = fixture_state(
        tmp_path, config=remote_config(tool_calls=True)
    )
    normalized = ChatMessage(
        role="assistant", tool_calls=[ToolCall(id="call_1", name="read", arguments={})]
    )
    with store.transaction() as uow:
        uow.messages.create(
            Message(
                conversation_id=conversation.id,
                role="assistant",
                content="",
                metadata={"provider_message": normalized.model_dump(mode="json")},
            )
        )
    with pytest.raises(ProviderError) as error:
        service(database).set_config(agent.id, local_config(), expected_revision=1)
    assert error.value.code == "unsupported_capability"
    with store.transaction(write=False) as uow:
        assert uow.agents.get(agent.id) == agent
    # Capability compatível aceita histórico com call pendente sem exigir execução.
    assert (
        service(database)
        .set_config(agent.id, local_config(tool_calls=True), expected_revision=1)
        .provider_config["kind"]
        == "ollama"
    )


@pytest.mark.parametrize("field", ["api_key", "token", "authorization"])
def test_secret_values_cannot_be_stored_in_agent_config(tmp_path, field) -> None:
    database, store, agent, _ = fixture_state(tmp_path)
    invalid = remote_config().model_dump(mode="json") | {field: TEST_SECRET}
    with pytest.raises(ValidationError) as validation:
        Agent(name="Inválido", provider_config=invalid)
    assert TEST_SECRET not in str(validation.value)
    with pytest.raises(ProviderError) as result:
        service(database).set_config(agent.id, invalid, expected_revision=1)
    assert result.value.code == "invalid_config"
    assert TEST_SECRET not in str(result.value)
    # model_copy não pode contornar revalidação do repositório.
    with store.transaction() as uow:
        with pytest.raises(ValidationError):
            uow.agents.update(
                agent.model_copy(update={"provider_config": invalid}), expected_revision=1
            )
    with database.transaction(write=False) as connection:
        assert TEST_SECRET not in connection.execute("SELECT model_config_json FROM agents").get


def test_agent_config_creation_rejects_direct_secret_and_hides_endpoint_credentials() -> None:
    invalid = remote_config().model_dump(mode="json")
    invalid["endpoint"] = f"https://user:{TEST_SECRET}@provider.test"
    with pytest.raises(ValidationError) as result:
        Agent(name="Inválido", provider_config=invalid)
    assert TEST_SECRET not in str(result.value)


def test_agent_revalidates_provider_model_copy_before_persisting() -> None:
    invalid = remote_config().model_copy(
        update={"endpoint": f"https://user:{TEST_SECRET}@provider.test"}
    )
    with pytest.raises(ValidationError) as result:
        Agent(name="Inválido", provider_config=invalid)
    assert TEST_SECRET not in str(result.value)


@pytest.mark.parametrize("config", [remote_config(), local_config()])
def test_same_chat_flow_for_both_adapters_persists_normalized_state(tmp_path, config) -> None:
    database, store, agent, conversation = fixture_state(tmp_path, config=config)
    requests = []

    def handler(request):
        requests.append(request)
        return response(request)

    result = asyncio.run(service(database, handler).chat(agent.id, conversation.id, "Pesquisar"))
    assert result.message.content == "Resposta de teste"
    assert len(requests) == (3 if config.kind == "ollama" else 1)
    history = service(database).load_history(agent.id, conversation.id)
    assert [message.role for message in history] == ["user", "assistant"]
    with store.transaction(write=False) as uow:
        messages = uow.messages.list(conversation_id=conversation.id)
        assert messages[1].metadata["provider_response"]["usage"]["kind"] == "reported"
        assert messages[1].metadata["provider_kind"] == config.kind
        assert uow.conversations.get(conversation.id).revision == 3
    with database.transaction(write=False) as connection:
        assert TEST_SECRET not in connection.execute("SELECT model_config_json FROM agents").get
        assert all(
            TEST_SECRET not in row[0]
            for row in connection.execute(
                "SELECT metadata_json FROM messages UNION ALL "
                "SELECT payload_json FROM domain_events"
            )
        )


def test_provider_network_does_not_hold_sqlite_transaction(tmp_path) -> None:
    database, store, agent, conversation = fixture_state(tmp_path)

    def handler(request):
        # BEGIN IMMEDIATE em outra conexão prova ausência de escrita bloqueada.
        with store.transaction() as uow:
            uow.memories.create(Memory(agent_id=agent.id, scope="agent", content="Durante a rede"))
        return response(request)

    assert (
        asyncio.run(
            service(database, handler).chat(agent.id, conversation.id, "Pesquisar")
        ).message.content
        == "Resposta de teste"
    )


def test_provider_edit_while_response_pending_keeps_input_without_stale_response(tmp_path) -> None:
    database, store, agent, conversation = fixture_state(tmp_path)

    def handler(request):
        service(database).set_config(agent.id, local_config(), expected_revision=1)
        return response(request)

    with pytest.raises(ProviderError) as result:
        asyncio.run(service(database, handler).chat(agent.id, conversation.id, "Pesquisar"))
    assert result.value.code == "state_conflict"
    with store.transaction(write=False) as uow:
        assert uow.agents.get(agent.id).provider_config["kind"] == "ollama"
        messages = uow.messages.list(conversation_id=conversation.id)
        assert len(messages) == 1 and messages[0].role == "user"


def test_history_appended_outside_service_rejects_stale_response(tmp_path) -> None:
    database, store, agent, conversation = fixture_state(tmp_path)

    def handler(request):
        with store.transaction() as uow:
            uow.messages.create(
                Message(conversation_id=conversation.id, role="user", content="Nova instrução")
            )
        return response(request)

    with pytest.raises(ProviderError) as result:
        asyncio.run(service(database, handler).chat(agent.id, conversation.id, "Pesquisar"))
    assert result.value.code == "state_conflict"
    assert all(
        message.role == "user"
        for message in service(database).load_history(agent.id, conversation.id)
    )


def test_two_concurrent_chats_do_not_append_stale_assistant_turns(tmp_path) -> None:
    database, store, agent, conversation = fixture_state(tmp_path)

    async def run_both():
        arrived = asyncio.Event()
        requests = []

        async def handler(request):
            requests.append(request)
            if len(requests) == 2:
                arrived.set()
            await arrived.wait()
            content = json.loads(request.content)["messages"][-1]["content"]
            return response(request, f"Resposta para {content}")

        provider = service(database, handler)
        return await asyncio.gather(
            provider.chat(agent.id, conversation.id, "Primeira"),
            provider.chat(agent.id, conversation.id, "Segunda"),
            return_exceptions=True,
        )

    outcomes = asyncio.run(run_both())
    failures = [result for result in outcomes if isinstance(result, ProviderError)]
    assert len(failures) == 1 and failures[0].code == "state_conflict"
    with store.transaction(write=False) as uow:
        messages = uow.messages.list(conversation_id=conversation.id)
        assert [message.role for message in messages] == ["user", "user", "assistant"]
        assert messages[-1].content == "Resposta para Segunda"
        assert uow.conversations.get(conversation.id).revision == 4


def test_diagnose_keeps_private_agent_state_and_history_unchanged(tmp_path) -> None:
    database, store, agent, conversation = fixture_state(tmp_path)

    def handler(request):
        assert request.url.path == "/v1/models"
        assert request.headers["authorization"] == f"Bearer {TEST_SECRET}"
        return httpx.Response(200, json={"data": [{"id": "remote-test-model"}]})

    result = asyncio.run(service(database, handler).diagnose(agent.id))
    assert result.status == "ok"
    assert result.capabilities == remote_config().capabilities
    with store.transaction(write=False) as uow:
        assert uow.agents.get(agent.id) == agent
        assert uow.conversations.get(conversation.id) == conversation
        assert uow.messages.list(conversation_id=conversation.id) == []


def test_missing_secret_and_capability_fail_before_input_or_network(tmp_path) -> None:
    database, store, agent, conversation = fixture_state(tmp_path)
    requests = []

    def handler(request):
        requests.append(request)
        return response(request)

    with pytest.raises(ProviderError) as missing:
        asyncio.run(
            service(database, handler, secrets=EnvSecretResolver({})).chat(
                agent.id, conversation.id, "Pesquisar"
            )
        )
    assert missing.value.code == "secret_unavailable"
    with pytest.raises(ProviderError) as capability:
        asyncio.run(
            service(database, handler).chat(
                agent.id,
                conversation.id,
                "Pesquisar",
                requirements=CapabilityRequirements(tool_calls=True),
            )
        )
    assert capability.value.code == "unsupported_capability"
    assert requests == []
    with store.transaction(write=False) as uow:
        assert uow.messages.list(conversation_id=conversation.id) == []
        assert uow.conversations.get(conversation.id) == conversation


def test_provider_failure_keeps_input_without_fallback(tmp_path) -> None:
    database, _, agent, conversation = fixture_state(tmp_path)
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(429, json={"error": {"message": TEST_SECRET}})

    with pytest.raises(ProviderError) as result:
        asyncio.run(service(database, handler).chat(agent.id, conversation.id, "Pesquisar"))
    assert result.value.code == "rate_limited"
    assert TEST_SECRET not in str(result.value)
    assert len(requests) == 1
    assert len(service(database).load_history(agent.id, conversation.id)) == 1


def test_cross_agent_conversation_is_rejected_before_network(tmp_path) -> None:
    database, store, agent, conversation = fixture_state(tmp_path)
    other = Agent(name="Outra", provider_config=remote_config().model_dump(mode="json"))
    with store.transaction() as uow:
        uow.agents.create(other)
    with pytest.raises(ProviderError) as result:
        asyncio.run(service(database).chat(other.id, conversation.id, "Pesquisar"))
    assert result.value.code == "conversation_scope"
    assert service(database).load_history(agent.id, conversation.id) == []


def test_history_loader_does_not_silently_truncate_at_repository_page(tmp_path) -> None:
    database, store, agent, conversation = fixture_state(tmp_path)
    beginning = utc_now()
    with store.transaction() as uow:
        for index in range(1003):
            stamp = beginning + timedelta(microseconds=index)
            uow.messages.create(
                Message(
                    conversation_id=conversation.id,
                    role="user",
                    content=str(index),
                    created_at=stamp,
                    updated_at=stamp,
                )
            )
    history = service(database).load_history(agent.id, conversation.id)
    assert len(history) == 1003
    assert [message.content for message in history] == [str(index) for index in range(1003)]


def test_normalized_history_mismatch_is_rejected(tmp_path) -> None:
    database, store, agent, conversation = fixture_state(tmp_path)
    with store.transaction() as uow:
        uow.messages.create(
            Message(
                conversation_id=conversation.id,
                role="user",
                content="Canônico",
                metadata={"provider_message": {"role": "user", "content": "Alterado"}},
            )
        )
    with pytest.raises(ProviderError) as result:
        service(database).load_history(agent.id, conversation.id)
    assert result.value.code == "invalid_history"


def test_tool_call_roundtrip_persists_ids_without_executing_tools(tmp_path) -> None:
    database, _, agent, conversation = fixture_state(
        tmp_path, config=remote_config(tool_calls=True)
    )
    calls = []
    tools = [ToolDefinition(name="read", parameters={"type": "object", "properties": {}})]

    def handler(request):
        calls.append(request)
        if len(calls) == 1:
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
                                        "id": "call_1",
                                        "type": "function",
                                        "function": {"name": "read", "arguments": "{}"},
                                    }
                                ],
                            },
                            "finish_reason": "tool_calls",
                        }
                    ]
                },
            )
        return response(request, "Resultado revisado")

    provider = service(database, handler)
    initial = asyncio.run(provider.chat(agent.id, conversation.id, "Ler", tools=tools))
    assert initial.message.tool_calls[0].id == "call_1"
    result = asyncio.run(
        provider.chat(
            agent.id,
            conversation.id,
            ChatMessage(
                role="tool", content="Resultado fornecido pelo teste", tool_call_id="call_1"
            ),
            tools=tools,
        )
    )
    assert result.message.content == "Resultado revisado"
    history = provider.load_history(agent.id, conversation.id)
    assert [message.role for message in history] == ["user", "assistant", "tool", "assistant"]
    assert history[1].content is None
    assert history[2].tool_call_id == "call_1"


def multiple_tool_calls_fixture(request: httpx.Request) -> httpx.Response:
    calls = [
        {
            "id": f"call_{index}",
            "type": "function",
            "function": {"name": "read", "arguments": json.dumps({"index": index})},
        }
        for index in (1, 2)
    ]
    return httpx.Response(
        200,
        json={
            "choices": [
                {
                    "message": {"role": "assistant", "content": None, "tool_calls": calls},
                    "finish_reason": "tool_calls",
                }
            ]
        },
    )


def multiple_calls_state(tmp_path):
    database, store, agent, conversation = fixture_state(
        tmp_path, config=remote_config(tool_calls=True)
    )
    tools = [
        ToolDefinition(
            name="read",
            parameters={
                "type": "object",
                "properties": {"index": {"type": "integer"}},
                "required": ["index"],
                "additionalProperties": False,
            },
        )
    ]
    asyncio.run(
        service(database, multiple_tool_calls_fixture).chat(
            agent.id, conversation.id, "Ler duas entradas", tools=tools
        )
    )
    return database, store, agent, conversation, tools


@pytest.mark.parametrize("target", ["openai_compatible", "ollama"])
def test_multiple_tool_results_are_atomic_and_portable_between_adapters(tmp_path, target) -> None:
    database, store, agent, conversation, tools = multiple_calls_state(tmp_path)
    requests = []

    def handler(request):
        requests.append(request)
        return response(request, "Duas entradas revisadas")

    provider = service(database, handler)
    if target == "ollama":
        provider.set_config(agent.id, local_config(tool_calls=True), expected_revision=1)
    # Ordem Bees pode diferir da ordem nativa; associação é preservada pelos IDs.
    batch = [
        ChatMessage(role="tool", content="Segundo", tool_call_id="call_2"),
        ChatMessage(role="tool", content="Primeiro", tool_call_id="call_1"),
    ]
    result = asyncio.run(provider.chat(agent.id, conversation.id, batch, tools=tools))
    assert result.message.content == "Duas entradas revisadas"
    history = provider.load_history(agent.id, conversation.id)
    assert [message.role for message in history] == [
        "user",
        "assistant",
        "tool",
        "tool",
        "assistant",
    ]
    assert [message.tool_call_id for message in history[2:4]] == ["call_2", "call_1"]
    with store.transaction(write=False) as uow:
        assert uow.conversations.get(conversation.id).revision == 5
    chat_requests = [request for request in requests if request.url.path.endswith("/api/chat")]
    if target == "ollama":
        wire = json.loads(chat_requests[0].content)
        assert [message["content"] for message in wire["messages"][2:4]] == ["Primeiro", "Segundo"]


@pytest.mark.parametrize("problem", ["missing", "unknown", "duplicate", "user_in_batch", "empty"])
def test_invalid_tool_result_batch_is_rejected_before_persist_or_network(tmp_path, problem) -> None:
    database, store, agent, conversation, tools = multiple_calls_state(tmp_path)
    requests = []

    def handler(request):
        requests.append(request)
        return response(request)

    first = ChatMessage(role="tool", content="Primeiro", tool_call_id="call_1")
    second = ChatMessage(role="tool", content="Segundo", tool_call_id="call_2")
    batch = {
        "missing": [first],
        "unknown": [first, second.model_copy(update={"tool_call_id": "unknown"})],
        "duplicate": [first, first],
        "user_in_batch": [ChatMessage(role="user", content="Entrada")],
        "empty": [],
    }[problem]
    with pytest.raises(ProviderError) as result:
        asyncio.run(service(database, handler).chat(agent.id, conversation.id, batch, tools=tools))
    assert result.value.code == "invalid_request"
    assert requests == []
    with store.transaction(write=False) as uow:
        assert len(uow.messages.list(conversation_id=conversation.id)) == 2
        assert uow.conversations.get(conversation.id).revision == 3
