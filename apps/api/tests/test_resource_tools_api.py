"""Canal humano separado; recursos de teste, sessão e transporte POSIX real."""

import os
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from test_global_stop_api import ORIGIN, headers
from test_global_stop_api import state as state

from bees_core.executor_transport.client import ProcessTextResource, ProcessTextTransport
from bees_core.executor_transport.contracts import ResourceIdentity
from bees_core.executor_transport.resource_authority import ResourceToolAuthority
from bees_core.models import Run, Task, utc_now
from bees_core.resource_tools import TASK_KIND, ResourceToolService
from bees_core.security.hosts import HostService
from bees_core.tools import ToolService


@pytest.fixture
def resource_case(state, tmp_path):
    client, agent, conversation = state
    db = client.app.state.database
    invite = HostService(db).issue_invite(uuid4())
    if os.name == "posix":
        resource = ProcessTextResource.initialize(
            tmp_path / "executor", str(invite.installation_id), str(agent.id)
        )
    else:
        # Contrato HTTP no Windows; esta identidade sintética nunca é executada.
        resource = SimpleNamespace(
            identity=ResourceIdentity(str(invite.installation_id), str(uuid4()), str(agent.id))
        )
    tools = ToolService(db)
    plugin = tools.install({"client_request_id": uuid4(), "manifest": tools.catalog()[0]})
    plugin = tools.update_plugin(plugin.id, {"expected_revision": 1, "enabled": True})
    tools.set_grant(
        agent.id, plugin.id, "text.normalize", {"expected_revision": 0, "enabled": True}
    )
    with client.app.state.store.transaction() as unit:
        task = unit.tasks.create(
            Task(
                agent_id=agent.id,
                conversation_id=conversation.id,
                title="Revisar texto",
                objective="Conferir transformação",
                metadata={"execution_kind": TASK_KIND},
            )
        )
        run = unit.runs.create(Run(task_id=task.id))
        claim = unit.execution.claim_next(uuid4(), now=utc_now(), ttl_seconds=300)
    service = ResourceToolService(db, resource.identity)
    prepared = service.prepare(
        agent.id,
        run.id,
        {
            "client_request_id": uuid4(),
            "plugin_id": plugin.id,
            "tool_name": "text.normalize",
            "version": "1",
            "arguments": {"text": "  PRIVATE-REVIEW-TEXT  ", "operation": "trim"},
        },
        claim=claim,
    )
    return client, agent, task, service, prepared, resource


def test_session_csrf_closed_decision_and_private_preview(resource_case):
    client, agent, task, _, prepared, _ = resource_case
    base = f"/api/v1/agents/{agent.id}/tool-approvals"
    path = base + f"/{prepared.approval.id}"
    anonymous = TestClient(client.app, base_url=ORIGIN)
    assert anonymous.get(base).status_code == 401
    assert (
        anonymous.get(path, headers={"Authorization": "Bearer bp_" + "A" * 43}).status_code == 401
    )
    listing = client.get(base + f"?task_id={task.id}")
    assert listing.status_code == 200 and "PRIVATE-REVIEW-TEXT" not in listing.text
    assert len(listing.json()["approvals"]) == 1
    detail = client.get(path)
    assert detail.json()["input_preview"] == "  PRIVATE-REVIEW-TEXT  "
    assert detail.json()["scope"]["action"] == "transform_resource"
    assert client.get(f"/api/v1/agents/{agent.id}/approvals").json()["approvals"] == []
    assert (
        client.get(f"/api/v1/agents/{agent.id}/approvals/{prepared.approval.id}").status_code == 404
    )
    task_detail = client.get(f"/api/v1/agents/{agent.id}/tasks/{task.id}")
    assert task_detail.json()["task"]["tool_approvals_available"] is True
    assert task_detail.json()["task"]["model_approvals_available"] is False
    assert "redirect" in task_detail.json()["task"]["available_controls"]
    assert "PRIVATE-REVIEW-TEXT" not in task_detail.text
    body = {"client_request_id": str(uuid4()), "expected_revision": 1, "decision": "allow_once"}
    assert client.post(path + "/decision", json=body).status_code in (401, 403)
    for change in (
        {"actor": "user"},
        {"resource_id": str(uuid4())},
        {"snapshot": {}},
        {"arguments": {"text": "PRIVATE-INJECTION"}},
        {"client_request_id": "0" * 32},
    ):
        response = client.post(path + "/decision", json=body | change, headers=headers(client))
        assert response.status_code == 422
        assert "PRIVATE-INJECTION" not in response.text
    assert client.get(path.replace(str(agent.id), str(uuid4()))).status_code == 404


def test_receipt_reconciles_exact_request_even_after_revocation(resource_case):
    client, agent, _, _, prepared, _ = resource_case
    path = f"/api/v1/agents/{agent.id}/tool-approvals/{prepared.approval.id}"
    body = {"client_request_id": str(uuid4()), "expected_revision": 1, "decision": "allow_once"}
    approved = client.post(path + "/decision", json=body, headers=headers(client))
    assert approved.status_code == 200
    first = approved.json()
    assert first["approval"]["status"] == "approved"
    assert first["receipt"]["client_request_id"] == body["client_request_id"]
    assert first["receipt"]["confirmed"] is True
    assert client.post(path + "/decision", json=body, headers=headers(client)).json() == first
    revoke = {"client_request_id": str(uuid4()), "expected_revision": 2, "status": "revoked"}
    revoked = client.patch(path, json=revoke, headers=headers(client))
    assert revoked.status_code == 200 and revoked.json()["approval"]["status"] == "revoked"
    replay = client.post(path + "/decision", json=body, headers=headers(client)).json()
    assert replay["receipt"] == first["receipt"]
    assert replay["approval"]["status"] == "revoked"
    changed = body | {"decision": "deny"}
    assert client.post(path + "/decision", json=changed, headers=headers(client)).status_code == 409
    assert client.post(path + "/execute", json={}, headers=headers(client)).status_code in (
        404,
        405,
    )


def test_human_redirection_invalidates_old_review_without_executing(resource_case):
    client, agent, task, service, prepared, _ = resource_case
    path = f"/api/v1/agents/{agent.id}/tool-approvals/{prepared.approval.id}"
    current = client.get(f"/api/v1/agents/{agent.id}/tasks/{task.id}").json()["task"]
    response = client.post(
        f"/api/v1/agents/{agent.id}/tasks/{task.id}/control",
        json={
            "client_request_id": str(uuid4()),
            "expected_revision": current["revision"],
            "action": "redirect",
            "instruction": "Usar outra entrada e solicitar nova revisão",
        },
        headers=headers(client),
    )
    assert response.status_code == 200
    assert response.json()["status"] == "waiting_approval"
    assert client.get(path).json()["valid"] is False
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
        == 409
    )
    with service.store.transaction(write=False) as unit:
        assert unit.actions.get(service._effect_id(prepared.intent.id)) is None
        assert unit.model_calls.list(run_id=prepared.intent.run_id) == []


@pytest.mark.skipif(os.name != "posix", reason="Canal de processo real inicial é POSIX")
def test_human_http_decision_then_real_process_has_one_effect(resource_case):
    client, agent, _, service, prepared, resource = resource_case
    path = f"/api/v1/agents/{agent.id}/tool-approvals/{prepared.approval.id}"
    body = {"client_request_id": str(uuid4()), "expected_revision": 1, "decision": "allow_once"}
    assert client.post(path + "/decision", json=body, headers=headers(client)).status_code == 200
    with client.app.state.store.transaction() as unit:
        claim = unit.execution.claim_next(uuid4(), now=utc_now(), ttl_seconds=300)
    effect = service.prepare_dispatch(agent.id, prepared.intent.id, claim=claim)
    authority = ResourceToolAuthority(
        service, agent.id, effect.id, expected_revision=effect.revision, claim=claim
    )
    result = ProcessTextTransport(resource).execute(authority)
    assert result.status == "confirmed" and result.result == {"text": "PRIVATE-REVIEW-TEXT"}
    observed = client.get(path).json()
    assert observed["consumed_at"] and observed["effect_status"] == "confirmed"
    replay = client.post(path + "/decision", json=body, headers=headers(client)).json()
    assert replay["approval"]["effect_status"] == "confirmed" and replay["receipt"]["confirmed"]
    assert ProcessTextTransport(resource).execute(authority) == result
