"""Parada global pela API: sessão, CSRF, UUID idempotente, CAS e bloqueio antes da rede."""

from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from bees_api.app import create_app
from bees_api.config import Settings
from bees_core.models import Agent, Conversation
from bees_core.providers.contracts import ProviderConfig

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
                json={"bootstrap_token": token, "name": "Teste", "password": "senha teste 123456"},
                headers={"Origin": ORIGIN},
            ).status_code
            == 200
        )
        config = ProviderConfig(
            kind="openai_compatible",
            endpoint="https://model.example/v1",
            model="test-model",
            capabilities={"text": True, "tool_calls": False},
        )
        agent = Agent(name="Primeira", provider_config=config.model_dump(mode="json"))
        conversation = Conversation(agent_id=agent.id)
        with client.app.state.store.transaction() as unit:
            unit.agents.create(agent)
            unit.conversations.create(conversation)
        yield client, agent, conversation


def command(client, kind, revision, request_id=None, **extra):
    return client.post(
        "/api/v1/safety/commands",
        json={
            "client_request_id": str(request_id or uuid4()),
            "kind": kind,
            "expected_revision": revision,
        }
        | extra,
        headers=headers(client),
    )


def test_state_requires_session_and_commands_require_csrf_uuid_and_cas(state):
    client, _, _ = state
    anonymous = TestClient(client.app, base_url=ORIGIN)
    assert anonymous.get("/api/v1/safety").status_code == 401
    current = client.get("/api/v1/safety").json()
    assert (current["status"], current["generation"], current["revision"]) == ("running", 0, 1)
    assert set(current["in_flight"]) == {
        "model_calls",
        "tool_actions",
        "chat_reservations",
        "provisioning_effects",
    }
    unprotected = client.post(
        "/api/v1/safety/commands",
        json={"client_request_id": str(uuid4()), "kind": "stop", "expected_revision": 1},
        headers={"Origin": ORIGIN},
    )
    assert unprotected.status_code in (401, 403)
    request_id = uuid4()
    stopped = command(client, "stop", 1, request_id)
    assert stopped.status_code == 200
    assert (stopped.json()["status"], stopped.json()["revision"]) == ("stopped", 2)
    replay = command(client, "stop", 1, request_id)
    assert replay.status_code == 200 and replay.json()["revision"] == 2
    assert command(client, "resume", 1, request_id).status_code == 409
    assert command(client, "resume", 1).status_code == 409
    assert command(client, "stop", 2).status_code == 409
    for invalid in ({"kind": "pause"}, {"expected_revision": "2"}, {"extra": True}):
        body = {"client_request_id": str(uuid4()), "kind": "resume", "expected_revision": 2}
        response = client.post(
            "/api/v1/safety/commands", json=body | invalid, headers=headers(client)
        )
        assert response.status_code == 422, invalid
    resumed = command(client, "resume", 2)
    assert (resumed.json()["status"], resumed.json()["generation"]) == ("running", 1)


def test_stopped_chat_returns_409_without_reaching_provider(state, monkeypatch):
    client, agent, conversation = state
    calls = []
    original = httpx.AsyncClient.send

    async def observed(self, request, *args, **kwargs):
        calls.append(request.url.host)
        return await original(self, request, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "send", observed)
    assert command(client, "stop", 1).status_code == 200
    response = client.post(
        f"/api/v1/agents/{agent.id}/chat",
        json={"conversation_id": str(conversation.id), "content": "Pergunta de teste"},
        headers=headers(client),
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "global_stop"
    assert "model.example" not in calls
