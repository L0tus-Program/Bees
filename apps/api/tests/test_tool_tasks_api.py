"""Modo explícito: API humana, fila padrão e processo próprio com modelo controlado."""

import asyncio
import json
import os
from datetime import UTC, datetime
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from test_global_stop_api import ORIGIN, headers
from test_global_stop_api import state as state

from bees_api.worker_status import HEARTBEAT_KEY
from bees_core.tool_runtime import ToolQueueWorker, initialize_resource
from bees_core.tools import ToolService


@pytest.fixture
def runtime(state):
    if os.name != "posix":
        pytest.skip("Recurso local de processo inicial exige POSIX.")
    client, agent, _ = state
    with client.app.state.store.transaction() as unit:
        current = unit.agents.get(agent.id)
        config = current.provider_config | {"capabilities": {"text": True, "tool_calls": True}}
        unit.agents.update(current.model_copy(update={"provider_config": config}), current.revision)
    client.app.state.store.put_cache(
        HEARTBEAT_KEY,
        {"last_seen": datetime.now(UTC).isoformat(), "tool_cycle_version": 1},
        ttl_seconds=20,
    )
    resource = initialize_resource(client.app.state.database, agent.id)
    tools = ToolService(client.app.state.database)
    plugin = tools.install({"client_request_id": uuid4(), "manifest": tools.catalog()[0]})
    tools.update_plugin(plugin.id, {"expected_revision": 1, "enabled": True})
    tools.set_grant(
        agent.id, plugin.id, "text.normalize", {"expected_revision": 0, "enabled": True}
    )
    return client, agent, resource, plugin


def value():
    return {
        "client_request_id": str(uuid4()),
        "title": "Transformar texto próprio",
        "objective": "Remover espaços de Olá e conferir o resultado",
        "expected_result": "Resultado conferido",
        "max_calls": 4,
        "max_active_seconds": 120,
        "deliverables": [],
    }


def test_probe_is_read_only_and_requires_a_compatible_live_worker(state):
    client, agent, _ = state
    endpoint = f"/api/v1/agents/{agent.id}/tool-task-capabilities"
    assert TestClient(client.app, base_url=ORIGIN).get(endpoint).status_code == 401
    response = client.get(endpoint).json()
    assert response["available"] is False and response["reason"] == "worker_unavailable"
    client.app.state.store.put_cache(
        HEARTBEAT_KEY, {"last_seen": datetime.now(UTC).isoformat()}, ttl_seconds=20
    )
    assert client.get(endpoint).json()["available"] is False
    assert not (client.app.state.database.path.parent / "text-resources").exists()


def test_closed_creation_receipt_and_replay_without_live_runtime(runtime):
    client, agent, _, _ = runtime
    endpoint = f"/api/v1/agents/{agent.id}/tool-tasks"
    body = value()
    assert (
        client.get(f"/api/v1/agents/{agent.id}/tool-task-capabilities").json()["available"] is True
    )
    assert client.post(endpoint, json=body).status_code in (401, 403)
    for extra in [
        {"resource_id": str(uuid4())},
        {"identity": "admin"},
        {"plugin_id": str(uuid4())},
        {"max_calls": 7},
        {"client_request_id": "00000000-0000-0000-0000-000000000000"},
    ]:
        assert client.post(endpoint, json=body | extra, headers=headers(client)).status_code == 422
    created = client.post(endpoint, json=body, headers=headers(client))
    assert created.status_code == 201
    result = created.json()
    assert (
        result["client_request_id"] == body["client_request_id"]
        and len(result["request_hash"]) == 64
    )
    assert (
        result["task"]["tool_approvals_available"] and result["task"]["model_approvals_available"]
    )
    client.app.state.store.put_cache(HEARTBEAT_KEY, {}, ttl_seconds=20)
    replay = client.post(endpoint, json=body, headers=headers(client)).json()
    assert replay == result
    assert (
        client.post(
            endpoint, json=body | {"objective": "Different"}, headers=headers(client)
        ).status_code
        == 409
    )
    assert (
        client.post(
            f"/api/v1/agents/{agent.id}/tasks", json=body, headers=headers(client)
        ).status_code
        == 409
    )
    # A conversa exclusiva da tarefa não é um chat avulso nem caminho sem orçamento.
    chat = client.post(
        f"/api/v1/agents/{agent.id}/chat",
        json={"conversation_id": result["task"]["conversation_id"], "content": "Bypass"},
        headers=headers(client),
    )
    assert chat.status_code in (409, 422)


def test_real_http_decision_and_default_worker_complete_one_tool_effect(runtime):
    client, agent, _, _ = runtime
    created = client.post(
        f"/api/v1/agents/{agent.id}/tool-tasks", json=value(), headers=headers(client)
    ).json()
    task_id = created["task"]["id"]
    requests = []

    def model(request):
        requests.append(json.loads(request.content))
        if len(requests) == 1:
            message = {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "normalize_text",
                            "arguments": json.dumps({"operation": "trim", "text": "  Olá  "}),
                        },
                    }
                ],
            }
            finish = "tool_calls"
        else:
            message = {"role": "assistant", "content": "Resultado conferido: Olá"}
            finish = "stop"
        return httpx.Response(
            200, json={"choices": [{"message": message, "finish_reason": finish}]}
        )

    worker = ToolQueueWorker(client.app.state.database, transport=httpx.MockTransport(model))
    assert asyncio.run(worker.run_once()) and asyncio.run(worker.run_once())
    collection = client.get(f"/api/v1/agents/{agent.id}/tool-approvals?task_id={task_id}").json()
    assert len(collection["approvals"]) == 1 and collection["approvals"][0]["input_preview"] is None
    approval = collection["approvals"][0]
    path = f"/api/v1/agents/{agent.id}/tool-approvals/{approval['id']}"
    assert (
        client.post(
            path + "/decision",
            json={
                "client_request_id": str(uuid4()),
                "expected_revision": 1,
                "decision": "allow_once",
            },
            headers=headers(client),
        ).status_code
        == 200
    )
    assert asyncio.run(worker.run_once()) and asyncio.run(worker.run_once())
    detail = client.get(f"/api/v1/agents/{agent.id}/tasks/{task_id}").json()
    assert detail["task"]["status"] == "completed"
    assert detail["task"]["latest_run"]["result"]["content"] == "Resultado conferido: Olá"
    assert client.get(path).json()["effect_status"] == "confirmed"
    assert len(requests) == 2 and not asyncio.run(worker.run_once())
    results = [
        json.loads(item["content"]) for item in requests[1]["messages"] if item["role"] == "tool"
    ]
    assert results[0]["output"] == {"text": "Olá"}
