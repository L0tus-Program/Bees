"""Delegação sem rede, isolamento entre abelhas, CAS e observação sem efeitos."""

import asyncio
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from bees_api.app import create_app
from bees_api.config import Settings
from bees_core.execution import TaskWorker
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
        first = Agent(name="Primeira", provider_config=config.model_dump(mode="json"))
        other = Agent(name="Outra", provider_config=config.model_dump(mode="json"))
        conversation = Conversation(agent_id=first.id)
        with client.app.state.store.transaction() as unit:
            unit.agents.create(first)
            unit.agents.create(other)
            unit.conversations.create(conversation)
        yield client, first, other, conversation, settings


def body():
    return {
        "client_request_id": str(uuid4()),
        "title": "Organizar ideias",
        "objective": "Preparar um plano com as informações fornecidas.",
        "expected_result": "Uma lista curta com próximos passos.",
    }


def test_create_idempotent_readonly_polling_and_restart(state):
    client, first, _, conversation, settings = state
    path = f"/api/v1/agents/{first.id}/tasks"
    value = body() | {"conversation_id": str(conversation.id)}
    created = client.post(path, json=value, headers=headers(client))
    assert created.status_code == 201, created.text
    task = created.json()
    assert task["status"] == "queued"
    assert task["conversation_id"] != str(conversation.id)
    assert task["latest_run"]["result"] is None
    assert client.post(path, json=value, headers=headers(client)).json()["id"] == task["id"]
    for _ in range(3):
        listed = client.get(path)
        assert listed.status_code == 200
        assert len(listed.json()["tasks"]) == 1
        assert listed.json()["worker"]["available"] is False
        detail = client.get(path + "/" + task["id"])
        assert detail.status_code == 200
        assert detail.json()["task"]["status"] == "queued"
    with client.app.state.store.transaction(write=False) as unit:
        assert unit.messages.list(conversation_id=conversation.id) == []
        assert unit.tasks.get(UUID(task["id"])).calls_started == 0
    with TestClient(create_app(settings), base_url=ORIGIN) as restarted:
        restarted.cookies.update(client.cookies)
        assert restarted.get(path + "/" + task["id"]).json()["task"]["id"] == task["id"]


def test_controls_cas_replay_redirect_and_cancel(state):
    client, first, _, _, _ = state
    path = f"/api/v1/agents/{first.id}/tasks"
    task = client.post(path, json=body(), headers=headers(client)).json()
    control = path + "/" + task["id"] + "/control"
    pause = {
        "client_request_id": str(uuid4()),
        "expected_revision": task["revision"],
        "action": "pause",
    }
    paused = client.post(control, json=pause, headers=headers(client))
    assert paused.status_code == 200, paused.text
    assert paused.json()["status"] == "paused"
    replay = client.post(control, json=pause, headers=headers(client))
    assert replay.status_code == 200
    assert replay.json()["revision"] == paused.json()["revision"]
    stale = pause | {"client_request_id": str(uuid4()), "action": "resume"}
    assert client.post(control, json=stale, headers=headers(client)).status_code == 409
    redirect = stale | {
        "expected_revision": paused.json()["revision"],
        "action": "redirect",
        "instruction": "Priorize os passos de hoje.",
    }
    changed = client.post(control, json=redirect, headers=headers(client))
    assert changed.status_code == 200, changed.text
    detail = client.get(path + "/" + task["id"]).json()
    assert any(event.get("content") == redirect["instruction"] for event in detail["events"])
    cancel = {
        "client_request_id": str(uuid4()),
        "expected_revision": changed.json()["revision"],
        "action": "cancel",
    }
    cancelled = client.post(control, json=cancel, headers=headers(client))
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"
    assert cancelled.json()["available_controls"] == []
    # A conversa exclusiva não é uma entrada alternativa para burlar o worker.
    bypass = client.post(
        f"/api/v1/agents/{first.id}/chat",
        json={"conversation_id": task["conversation_id"], "content": "Tente executar novamente."},
        headers=headers(client),
    )
    assert bypass.status_code in (409, 422)


def test_auth_csrf_ownership_validation_and_secret_projection(state):
    client, first, other, conversation, _ = state
    path = f"/api/v1/agents/{first.id}/tasks"
    assert client.post(path, json=body(), headers={"Origin": ORIGIN}).status_code == 403
    assert (
        client.post(path, json=body() | {"max_calls": 51}, headers=headers(client)).status_code
        == 422
    )
    wrong_conversation = f"/api/v1/agents/{other.id}/tasks"
    assert (
        client.post(
            wrong_conversation,
            json=body() | {"conversation_id": str(conversation.id)},
            headers=headers(client),
        ).status_code
        == 404
    )
    task = client.post(path, json=body(), headers=headers(client)).json()
    assert client.get(wrong_conversation + "/" + task["id"]).status_code == 404
    invalid = client.post(
        path + "/" + task["id"] + "/control",
        json={
            "client_request_id": str(uuid4()),
            "expected_revision": task["revision"],
            "action": "redirect",
        },
        headers=headers(client),
    )
    assert invalid.status_code == 422
    detail = client.get(path + "/" + task["id"])
    for private in ("provider_config", "secret_ref", "snapshot", "request_hash", "vault:"):
        assert private not in detail.text
    client.cookies.clear()
    assert client.get(path).status_code == 401


def test_worker_result_projection_does_not_pollute_chat(state):
    client, first, _, conversation, _ = state
    path = f"/api/v1/agents/{first.id}/tasks"
    task = client.post(path, json=body(), headers=headers(client)).json()

    def response(_):
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {"role": "assistant", "content": "Plano controlado."},
                        "finish_reason": "stop",
                    }
                ],
            },
        )

    worker = TaskWorker(client.app.state.database, transport=httpx.MockTransport(response))
    assert asyncio.run(worker.run_once()) is True
    detail = client.get(path + "/" + task["id"])
    assert detail.status_code == 200
    final = detail.json()["task"]
    assert final["status"] == "completed"
    assert final["latest_run"]["result"]["content"] == "Plano controlado."
    assert final["calls_started"] == 1
    assert final["available_controls"] == []
    with client.app.state.store.transaction(write=False) as unit:
        assert unit.messages.list(conversation_id=conversation.id) == []
