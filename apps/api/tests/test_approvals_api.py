"""API de consentimento: sessão, autoridade, replay e projeção segura."""

import asyncio
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from test_tasks import body, headers
from test_tasks import state as state

from bees_api.app import create_app
from bees_core.execution import TaskWorker


def waiting(client, agent):
    base = f"/api/v1/agents/{agent.id}"
    response = client.post(
        base + "/policies",
        json={
            "client_request_id": str(uuid4()),
            "name": "Perguntar antes de gerar",
            "effect": "ask",
            "scope": {"tool_name": "model", "action": "generate"},
        },
        headers=headers(client),
    )
    assert response.status_code == 201
    made = client.post(base + "/tasks", json=body(), headers=headers(client))
    assert made.status_code == 201
    assert asyncio.run(TaskWorker(client.app.state.database).run_once())
    response = client.get(base + "/approvals", params={"task_id": made.json()["id"]})
    assert response.status_code == 200, response.text
    approval = response.json()["approvals"][0]
    return base, made.json(), approval


def decision(approval, **updates):
    return {
        "client_request_id": str(uuid4()),
        "expected_revision": approval["revision"],
        "decision": "allow_once",
    } | updates


def test_question_persisted_after_restart_and_safe_projection(state):
    client, first, other, _, settings = state
    base, task, approval = waiting(client, first)
    assert approval["valid"] and approval["status"] == "pending"
    assert approval["task_id"] == task["id"] and approval["parameters"] == {"model": "test-model"}
    assert approval["resource"] == first.provider_config["endpoint"]
    assert "request_hash" not in approval["scope"]["parameters"]
    assert not any(name in str(approval) for name in ("secret_ref", "prepared", "history_digest"))
    with TestClient(create_app(settings), base_url="http://127.0.0.1:8000") as restarted:
        restarted.cookies.update(client.cookies)
        assert restarted.get(base + "/approvals/" + approval["id"]).json() == approval
    assert client.get(f"/api/v1/agents/{other.id}/approvals/{approval['id']}").status_code == 404
    assert (
        client.get(
            f"/api/v1/agents/{other.id}/approvals", params={"task_id": task["id"]}
        ).status_code
        == 404
    )
    anonymous = TestClient(client.app, base_url="http://127.0.0.1:8000")
    assert anonymous.get(base + "/approvals").status_code == 401
    with client.app.state.store.transaction(write=False) as unit:
        assert unit.tasks.get(UUID(task["id"])).calls_started == 0


def test_human_decision_requires_csrf_is_idempotent_and_requeues_same_run(state):
    client, first, _, _, _ = state
    base, task, approval = waiting(client, first)
    path = base + "/approvals/" + approval["id"] + "/decision"
    value = decision(approval)
    assert client.post(path, json=value).status_code == 403
    assert (
        client.post(
            path, json=value, headers=headers(client) | {"Origin": "http://untrusted.example"}
        ).status_code
        == 403
    )
    assert (
        client.post(path, json=value | {"actor": "model"}, headers=headers(client)).status_code
        == 422
    )
    accepted = client.post(path, json=value, headers=headers(client))
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["status"] == "approved"
    assert client.post(path, json=value, headers=headers(client)).json()["id"] == approval["id"]
    assert (
        client.post(path, json=value | {"decision": "deny"}, headers=headers(client)).status_code
        == 409
    )
    current = client.get(base + "/tasks/" + task["id"]).json()["task"]
    assert current["status"] == "queued" and current["active_run_id"] == task["active_run_id"]
    requests = []

    def answer(request):
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "Resultado autorizado",
                        },
                        "finish_reason": "stop",
                    }
                ]
            },
        )

    assert asyncio.run(
        TaskWorker(client.app.state.database, transport=httpx.MockTransport(answer)).run_once()
    )
    replay = client.post(path, json=value, headers=headers(client))
    assert replay.status_code == 200 and replay.json()["consumed_at"]
    assert not asyncio.run(TaskWorker(client.app.state.database).run_once())
    assert len(requests) == 1


@pytest.mark.parametrize("change", ["policy", "task"])
def test_stale_approval_cannot_authorize_changed_state(state, change):
    client, first, _, _, _ = state
    base, task, approval = waiting(client, first)
    if change == "policy":
        policy = client.get(base + "/policies").json()["policies"][0]
        changed = client.patch(
            base + "/policies/" + policy["id"],
            json={
                "expected_revision": policy["revision"],
                "effect": "deny",
            },
            headers=headers(client),
        )
    else:
        current = client.get(base + "/tasks/" + task["id"]).json()["task"]
        changed = client.post(
            base + "/tasks/" + task["id"] + "/control",
            json={
                "client_request_id": str(uuid4()),
                "expected_revision": current["revision"],
                "action": "redirect",
                "instruction": "Novo objetivo autorizado separadamente",
            },
            headers=headers(client),
        )
    assert changed.status_code == 200
    fresh = client.get(base + "/approvals/" + approval["id"]).json()
    assert not fresh["valid"]
    refused = client.post(
        base + "/approvals/" + approval["id"] + "/decision",
        json=decision(approval),
        headers=headers(client),
    )
    assert refused.status_code == 409, refused.text
    assert client.get(base + "/tasks/" + task["id"]).json()["task"]["calls_started"] == 0


def test_revocation_cas_and_pagination(state):
    client, first, _, _, _ = state
    base, _, approval = waiting(client, first)
    path = base + "/approvals/" + approval["id"]
    accepted = client.post(
        path + "/decision", json=decision(approval), headers=headers(client)
    ).json()
    value = {"expected_revision": accepted["revision"], "status": "revoked"}
    assert client.patch(path, json=value).status_code == 403
    assert (
        client.patch(
            path, json=value | {"expected_revision": 1}, headers=headers(client)
        ).status_code
        == 409
    )
    revoked = client.patch(path, json=value, headers=headers(client))
    assert revoked.status_code == 200, revoked.text
    assert revoked.json()["status"] == "revoked" and not revoked.json()["valid"]
    _, _, second = waiting(client, first)
    page = client.get(base + "/approvals", params={"limit": 1}).json()
    assert page["has_more"] and page["next_offset"] == 1
    last = client.get(base + "/approvals", params={"limit": 1, "offset": 1}).json()
    assert not last["has_more"] and last["next_offset"] is None
    assert {page["approvals"][0]["id"], last["approvals"][0]["id"]} == {
        approval["id"],
        second["id"],
    }
