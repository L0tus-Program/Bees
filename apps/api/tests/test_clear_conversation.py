"""Limpar abre um novo contexto sem destruir registros ou esconder execução pendente."""

from uuid import UUID, uuid4

import pytest
from test_configuration import configured as configured

from bees_core.models import Agent, Conversation, Message, Task


def clear_path(agent, conversation):
    return f"/api/v1/agents/{agent.id}/conversations/{conversation.id}/clear"


def intent(agent, conversation):
    return {
        "client_request_id": str(uuid4()),
        "expected_revision": agent.revision,
        "expected_conversation_revision": conversation.revision,
        "consent": "clear_conversation",
    }


def test_clear_archives_history_preserves_memory_and_replays_after_restart(configured):
    client, agent, conversation, memory, config, headers, _ = configured
    body = intent(agent, conversation)
    result = client.post(clear_path(agent, conversation), json=body, headers=headers)
    assert result.status_code == 200, result.text
    new_id = UUID(result.json()["conversation_id"])
    assert new_id != conversation.id
    assert (
        client.get(
            f"/api/v1/agents/{agent.id}/messages", params={"conversation_id": str(new_id)}
        ).json()["messages"]
        == []
    )
    with client.app.state.store.transaction(write=False) as unit:
        assert unit.conversations.get(conversation.id).status == "archived"
        assert unit.messages.list(conversation_id=conversation.id)[0].content == "Histórico"
        assert unit.memories.get(memory.id) == memory
        assert unit.agents.get(agent.id).provider_config == config
        assert unit.agents.get(agent.id).revision == agent.revision
    summary = client.get("/api/v1/onboarding").json()["agents"][0]
    assert summary["conversation_id"] == str(new_id)
    # O replay depende de estado durável, não de um cache/comprovante do processo.
    from bees_api.onboarding import Receipts

    client.app.state.receipts = Receipts()
    assert (
        client.post(clear_path(agent, conversation), json=body, headers=headers).json()
        == result.json()
    )
    conflict = client.post(
        clear_path(agent, conversation), json=body | {"expected_revision": 999}, headers=headers
    )
    assert conflict.status_code == 409
    with client.app.state.store.transaction(write=False) as unit:
        assert len(unit.conversations.list(agent_id=agent.id)) == 2
    # Próximo pedido tem contexto vazio mesmo que o histórico antigo permaneça consultável.
    from bees_core.agent_tools.chat import submit_agent_message

    with client.app.state.store.transaction() as unit:
        current = unit.agents.get(agent.id)
        unit.agents.update(
            current.model_copy(
                update={
                    "provider_config": config | {"capabilities": {"text": True, "tool_calls": True}}
                }
            ),
            current.revision,
        )
        submitted = submit_agent_message(
            client.app.state.database, unit, agent.id, new_id, "Oi de novo", uuid4()
        )
        task = unit.tasks.get(submitted["task_id"])
        assert task.metadata["agent_chat"]["context"] == ""


@pytest.mark.parametrize("link", ["direct", "agent_chat", "delegation"])
@pytest.mark.parametrize(
    "status", ["queued", "running", "waiting_approval", "waiting_resource", "paused"]
)
def test_clear_blocks_pending_work_even_with_private_conversation(configured, link, status):
    client, agent, conversation, _, _, headers, _ = configured
    with client.app.state.store.transaction() as unit:
        private = unit.conversations.create(
            Conversation(agent_id=agent.id, metadata={"execution_kind": "text_task"})
        )
        metadata = (
            {}
            if link == "direct"
            else {
                link: {
                    "conversation_id" if link == "agent_chat" else "origin_conversation_id": str(
                        conversation.id
                    )
                }
            }
        )
        unit.tasks.create(
            Task(
                agent_id=agent.id,
                conversation_id=conversation.id if link == "direct" else private.id,
                title="Pendente",
                objective="Continuar",
                status=status,
                metadata=metadata,
            )
        )
    response = client.post(
        clear_path(agent, conversation), json=intent(agent, conversation), headers=headers
    )
    assert (
        response.status_code == 409
        and response.json()["error"]["code"] == "conversation_has_pending_work"
    )
    with client.app.state.store.transaction(write=False) as unit:
        assert unit.conversations.get(conversation.id).status == "active"


def test_clear_csrf_ownership_and_revision_guards(configured):
    client, agent, conversation, _, _, headers, _ = configured
    body = intent(agent, conversation)
    assert client.post(clear_path(agent, conversation), json=body).status_code == 403
    with client.app.state.store.transaction() as unit:
        other = unit.agents.create(Agent(name="Outra"))
        foreign = unit.conversations.create(Conversation(agent_id=other.id))
    assert client.post(clear_path(agent, foreign), json=body, headers=headers).status_code == 404
    assert (
        client.post(
            clear_path(agent, conversation), json=body | {"expected_revision": 999}, headers=headers
        ).status_code
        == 409
    )
    with client.app.state.store.transaction() as unit:
        unit.messages.create(
            Message(conversation_id=conversation.id, role="user", content="Outra aba")
        )
        unit.conversations.update(conversation, conversation.revision)
    assert (
        client.post(clear_path(agent, conversation), json=body, headers=headers).status_code == 409
    )
    with client.app.state.receipts.operation(conversation.id):
        assert (
            client.post(clear_path(agent, conversation), json=body, headers=headers).json()[
                "error"
            ]["code"]
            == "conversation_busy"
        )
    assert (
        client.post(
            clear_path(agent, conversation), json=body | {"consent": "anything"}, headers=headers
        ).status_code
        == 422
    )


def test_clear_keeps_finished_tasks_and_blocks_work_delegated_to_another_bee(configured):
    client, agent, conversation, _, _, headers, _ = configured
    with client.app.state.store.transaction() as unit:
        other = unit.agents.create(Agent(name="Outra"))
        delegated = unit.tasks.create(
            Task(
                agent_id=other.id,
                title="Delegada",
                objective="Concluir",
                metadata={
                    "delegated_from": {
                        "agent_id": str(agent.id),
                        "conversation_id": str(conversation.id),
                    }
                },
            )
        )
    body = intent(agent, conversation)
    assert (
        client.post(clear_path(agent, conversation), json=body, headers=headers).status_code == 409
    )
    with client.app.state.store.transaction() as unit:
        finished = unit.tasks.update(
            delegated.model_copy(update={"status": "cancelled"}), delegated.revision
        )
    assert (
        client.post(clear_path(agent, conversation), json=body, headers=headers).status_code == 200
    )
    with client.app.state.store.transaction(write=False) as unit:
        assert unit.tasks.get(finished.id) == finished
