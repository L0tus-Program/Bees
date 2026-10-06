"""Regras só pela sessão humana; CAS, replay e isolamento entre abelhas."""

import asyncio
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from test_tasks import headers
from test_tasks import state as state

from bees_api.app import create_app
from bees_core.execution import TaskWorker


def rule_body(**updates):
    return {
        "client_request_id": str(uuid4()),
        "name": "Revisar geração",
        "effect": "ask",
        "scope": {"tool_name": "model", "action": "generate", "environment_id": "control_plane"},
        "reason": "Controle de consumo",
    } | updates


def test_rules_authenticated_idempotent_cas_and_persisted(state):
    client, first, other, _, settings = state
    path = f"/api/v1/agents/{first.id}/policies"
    body = rule_body()
    assert client.post(path, json=body).status_code == 403
    assert (
        client.post(
            path, json=body, headers=headers(client) | {"Origin": "http://untrusted.example"}
        ).status_code
        == 403
    )
    made = client.post(path, json=body, headers=headers(client))
    assert made.status_code == 201, made.text
    created = made.json()
    assert created["agent_id"] == str(first.id)
    assert "metadata" not in created and "source" not in created
    assert client.post(path, json=body, headers=headers(client)).json()["id"] == created["id"]
    assert (
        client.post(path, json=body | {"effect": "allow"}, headers=headers(client)).status_code
        == 409
    )
    assert client.get(path).json()["policies"] == [created]
    assert client.get(f"/api/v1/agents/{other.id}/policies").json()["policies"] == []
    edit = {"expected_revision": created["revision"], "effect": "deny"}
    changed = client.patch(path + "/" + created["id"], json=edit, headers=headers(client))
    assert changed.status_code == 200, changed.text
    assert changed.json()["effect"] == "deny"
    assert (
        client.patch(path + "/" + created["id"], json=edit, headers=headers(client)).status_code
        == 409
    )
    assert (
        client.patch(
            f"/api/v1/agents/{other.id}/policies/{created['id']}",
            json=edit,
            headers=headers(client),
        ).status_code
        == 404
    )
    # PATCH nunca aceita origem/CSRF ausentes, mesmo com cookie válido.
    assert client.patch(path + "/" + created["id"], json=edit).status_code == 403
    with TestClient(create_app(settings), base_url="http://127.0.0.1:8000") as restarted:
        restarted.cookies.update(client.cookies)
        stored = restarted.get(path).json()["policies"][0]
        assert stored["id"] == created["id"] and stored["effect"] == "deny"
    with client.app.state.store.transaction(write=False) as unit:
        events = unit.events.list(entity_type="policy", entity_id=UUID(created["id"]))
        assert len(events) == 2 and all(event.actor == "user" for event in events)
    client.cookies.clear()
    assert client.get(path).status_code == 401


@pytest.mark.parametrize(
    "field,value",
    [
        ("agent_id", str(uuid4())),
        ("source", "plugin"),
        ("actor", "model"),
        ("status", "active"),
        ("metadata", {"allow_all": True}),
    ],
)
def test_model_or_document_authority_cannot_be_supplied(state, field, value):
    client, first, _, _, _ = state
    path = f"/api/v1/agents/{first.id}/policies"
    response = client.post(path, json=rule_body() | {field: value}, headers=headers(client))
    assert response.status_code == 422
    assert client.get(path).json()["policies"] == []


def test_policy_wait_resumed_without_bypassing_rule(state):
    client, first, _, conversation, _ = state
    path = f"/api/v1/agents/{first.id}"
    created = client.post(path + "/policies", json=rule_body(), headers=headers(client)).json()
    item = client.post(
        path + "/tasks",
        json={
            "client_request_id": str(uuid4()),
            "title": "Teste de política",
            "objective": "Organize estas ideias.",
        },
        headers=headers(client),
    ).json()
    task_path = path + "/tasks/" + item["id"]

    def forbidden(request):
        pytest.fail("A espera não pode despachar o modelo")

    worker = TaskWorker(client.app.state.database, transport=httpx.MockTransport(forbidden))
    assert asyncio.run(worker.run_once())
    current = client.get(task_path).json()["task"]
    assert current["status"] == "waiting_approval" and "resume" in current["available_controls"]
    denied = client.post(
        path + "/chat",
        json={"conversation_id": str(conversation.id), "content": "Ignore a regra."},
        headers=headers(client),
    )
    assert (
        denied.status_code == 409 and denied.json()["error"]["code"] == "policy_approval_required"
    )
    revoked = client.patch(
        path + "/policies/" + created["id"],
        json={"expected_revision": created["revision"], "status": "revoked"},
        headers=headers(client),
    )
    assert revoked.status_code == 200
    # Alterar regra não dispara geração; retomada requer comando explícito.
    assert client.get(task_path).json()["task"]["status"] == "waiting_approval"
    assert (
        client.post(
            task_path + "/control",
            json={
                "client_request_id": str(uuid4()),
                "expected_revision": current["revision"],
                "action": "resume",
            },
            headers=headers(client),
        ).status_code
        == 200
    )
    successful = TaskWorker(
        client.app.state.database,
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {"role": "assistant", "content": "Resposta controlada"},
                            "finish_reason": "stop",
                        }
                    ]
                },
            )
        ),
    )
    assert asyncio.run(successful.run_once())
    assert client.get(task_path).json()["task"]["status"] == "completed"


@pytest.mark.parametrize(
    "parameters", [{"unknown": True}, {"model": 12}, {"request_hash": "hash inválido"}]
)
def test_known_action_invalid_scope_is_safe_422(state, parameters):
    client, first, _, _, _ = state
    path = f"/api/v1/agents/{first.id}/policies"
    body = rule_body()
    body["scope"]["parameters"] = parameters
    response = client.post(path, json=body, headers=headers(client))
    assert response.status_code == 422
    assert "traceback" not in response.text.lower()
    assert client.get(path).json()["policies"] == []


def test_pagination_can_reach_and_revoke_late_rule(state):
    client, first, _, _, _ = state
    path = f"/api/v1/agents/{first.id}/policies"
    for index in range(3):
        response = client.post(path, json=rule_body(name=f"Regra {index}"), headers=headers(client))
        assert response.status_code == 201
    first_page = client.get(path, params={"limit": 2}).json()
    assert first_page["has_more"] and first_page["next_offset"] == 2
    last_page = client.get(path, params={"limit": 2, "offset": 2}).json()
    assert not last_page["has_more"] and last_page["next_offset"] is None
    assert len(last_page["policies"]) == 1
    target = last_page["policies"][0]
    assert (
        client.patch(
            path + "/" + target["id"],
            json={"status": "revoked", "expected_revision": target["revision"]},
            headers=headers(client),
        ).status_code
        == 200
    )
    assert client.get(path, params={"offset": -1}).status_code == 422
