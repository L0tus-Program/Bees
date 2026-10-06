"""Observação dos journals e reconhecimento humano sem canal de execução."""

import asyncio
from uuid import UUID, uuid4

import httpx
from fastapi.testclient import TestClient
from test_approvals_api import decision, waiting
from test_tasks import ORIGIN, body, headers
from test_tasks import state as state

from bees_api.app import create_app
from bees_core.execution import TaskWorker
from bees_core.models import Action

PRIVATE = "conteudo-privado-do-journal"
ACTION_FIELDS = {
    "id",
    "run_id",
    "tool_name",
    "status",
    "error_code",
    "obsolete",
    "acknowledged",
    "created_at",
    "updated_at",
}


def paused_task(client, agent):
    path = f"/api/v1/agents/{agent.id}/tasks"
    made = client.post(path, json=body(), headers=headers(client))
    assert made.status_code == 201, made.text
    task = made.json()
    path += "/" + task["id"]
    paused = client.post(
        path + "/control",
        json={
            "client_request_id": str(uuid4()),
            "expected_revision": task["revision"],
            "action": "pause",
        },
        headers=headers(client),
    )
    assert paused.status_code == 200, paused.text
    return path, paused.json()


def journal(client, task, status="outcome_unknown", **changes):
    # Dados descartáveis escritos pela camada confiável: a API não despacha tools.
    with client.app.state.store.transaction() as unit:
        return unit.actions.create(
            Action(
                run_id=UUID(task["active_run_id"]),
                tool_name="text.normalize",
                status=status,
                parameters={"input_hash": PRIVATE},
                result={"text": PRIVATE} if status == "confirmed" else None,
                metadata={
                    "contract_version": 1,
                    "snapshot": PRIVATE,
                    "error_code": PRIVATE,
                    "recovery_evidence_ref": PRIVATE,
                },
                **changes,
            )
        )


def test_action_summaries_are_private_readonly_and_agent_scoped(state):
    client, first, other, _, settings = state
    path, task = paused_task(client, first)
    action = journal(client, task)
    journal(client, task, "confirmed")
    with client.app.state.store.transaction(write=False) as unit:
        before = unit.actions.get(action.id)
    for _ in range(2):
        response = client.get(path)
        assert response.status_code == 200, response.text
        detail = response.json()
        assert len(detail["actions"]) == 2
        assert all(set(item) == ACTION_FIELDS for item in detail["actions"])
        assert all(item["error_code"] is None for item in detail["actions"])
        assert PRIVATE not in response.text
        for field in ("execution_binding", "parameters", "result", "metadata", "lease_generation"):
            assert field not in str(detail["actions"])
        assert detail["task"]["unknown_tool_actions"] == 1
        assert detail["task"]["unknown_model_calls"] == 0
        assert detail["task"]["unknown_requires_ack"] is True
    with client.app.state.store.transaction(write=False) as unit:
        assert unit.actions.get(action.id) == before
    assert client.get(f"/api/v1/agents/{other.id}/tasks/{task['id']}").status_code == 404
    anonymous = TestClient(client.app, base_url=ORIGIN)
    assert anonymous.get(path).status_code == 401
    with TestClient(create_app(settings), base_url=ORIGIN) as restarted:
        restarted.cookies.update(client.cookies)
        assert restarted.get(path).json()["actions"] == detail["actions"]


def test_unknown_action_requires_explicit_cas_ack_and_preserves_old_journal(state):
    client, first, _, _, settings = state
    path, task = paused_task(client, first)
    action = journal(client, task)
    control = path + "/control"
    resume = {
        "client_request_id": str(uuid4()),
        "expected_revision": task["revision"],
        "action": "resume",
    }
    rejected = client.post(control, json=resume, headers=headers(client))
    assert rejected.status_code == 409 and "unknown_requires_ack" in rejected.text
    redirected = client.post(
        control,
        json=resume | {"action": "redirect", "instruction": "Outro objetivo"},
        headers=headers(client),
    )
    assert redirected.status_code == 409
    accepted_body = resume | {"acknowledge_unknown": True}
    assert client.post(control, json=accepted_body).status_code == 403
    assert (
        client.post(
            control,
            json=accepted_body | {"expected_revision": task["revision"] + 1},
            headers=headers(client),
        ).status_code
        == 409
    )
    accepted = client.post(control, json=accepted_body, headers=headers(client))
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["unknown_requires_ack"] is False
    assert accepted.json()["unknown_tool_actions"] == 0
    assert accepted.json()["active_run_id"] != task["active_run_id"]
    replay = client.post(control, json=accepted_body, headers=headers(client))
    assert replay.json() == accepted.json()
    assert (
        client.post(
            control, json=accepted_body | {"acknowledge_unknown": False}, headers=headers(client)
        ).status_code
        == 409
    )
    with client.app.state.store.transaction(write=False) as unit:
        old = unit.actions.get(action.id)
        assert old.status == "outcome_unknown" and old.result is None
        assert old.unknown_acknowledged_at is not None
    with TestClient(create_app(settings), base_url=ORIGIN) as restarted:
        restarted.cookies.update(client.cookies)
        detail = restarted.get(path).json()
        assert detail["actions"][0]["status"] == "outcome_unknown"
        assert detail["actions"][0]["acknowledged"] is True
        assert detail["task"]["unknown_requires_ack"] is False


def test_unknown_aggregate_is_independent_of_summary_page(state):
    client, first, _, _, _ = state
    path, task = paused_task(client, first)
    for _ in range(101):
        journal(client, task, "confirmed")
    journal(client, task)
    detail = client.get(path).json()
    assert detail["has_more"] is True
    assert len(detail["actions"]) == 100
    assert all(item["status"] == "confirmed" for item in detail["actions"])
    assert detail["task"]["unknown_requires_ack"] is True
    listed = client.get(f"/api/v1/agents/{first.id}/tasks").json()["tasks"][0]
    assert listed["unknown_tool_actions"] == 1 and listed["unknown_requires_ack"] is True
    response = client.post(
        path + "/control",
        json={
            "client_request_id": str(uuid4()),
            "expected_revision": task["revision"],
            "action": "resume",
        },
        headers=headers(client),
    )
    assert response.status_code == 409


def test_in_flight_action_cannot_be_acknowledged_as_unknown(state):
    client, first, _, _, _ = state
    path, task = paused_task(client, first)
    action = journal(client, task, "dispatch_started")
    detail = client.get(path).json()
    assert detail["task"]["action_in_flight"] is True
    assert detail["task"]["unknown_requires_ack"] is False
    response = client.post(
        path + "/control",
        json={
            "client_request_id": str(uuid4()),
            "expected_revision": task["revision"],
            "action": "resume",
            "acknowledge_unknown": True,
        },
        headers=headers(client),
    )
    assert response.status_code == 409
    with client.app.state.store.transaction(write=False) as unit:
        assert unit.actions.get(action.id).status == "dispatch_started"


def test_textual_approval_and_model_journal_are_not_counted_twice(state):
    client, first, _, _, _ = state
    base, task, approval = waiting(client, first)
    accepted = client.post(
        base + "/approvals/" + approval["id"] + "/decision",
        json=decision(approval),
        headers=headers(client),
    )
    assert accepted.status_code == 200

    def lost(request):
        raise httpx.ReadError("Resposta descartável perdida", request=request)

    worker = TaskWorker(client.app.state.database, transport=httpx.MockTransport(lost))
    assert asyncio.run(worker.run_once())
    detail = client.get(base + "/tasks/" + task["id"]).json()
    assert detail["task"]["unknown_model_calls"] == 1
    assert detail["task"]["unknown_tool_actions"] == 0
    assert detail["task"]["unknown_requires_ack"] is True
    assert detail["actions"] == []
    assert any(event["code"] == "outcome_unknown" for event in detail["events"])
    # Revogar consentimento consumido não desfaz nem duplica o efeito textual.
    approval_path = base + "/approvals/" + approval["id"]
    current = client.get(approval_path).json()
    revoked = client.patch(
        approval_path,
        json={"expected_revision": current["revision"], "status": "revoked"},
        headers=headers(client),
    )
    assert revoked.status_code == 200, revoked.text
    after = client.get(base + "/tasks/" + task["id"]).json()
    assert after["task"]["unknown_model_calls"] == 1
    assert after["task"]["unknown_tool_actions"] == 0
    assert after["actions"] == []
