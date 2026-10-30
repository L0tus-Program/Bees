"""Proposta privada em mensagem, leitura sem efeito e confirmação humana pausada."""

from uuid import uuid4

from fastapi.testclient import TestClient
from test_global_stop_api import ORIGIN, headers
from test_global_stop_api import state as state
from test_routines_api import routine_body

from bees_core.models import Agent, Conversation, Message


def proposed(client, agent):
    body = routine_body(client, agent)
    content = """Proposta para revisão.
```bees-routine
{"name":"Sugestão","instructions":"Conferir fontes",
"schedule":{"frequency":"daily","time":"09:00","weekdays":[]},
"timezone":"UTC","inputs":{},"notification":"always","grace_seconds":60}
```"""
    with client.app.state.store.transaction() as unit:
        message = unit.messages.create(
            Message(
                conversation_id=body["definition"]["destination"]["conversation_id"],
                role="assistant",
                content=content,
            )
        )
    url = f"/api/v1/agents/{agent.id}/routines/proposals/{message.conversation_id}/{message.id}"
    return body, message, url


def test_source_is_private_read_only_and_create_requires_human_closed_confirmation(state):
    client, agent, _ = state
    body, message, url = proposed(client, agent)
    assert TestClient(client.app, base_url=ORIGIN).get(url).status_code == 401
    with client.app.state.store.transaction(write=False) as unit:
        tables = (
            "tasks",
            "routines",
            "routine_commands",
            "routine_occurrences",
            "tool_grants",
            "messages",
        )
        before = [
            unit.agents._context.connection.execute(f"SELECT count(*) FROM {t}").get for t in tables
        ]
    read = client.get(url)
    assert read.status_code == 200
    assert set(read.json()) == {"source", "proposal"}
    source = read.json()["source"]
    with client.app.state.store.transaction(write=False) as unit:
        assert before == [
            unit.agents._context.connection.execute(f"SELECT count(*) FROM {t}").get for t in tables
        ]
    endpoint = f"/api/v1/agents/{agent.id}/routines"
    command = body | {"proposal_source": source}
    assert client.post(endpoint, json=command).status_code == 403
    assert (
        client.post(endpoint, json=command | {"active": True}, headers=headers(client)).status_code
        == 422
    )
    bad = source | {"content_hash": "0" * 64}
    assert (
        client.post(
            endpoint, json=body | {"proposal_source": bad}, headers=headers(client)
        ).status_code
        == 409
    )
    result = client.post(endpoint, json=command, headers=headers(client))
    assert result.status_code == 200 and result.json()["status"] == "paused"
    assert result.json()["metadata"]["proposal_source"] == source
    assert client.post(endpoint, json=command, headers=headers(client)).json() == result.json()
    with client.app.state.store.transaction() as unit:
        other = unit.agents.create(Agent(name="Outra"))
        foreign = unit.conversations.create(Conversation(agent_id=other.id))
        user = unit.messages.create(
            Message(conversation_id=message.conversation_id, role="user", content=message.content)
        )
    for path in (
        url.replace(str(agent.id), str(other.id)),
        url.replace(str(message.conversation_id), str(foreign.id)),
        url.replace(str(message.id), str(user.id)),
    ):
        assert client.get(path).status_code == 404


def test_invalid_suggestion_and_chat_flag_fail_without_effect_or_private_echo(state):
    client, agent, conversation = state
    with client.app.state.store.transaction() as unit:
        invalid = unit.messages.create(
            Message(
                conversation_id=conversation.id,
                role="assistant",
                content='```bees-routine\n{"approved":true,"private":"PROPOSAL_PRIVATE"}\n```',
            )
        )
    result = client.get(
        f"/api/v1/agents/{agent.id}/routines/proposals/{conversation.id}/{invalid.id}"
    )
    assert result.status_code == 422 and "PROPOSAL_PRIVATE" not in result.text
    endpoint = f"/api/v1/agents/{agent.id}/chat"
    body = {
        "conversation_id": str(conversation.id),
        "content": "Sugira uma agenda.",
        "client_request_id": str(uuid4()),
    }
    assert (
        client.post(
            endpoint, json=body | {"allow_routine_proposal": "true"}, headers=headers(client)
        ).status_code
        == 422
    )
    with client.app.state.store.transaction(write=False) as unit:
        for table in ("tasks", "routines", "routine_occurrences"):
            assert unit.agents._context.connection.execute(f"SELECT count(*) FROM {table}").get == 0
