"""Intenção manual fechada sob sessão, CAS, recibo privado e replay offline."""

from datetime import UTC, datetime
from uuid import uuid4

from fastapi.testclient import TestClient
from test_global_stop_api import ORIGIN, headers
from test_skill_tasks_api import prepared
from test_tool_tasks_api import runtime as runtime
from test_tool_tasks_api import state as state

from bees_api.worker_status import HEARTBEAT_KEY
from bees_core.executor_transport.contracts import TransportError
from bees_core.models import Conversation


def manual(runtime):
    client, agent, _, _, skill_input = prepared(runtime)
    with client.app.state.store.transaction() as unit:
        conversation = unit.conversations.create(Conversation(agent_id=agent.id))
    base = f"/api/v1/agents/{agent.id}/routines"
    routine = client.post(
        base,
        json={
            "client_request_id": str(uuid4()),
            "definition": {
                "name": "Manual de teste",
                "instructions": "Conferir a saída própria.",
                "schedule": {"frequency": "daily", "time": "09:00"},
                "timezone": "UTC",
                "skill_id": skill_input["skill_id"],
                "skill_version": 1,
                "inputs": skill_input["inputs"],
                "environment": "own",
                "destination": {"conversation_id": str(conversation.id)},
                "notification": "always",
            },
        },
        headers=headers(client),
    )
    assert routine.status_code == 200
    return client, agent, base + "/" + routine.json()["id"], routine.json()


def heartbeat(client, version=1):
    client.app.state.store.put_cache(
        HEARTBEAT_KEY,
        {
            "last_seen": datetime.now(UTC).isoformat(),
            "tool_cycle_version": 1,
            "routine_scheduler_version": 1,
            "routine_manual_version": version,
        },
        ttl_seconds=30,
    )


def test_manual_human_contract_closed_and_read_only_receipt(runtime):
    client, agent, path, original = manual(runtime)
    body = {"client_request_id": str(uuid4()), "expected_revision": 1}
    url = path + "/manual"
    anonymous = TestClient(client.app, base_url=ORIGIN)
    assert anonymous.get(url + "/" + body["client_request_id"]).status_code == 401
    assert anonymous.post(url, json=body).status_code in (401, 403)
    assert client.post(url, json=body).status_code == 403
    for extra in (
        {"runtime_binding": {}},
        {"approved": True},
        {"definition": {}},
        {"expected_revision": True},
        {"client_request_id": "00000000-0000-0000-0000-000000000000"},
    ):
        assert client.post(url, json=body | extra, headers=headers(client)).status_code == 422
    for hostile in ({"Origin": "https://evil.test"}, {"Host": "evil.test"}):
        assert client.post(url, json=body, headers=headers(client) | hostile).status_code in (
            400,
            403,
        )
    assert client.get(url + "/" + body["client_request_id"]).status_code == 404
    for version in (None, True, "1", 0, 1):
        heartbeat(client, version)
        probe = client.get(f"/api/v1/agents/{agent.id}/routines/runtime").json()
        valid = type(version) is int and version == 1
        assert probe["available"] is True and probe["manual_available"] is valid
        response = client.post(url, json=body, headers=headers(client))
        assert response.status_code == (201 if valid else 409)
        if not valid:
            assert response.json()["error"]["code"] == "unsupported_capability"
    receipt = response.json()
    assert receipt["occurrence"]["metadata"] == {"origin": "manual", "task_status": "queued"}
    assert "Dado próprio" not in response.text
    assert client.get(path).json() == original
    assert client.get(url + "/" + body["client_request_id"]).json() == receipt
    assert client.get(path + "/occurrences").json()[0]["metadata"]["origin"] == "manual"
    task = client.get(f"/api/v1/agents/{agent.id}/tasks/{receipt['occurrence']['task_id']}").json()
    assert task["task"]["routine_execution"]["trigger"] == "manual"
    assert (
        client.get(
            path.replace(original["id"], str(uuid4())) + "/manual/" + body["client_request_id"]
        ).status_code
        == 404
    )
    assert (
        client.post(url, json=body | {"expected_revision": 2}, headers=headers(client)).status_code
        == 409
    )


def test_manual_replay_receipt_are_available_without_worker_or_resource(runtime, monkeypatch):
    client, _, path, _ = manual(runtime)
    heartbeat(client)
    body = {"client_request_id": str(uuid4()), "expected_revision": 1}
    receipt = client.post(path + "/manual", json=body, headers=headers(client))
    assert receipt.status_code == 201
    client.app.state.store.put_cache(HEARTBEAT_KEY, None, ttl_seconds=30)

    def offline(*_):
        raise TransportError("executor_journal_missing")

    monkeypatch.setattr("bees_core.routine_manual.available_runtime", offline)
    assert (
        client.post(path + "/manual", json=body, headers=headers(client)).json() == receipt.json()
    )
    assert client.get(path + "/manual/" + body["client_request_id"]).json() == receipt.json()
