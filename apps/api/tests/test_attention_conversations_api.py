"""Escopo de conversa no HTTP humano; leitura não cria execução."""

from uuid import uuid4

from fastapi.testclient import TestClient
from test_attention_api import failed
from test_global_stop_api import ORIGIN, headers
from test_global_stop_api import state as state

from bees_core.models import Agent, Conversation


def test_catalog_and_filtered_get_keep_old_contract_scope_and_auth(state):
    client, agent, chat = state
    task = failed(client, agent)
    root = f"/api/v1/agents/{agent.id}/attention"
    anonymous = TestClient(client.app, base_url=ORIGIN)
    assert anonymous.get(root + "/conversations").status_code == 401
    assert anonymous.get(root + f"?conversation_id={task.conversation_id}").status_code == 401
    page = client.get(root + "/conversations?limit=1").json()
    assert page["agent_id"] == str(agent.id) and page["next_offset"] == 1
    assert set(page["items"][0]) == {"id", "title", "created_at"}
    assert client.get(root + "/conversations?limit=1&offset=1").json()["next_offset"] is None
    for query in ("limit=0", "offset=-1", "offset=true"):
        assert client.get(root + "/conversations?" + query).status_code == 422
    original = client.get(root).json()
    assert set(original) == {"items", "next_before", "unread_count"}
    scoped = client.get(root + f"?conversation_id={task.conversation_id}").json()
    assert scoped == original | {"conversation_id": str(task.conversation_id)}
    assert client.get(root + f"?conversation_id={chat.id}").json() == {
        "items": [],
        "next_before": None,
        "unread_count": 0,
        "conversation_id": str(chat.id),
    }
    assert client.get(root + "?conversation_id=invalid").status_code == 422
    assert task.objective not in str(scoped)
    with client.app.state.database.transaction(write=False) as connection:
        assert connection.execute("SELECT count(*) FROM attention_commands").get == 0
        assert connection.execute("SELECT count(*) FROM tasks").get == 1


def test_scoped_read_rejects_foreign_conversation_before_replay_and_keeps_csrf(state):
    client, agent, chat = state
    task = failed(client, agent)
    root = f"/api/v1/agents/{agent.id}/attention"
    item = client.get(root).json()["items"][0]
    path = root + f"/{item['id']}/read"
    body = {"client_request_id": str(uuid4()), "expected_revision": 1, "read": True}
    assert (
        client.put(path + f"?conversation_id={task.conversation_id}", json=body).status_code == 403
    )
    response = client.put(
        path + f"?conversation_id={task.conversation_id}", json=body, headers=headers(client)
    )
    assert response.status_code == 200 and response.json()["read"]
    assert (
        client.put(
            path + f"?conversation_id={chat.id}", json=body, headers=headers(client)
        ).status_code
        == 404
    )
    another = Agent(name="Outra")
    foreign = Conversation(agent_id=another.id)
    with client.app.state.store.transaction() as unit:
        unit.agents.create(another)
        unit.conversations.create(foreign)
    for rejected in (foreign.id, uuid4()):
        assert client.get(root + f"?conversation_id={rejected}").status_code == 404
        assert (
            client.put(
                path + f"?conversation_id={rejected}", json=body, headers=headers(client)
            ).status_code
            == 404
        )
    assert client.put(path, json=body, headers=headers(client)).json() == response.json()
    with client.app.state.database.transaction(write=False) as connection:
        assert connection.execute("SELECT count(*) FROM attention_commands").get == 1
