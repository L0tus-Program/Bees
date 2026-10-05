import json
from uuid import UUID

import httpx
import pytest
from fastapi.testclient import TestClient

from bees_api.app import create_app
from bees_api.config import Settings
from bees_core.models import Agent, Conversation, Task
from bees_core.providers.contracts import ProviderCapabilities, ProviderConfig

ORIGIN = "http://127.0.0.1:8000"


def headers(client):
    return {
        "Origin": ORIGIN,
        "X-Bees-CSRF": client.get("/api/v1/auth/status").json()["csrf_token"],
    }


@pytest.fixture
def state(tmp_path):
    settings = Settings(data_dir=tmp_path / "state", web_dist=tmp_path / "missing")
    with TestClient(create_app(settings), base_url=ORIGIN) as client:
        token = client.app.state.identity.issue_bootstrap()
        assert (
            client.post(
                "/api/v1/auth/setup",
                json={
                    "bootstrap_token": token,
                    "name": "Pessoa de teste",
                    "password": "senha de teste 123456",
                },
                headers={"Origin": ORIGIN},
            ).status_code
            == 200
        )
        config = ProviderConfig(
            kind="openai_compatible",
            endpoint="https://model.example/v1",
            model="test-model",
            capabilities=ProviderCapabilities(),
        )
        first = Agent(name="Principal", provider_config=config.model_dump(mode="json"))
        second = Agent(name="Outra", provider_config=config.model_dump(mode="json"))
        conversation = Conversation(agent_id=first.id)
        other_conversation = Conversation(agent_id=second.id)
        task = Task(
            agent_id=first.id,
            conversation_id=conversation.id,
            title="Tarefa principal",
            objective="Objetivo",
        )
        other_task = Task(
            agent_id=second.id,
            conversation_id=other_conversation.id,
            title="Outra tarefa",
            objective="Objetivo",
        )
        with client.app.state.store.transaction() as unit:
            for agent in (first, second):
                unit.agents.create(agent)
            for item in (conversation, other_conversation):
                unit.conversations.create(item)
            for item in (task, other_task):
                unit.tasks.create(item)
        yield client, first, second, conversation, task, other_task, settings


def create_memory(client, agent, **values):
    response = client.post(
        f"/api/v1/agents/{agent.id}/memories",
        json={"scope": "agent", "content": "Preferência de teste", "kind": "preference"} | values,
        headers=headers(client),
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_profile_cas_toggle_and_restart_preserve_profile_and_conversation(state):
    client, agent, _, conversation, _, _, settings = state
    body = {
        "expected_revision": 1,
        "name": " Abelha editada ",
        "purpose": "Pesquisa",
        "instructions": "Seja breve.",
        "memory_enabled": False,
    }
    response = client.post(f"/api/v1/agents/{agent.id}/profile", json=body, headers=headers(client))
    assert response.status_code == 200
    updated = response.json()
    assert updated["name"] == "Abelha editada"
    assert updated["revision"] == 2
    assert updated["memory_enabled"] is False
    assert updated["conversation_id"] == str(conversation.id)
    assert updated["provider_config"] == agent.provider_config
    assert (
        client.post(
            f"/api/v1/agents/{agent.id}/profile", json=body, headers=headers(client)
        ).status_code
        == 409
    )
    with TestClient(create_app(settings), base_url=ORIGIN) as restarted:
        restarted.cookies.update(client.cookies)
        records = restarted.get("/api/v1/onboarding").json()["agents"]
        stored = next(record for record in records if record["id"] == str(agent.id))
        assert stored["purpose"] == "Pesquisa"
        assert stored["instructions"] == "Seja breve."
        assert stored["memory_enabled"] is False


@pytest.mark.parametrize(
    "field,value", [("name", "   "), ("instructions", "x" * 16385), ("memory_enabled", "false")]
)
def test_invalid_profile_does_not_mutate_agent(state, field, value):
    client, agent, *_ = state
    body = {
        "expected_revision": 1,
        "name": "Principal",
        "purpose": "",
        "instructions": "",
        "memory_enabled": True,
    }
    response = client.post(
        f"/api/v1/agents/{agent.id}/profile", json=body | {field: value}, headers=headers(client)
    )
    assert response.status_code == 422
    with client.app.state.store.transaction(write=False) as unit:
        assert unit.agents.get(agent.id) == agent


def test_memory_listing_scopes_provenance_cas_update_and_delete(state):
    client, agent, other, _, task, _, _ = state
    user = create_memory(client, agent, scope="user", content="Preferência pessoal")
    own = create_memory(client, agent)
    task_memory = create_memory(
        client,
        agent,
        scope="task",
        task_id=str(task.id),
        kind="fact",
        content="Fato variável relevante",
        source_ref="Documento de teste, página 2",
        observed_at="2026-10-05T10:00:00-03:00",
    )
    other_memory = create_memory(client, other, content="Privado da outra abelha")
    listing = client.get(f"/api/v1/agents/{agent.id}/memories").json()
    assert {record["id"] for record in listing["memories"]} == {
        user["id"],
        own["id"],
        task_memory["id"],
    }
    assert listing["tasks"] == [{"id": str(task.id), "title": "Tarefa principal"}]
    assert listing["has_more"] is False
    facts = next(record for record in listing["memories"] if record["id"] == task_memory["id"])
    assert facts["kind"] == "fact"
    assert facts["source_ref"] == "Documento de teste, página 2"
    assert facts["observed_at"] == "2026-10-05T13:00:00Z"
    listing_other = client.get(f"/api/v1/agents/{other.id}/memories").json()
    assert {record["id"] for record in listing_other["memories"]} == {
        user["id"],
        other_memory["id"],
    }
    changed = client.post(
        f"/api/v1/agents/{agent.id}/memories/{own['id']}/update",
        json={"expected_revision": 1, "content": "Preferência editada", "kind": "preference"},
        headers=headers(client),
    )
    assert changed.status_code == 200
    assert changed.json()["revision"] == 2
    assert (
        client.post(
            f"/api/v1/agents/{agent.id}/memories/{own['id']}/delete",
            json={"expected_revision": 1},
            headers=headers(client),
        ).status_code
        == 409
    )
    assert (
        client.post(
            f"/api/v1/agents/{agent.id}/memories/{own['id']}/delete",
            json={"expected_revision": 2},
            headers=headers(client),
        ).status_code
        == 204
    )
    assert own["id"] not in {
        record["id"]
        for record in client.get(f"/api/v1/agents/{agent.id}/memories").json()["memories"]
    }


@pytest.mark.parametrize("mutation", ["update", "delete"])
def test_other_agent_memory_cannot_be_changed_through_wrong_url(state, mutation):
    client, agent, other, *_ = state
    memory = create_memory(client, other)
    body = {"expected_revision": 1}
    if mutation == "update":
        body |= {"content": "Alteração indevida", "kind": "preference"}
    response = client.post(
        f"/api/v1/agents/{agent.id}/memories/{memory['id']}/{mutation}",
        json=body,
        headers=headers(client),
    )
    assert response.status_code == 404
    with client.app.state.store.transaction(write=False) as unit:
        assert unit.memories.get(UUID(memory["id"])).revision == 1


def test_fact_without_source_or_date_and_cross_agent_task_are_rejected(state):
    client, agent, _, _, _, other_task, _ = state
    for fields in ({"kind": "fact"}, {"kind": "fact", "source_ref": "Source"}):
        response = client.post(
            f"/api/v1/agents/{agent.id}/memories",
            json={"scope": "agent", "content": "Fato sem evidência"} | fields,
            headers=headers(client),
        )
        assert response.status_code == 422
    response = client.post(
        f"/api/v1/agents/{agent.id}/memories",
        json={"scope": "task", "task_id": str(other_task.id), "content": "Task de outra abelha"},
        headers=headers(client),
    )
    assert response.status_code == 409
    assert client.get(f"/api/v1/agents/{agent.id}/memories").json()["memories"] == []


def test_fact_content_edit_preserves_provenance_and_explicit_removal_is_rejected(state):
    client, agent, *_ = state
    memory = create_memory(
        client,
        agent,
        kind="fact",
        source_ref="Documento de teste",
        observed_at="2026-10-05T12:00:00Z",
    )
    response = client.post(
        f"/api/v1/agents/{agent.id}/memories/{memory['id']}/update",
        json={"expected_revision": 1, "content": "Fato editado"},
        headers=headers(client),
    )
    assert response.status_code == 200
    assert response.json()["kind"] == "fact"
    assert response.json()["source_ref"] == memory["source_ref"]
    assert response.json()["observed_at"] == memory["observed_at"]
    response = client.post(
        f"/api/v1/agents/{agent.id}/memories/{memory['id']}/update",
        json={"expected_revision": 2, "content": "Inválido", "source_ref": None},
        headers=headers(client),
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_failed"
    with client.app.state.store.transaction(write=False) as unit:
        assert unit.memories.get(UUID(memory["id"])).revision == 2


def test_profile_and_memory_mutations_require_csrf_and_read_requires_auth(state):
    client, agent, *_ = state
    memory = create_memory(client, agent)
    bodies = {
        "profile": {
            "expected_revision": 1,
            "name": "Changed",
            "purpose": "",
            "instructions": "",
            "memory_enabled": True,
        },
        "memories": {"scope": "agent", "content": "Nova memória"},
        f"memories/{memory['id']}/update": {"expected_revision": 1, "content": "Edited"},
        f"memories/{memory['id']}/delete": {"expected_revision": 1},
    }
    for path, body in bodies.items():
        assert (
            client.post(
                f"/api/v1/agents/{agent.id}/{path}", json=body, headers={"Origin": ORIGIN}
            ).status_code
            == 403
        )
    client.cookies.clear()
    assert client.get(f"/api/v1/agents/{agent.id}/memories").status_code == 401


def test_profile_and_selected_memory_reach_model_as_separate_context(state):
    client, agent, _, conversation, task, _, _ = state
    create_memory(client, agent, content="Gosto de listas concisas")
    create_memory(
        client, agent, scope="task", task_id=str(task.id), content="Contexto exclusivo tarefa"
    )
    assert (
        client.post(
            f"/api/v1/agents/{agent.id}/profile",
            json={
                "expected_revision": 1,
                "name": agent.name,
                "purpose": "Pesquisa",
                "instructions": "Use português",
                "memory_enabled": True,
            },
            headers=headers(client),
        ).status_code
        == 200
    )
    captured = []

    def respond(request):
        captured.extend(json.loads(request.content)["messages"])
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {"role": "assistant", "content": "Resposta de teste"},
                        "finish_reason": "stop",
                    }
                ]
            },
        )

    client.app.state.providers.transport = httpx.MockTransport(respond)
    result = client.post(
        f"/api/v1/agents/{agent.id}/chat",
        json={
            "conversation_id": str(conversation.id),
            "content": "Organize a pesquisa",
            "task_id": str(task.id),
        },
        headers=headers(client),
    )
    assert result.status_code == 200, result.text
    assert captured[0]["role"] == "system"
    assert "Use português" in captured[0]["content"]
    assert captured[1]["role"] == "user"
    assert "Gosto de listas concisas" in captured[1]["content"]
    assert "Contexto exclusivo tarefa" in captured[1]["content"]
    history = client.get(
        f"/api/v1/agents/{agent.id}/messages", params={"conversation_id": str(conversation.id)}
    ).json()["messages"]
    assert len(history) == 2
    assert all("bees_memory_data" not in message["content"] for message in history)
