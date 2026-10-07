"""Delegação autenticada: perda da confirmação, isolamento e observação sem efeitos."""

import asyncio
import json
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from bees_api.app import create_app
from bees_api.config import Settings
from bees_core.execution import TaskWorker
from bees_core.models import Agent, Conversation
from bees_core.policies import PolicyInput, PolicyScope, PolicyService
from bees_core.providers.contracts import ProviderConfig

ORIGIN = "http://127.0.0.1:8000"
PASSWORD = "senha teste delegacao 123456"


def headers(client):
    return {"Origin": ORIGIN, "X-Bees-CSRF": client.get("/api/v1/auth/status").json()["csrf_token"]}


@pytest.fixture
def state(tmp_path):
    settings = Settings(data_dir=tmp_path / "state", web_dist=tmp_path / "missing")
    with TestClient(create_app(settings), base_url=ORIGIN, raise_server_exceptions=False) as client:
        assert (
            client.post(
                "/api/v1/auth/setup",
                json={
                    "bootstrap_token": client.app.state.identity.issue_bootstrap(),
                    "name": "Teste",
                    "password": PASSWORD,
                },
                headers={"Origin": ORIGIN},
            ).status_code
            == 200
        )
        config = ProviderConfig(
            kind="openai_compatible",
            endpoint="https://fixture.invalid/v1",
            model="fixture-tools",
            capabilities={"text": True, "tool_calls": True},
        )
        agent = Agent(name="Teste", provider_config=config.model_dump(mode="json"))
        other = Agent(name="Outra", provider_config=config.model_dump(mode="json"))
        conversation = Conversation(agent_id=agent.id)
        with client.app.state.store.transaction() as unit:
            unit.agents.create(agent)
            unit.agents.create(other)
            unit.conversations.create(conversation)
        requests = []

        def answer(request):
            body = json.loads(request.content)
            requests.append(body)
            if body.get("tools"):
                message = {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call-" + uuid4().hex,
                            "type": "function",
                            "function": {
                                "name": "create_text_task",
                                "arguments": json.dumps(
                                    {
                                        "title": "Redação sobre cultura no Brasil",
                                        "objective": "Argumente sobre acesso à cultura.",
                                        "expected_result": "Redação completa.",
                                    }
                                ),
                            },
                        }
                    ],
                }
                finish = "tool_calls"
            else:
                message = {"role": "assistant", "content": "Resposta falsa de teste sobre cultura."}
                finish = "stop"
            return httpx.Response(
                200, json={"choices": [{"message": message, "finish_reason": finish}]}
            )

        transport = httpx.MockTransport(answer)
        client.app.state.providers.transport = transport
        yield client, settings, agent, other, conversation, requests, transport


def submission(conversation):
    return {
        "conversation_id": str(conversation.id),
        "content": "Prepare a redação em segundo plano.",
        "allow_delegation": True,
        "client_request_id": str(uuid4()),
    }


def test_committed_response_loss_replay_and_restart_never_generate_again(state, monkeypatch):
    client, settings, agent, _, conversation, requests, transport = state
    path = f"/api/v1/agents/{agent.id}"
    body = submission(conversation)
    import bees_api.delegation as projection

    original = projection.chat_result

    def lost(*args):
        raise RuntimeError("Falha controlada após commit")

    monkeypatch.setattr(projection, "chat_result", lost)
    assert client.post(path + "/chat", json=body, headers=headers(client)).status_code == 500
    monkeypatch.setattr(projection, "chat_result", original)
    recovered = client.get(
        path + "/delegations", params={"conversation_id": conversation.id}
    ).json()
    assert len(recovered["delegations"]) == 1
    task_id = recovered["delegations"][0]["id"]
    replay = client.post(path + "/chat", json=body, headers=headers(client))
    assert replay.status_code == 200 and replay.json()["delegations"][0]["id"] == task_id
    assert len(requests) == 1
    with TestClient(create_app(settings), base_url=ORIGIN) as restarted:
        restarted.app.state.providers.transport = transport
        assert (
            restarted.post(
                "/api/v1/auth/login", json={"password": PASSWORD}, headers={"Origin": ORIGIN}
            ).status_code
            == 200
        )
        replay = restarted.post(path + "/chat", json=body, headers=headers(restarted))
        assert replay.status_code == 200 and replay.json()["delegations"][0]["id"] == task_id
    assert len(requests) == 1


def test_projection_worker_result_readonly_and_origin_isolation(state):
    client, _, agent, other, conversation, requests, transport = state
    path = f"/api/v1/agents/{agent.id}"
    response = client.post(path + "/chat", json=submission(conversation), headers=headers(client))
    assert response.status_code == 200, response.text
    task = response.json()["delegations"][0]["task"]
    worker = TaskWorker(client.app.state.database, transport=transport)
    assert asyncio.run(worker.run_once())
    with client.app.state.store.transaction(write=False) as unit:
        revision = unit.conversations.get(conversation.id).revision
    for _ in range(3):
        result = client.get(
            path + "/delegations", params={"conversation_id": conversation.id}
        ).json()
        assert (
            result["delegations"][0]["task"]["latest_run"]["result"]["content"]
            == "Resposta falsa de teste sobre cultura."
        )
    assert len(requests) == 2
    with client.app.state.store.transaction(write=False) as unit:
        assert unit.conversations.get(conversation.id).revision == revision
        assert unit.tasks.get(UUID(task["id"])).calls_started == 1
    assert (
        client.get(
            f"/api/v1/agents/{other.id}/delegations", params={"conversation_id": conversation.id}
        ).status_code
        == 404
    )
    another = Conversation(agent_id=agent.id)
    with client.app.state.store.transaction() as unit:
        unit.conversations.create(another)
    assert (
        client.get(path + "/delegations", params={"conversation_id": another.id}).json()[
            "delegations"
        ]
        == []
    )
    assert (
        client.get(
            path + "/delegations", params={"conversation_id": task["conversation_id"]}
        ).status_code
        == 422
    )
    assert (
        client.post(
            path + "/chat",
            json=submission(conversation) | {"conversation_id": task["conversation_id"]},
            headers=headers(client),
        ).status_code
        == 422
    )
    assert len(requests) == 2


@pytest.mark.parametrize("effect,status", [("ask", "waiting_approval"), ("deny", "paused")])
def test_worker_policy_still_applies_to_delegated_task(state, effect, status):
    client, _, agent, _, conversation, requests, transport = state
    path = f"/api/v1/agents/{agent.id}"
    assert (
        client.post(
            path + "/chat", json=submission(conversation), headers=headers(client)
        ).status_code
        == 200
    )
    PolicyService(client.app.state.database).create(
        agent.id,
        PolicyInput(
            name="Regra posterior",
            effect=effect,
            scope=PolicyScope(tool_name="model", action="generate"),
        ),
        client_request_id=uuid4(),
    )
    assert asyncio.run(TaskWorker(client.app.state.database, transport=transport).run_once())
    task = client.get(path + "/delegations", params={"conversation_id": conversation.id}).json()[
        "delegations"
    ][0]["task"]
    assert task["status"] == status and task["latest_run"]["result"] is None
    assert len(requests) == 1


@pytest.mark.parametrize("flag", [1, "true", None])
def test_strict_human_flag_no_coercion(state, flag):
    client, _, agent, _, conversation, requests, _ = state
    assert (
        client.post(
            f"/api/v1/agents/{agent.id}/chat",
            json=submission(conversation) | {"allow_delegation": flag},
            headers=headers(client),
        ).status_code
        == 422
    )
    assert requests == []


def test_csrf_session_and_query_bounds_before_effect(state):
    client, _, agent, _, conversation, requests, _ = state
    path = f"/api/v1/agents/{agent.id}"
    assert (
        client.post(
            path + "/chat", json=submission(conversation), headers={"Origin": ORIGIN}
        ).status_code
        == 403
    )
    assert (
        client.get(
            path + "/delegations", params={"conversation_id": conversation.id, "offset": -1}
        ).status_code
        == 422
    )
    client.cookies.clear()
    assert (
        client.get(path + "/delegations", params={"conversation_id": conversation.id}).status_code
        == 401
    )
    assert (
        client.post(
            path + "/chat", json=submission(conversation), headers={"Origin": ORIGIN}
        ).status_code
        == 401
    )
    assert requests == []
