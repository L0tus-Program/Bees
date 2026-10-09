"""Limite de consumo pela API: sessão, CSRF, CAS e bloqueio antes da rede."""

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


def test_summary_and_limit_require_session_csrf_and_cas(state):
    client, agent, _ = state
    path = f"/api/v1/agents/{agent.id}/budget"
    anonymous = TestClient(client.app, base_url=ORIGIN)
    assert anonymous.get(path).status_code == 401
    empty = client.get(path).json()
    assert empty["limit"] is None and empty["counted_tokens"] == 0
    assert empty["remaining_tokens"] is None and empty["window_seconds"] == 86400
    assert client.put(path, json={"token_limit": 5000}, headers={"Origin": ORIGIN}).status_code in (
        401,
        403,
    )
    created = client.put(path, json={"token_limit": 5000}, headers=headers(client))
    assert created.status_code == 200
    limit = created.json()["limit"]
    assert (limit["token_limit"], limit["revision"], limit["status"]) == (5000, 1, "active")
    assert created.json()["remaining_tokens"] == 5000
    stale = client.put(
        path, json={"token_limit": 1, "expected_revision": 7}, headers=headers(client)
    )
    assert stale.status_code == 409
    again = client.put(path, json={"token_limit": 1}, headers=headers(client))
    assert again.status_code == 409
    updated = client.put(
        path,
        json={"token_limit": 9000, "expected_revision": 1, "status": "disabled"},
        headers=headers(client),
    ).json()
    assert updated["limit"]["revision"] == 2 and updated["remaining_tokens"] is None
    for invalid in (
        {"token_limit": 0},
        {"token_limit": "5000"},
        {"token_limit": 10, "window_seconds": 60},
        {"token_limit": 10, "status": "unlimited"},
        {"token_limit": 10, "extra": True},
    ):
        response = client.put(
            path, json=invalid | {"expected_revision": 2}, headers=headers(client)
        )
        assert response.status_code == 422, invalid
    assert client.get(f"/api/v1/agents/{uuid4()}/budget").status_code == 404


def test_exhausted_budget_returns_409_without_reaching_provider(state, monkeypatch):
    client, agent, conversation = state
    calls = []
    original = httpx.AsyncClient.send

    async def observed(self, request, *args, **kwargs):
        calls.append(request.url.host)
        return await original(self, request, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "send", observed)
    path = f"/api/v1/agents/{agent.id}/budget"
    assert client.put(path, json={"token_limit": 1}, headers=headers(client)).status_code == 200
    response = client.post(
        f"/api/v1/agents/{agent.id}/chat",
        json={"conversation_id": str(conversation.id), "content": "Pergunta de teste"},
        headers=headers(client),
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "budget_exhausted"
    assert "model.example" not in calls
    summary = client.get(path).json()
    assert summary["entries"] == 0 and summary["counted_tokens"] == 0
