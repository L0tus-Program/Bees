"""Criação humana de execução com revisão fixa e reconciliação independente do runtime."""

from uuid import uuid4

from fastapi.testclient import TestClient
from test_global_stop_api import ORIGIN, headers
from test_tool_tasks_api import runtime as runtime
from test_tool_tasks_api import state as state

from bees_api.worker_status import HEARTBEAT_KEY
from bees_core.skills import CreateSkill, EnableSkill, SkillDefinition, SkillService


def prepared(runtime):
    client, agent, _, _ = runtime
    service = SkillService(client.app.state.database)
    definition = SkillDefinition(
        name="Conferir texto",
        purpose="Normalizar entrada própria.",
        inputs=[{"name": "texto", "description": "Texto próprio"}],
        steps=[{"title": "Normalizar", "instruction": "Use upper e confira a saída."}],
        resources={"environment": "own", "tools": ["text.normalize"]},
        output="Texto normalizado.",
        completion="Saída confirmada.",
    )
    skill = service.create(agent.id, CreateSkill(client_request_id=uuid4(), definition=definition))
    service.enable(
        agent.id,
        skill.id,
        EnableSkill(client_request_id=uuid4(), expected_revision=1, version=1, enabled=True),
    )
    body = {
        "client_request_id": str(uuid4()),
        "skill_id": str(skill.id),
        "skill_version": 1,
        "inputs": {"texto": "Dado próprio"},
        "max_calls": 4,
        "max_active_seconds": 120,
    }
    return client, agent, skill, service, body


def test_authenticated_closed_request_and_no_authority_fields(runtime):
    client, agent, _, _, body = prepared(runtime)
    endpoint = f"/api/v1/agents/{agent.id}/skill-tasks"
    # Middleware de origem/CSRF pode recusar antes da autenticação.
    assert TestClient(client.app, base_url=ORIGIN).post(endpoint, json=body).status_code in (
        401,
        403,
    )
    assert client.post(endpoint, json=body).status_code == 403
    for extra in (
        {"resource_id": str(uuid4())},
        {"approved": True},
        {"definition": {}},
        {"max_calls": 5},
        {"skill_version": True},
    ):
        assert client.post(endpoint, json=body | extra, headers=headers(client)).status_code == 422
    assert (
        client.post(endpoint, json=body | {"inputs": {}}, headers=headers(client)).json()["error"][
            "code"
        ]
        == "skill_inputs_invalid"
    )
    assert (
        client.post(
            endpoint, json=body, headers=headers(client) | {"Origin": "https://evil.test"}
        ).status_code
        == 403
    )


def test_exact_receipt_replay_after_disable_and_worker_disappears(runtime):
    client, agent, skill, service, body = prepared(runtime)
    endpoint = f"/api/v1/agents/{agent.id}/skill-tasks"
    first = client.post(endpoint, json=body, headers=headers(client))
    assert first.status_code == 201
    value = first.json()
    assert value["task"]["skill_execution"]["skill_id"] == str(skill.id)
    assert value["task"]["skill_execution"]["version"] == 1
    assert "Dado próprio" not in first.text  # Projeção da lista não expõe entradas.
    service.enable(
        agent.id,
        skill.id,
        EnableSkill(client_request_id=uuid4(), expected_revision=2, version=1, enabled=False),
    )
    client.app.state.store.put_cache(HEARTBEAT_KEY, {}, ttl_seconds=20)
    assert client.post(endpoint, json=body, headers=headers(client)).json() == value
    context_path = f"/api/v1/agents/{agent.id}/tasks/{value['task']['id']}/skill-context"
    assert TestClient(client.app, base_url=ORIGIN).get(context_path).status_code == 401
    saved = client.get(context_path).json()
    assert saved["snapshot"]["inputs"] == body["inputs"]
    assert saved["snapshot"]["version"] == 1
    assert saved["snapshot_hash"] == value["task"]["skill_execution"]["snapshot_hash"]
    assert client.get(context_path.replace(str(agent.id), str(uuid4()))).status_code == 404
    assert (
        client.post(
            endpoint, json=body | {"inputs": {"texto": "Outro"}}, headers=headers(client)
        ).status_code
        == 409
    )
    assert (
        client.post(
            endpoint, json=body | {"client_request_id": str(uuid4())}, headers=headers(client)
        ).status_code
        == 409
    )
    assert (
        client.get(f"/api/v1/agents/{agent.id}/tasks").json()["tasks"][0]["skill_execution"]
        == value["task"]["skill_execution"]
    )


def test_generic_creation_cannot_replay_a_skill_submission(runtime):
    client, agent, _, _, body = prepared(runtime)
    result = client.post(
        f"/api/v1/agents/{agent.id}/skill-tasks", json=body, headers=headers(client)
    ).json()["task"]
    generic = {
        "client_request_id": body["client_request_id"],
        "title": result["title"],
        "objective": result["objective"],
        "expected_result": result["expected_result"],
        "max_calls": 4,
        "max_active_seconds": 120,
        "deliverables": [],
    }
    for name in ("tasks", "tool-tasks"):
        response = client.post(
            f"/api/v1/agents/{agent.id}/{name}", json=generic, headers=headers(client)
        )
        assert (
            response.status_code == 409
            and response.json()["error"]["code"] == "idempotency_conflict"
        )
