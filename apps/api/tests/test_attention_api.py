"""Sessão humana, CSRF, CAS/replay e projeções tipadas dos avisos locais."""

import asyncio
from uuid import uuid4

from fastapi.testclient import TestClient
from test_global_stop_api import ORIGIN, headers
from test_global_stop_api import state as state

from bees_core.approvals import ApprovalDecisionInput, ApprovalService
from bees_core.execution import TaskWorker
from bees_core.models import Agent
from bees_core.policies import PolicyInput, PolicyService
from bees_core.tasks import TaskInput, TaskService


def failed(client, agent, title="Tarefa de teste"):
    task = TaskService(client.app.state.database).create(
        agent.id,
        TaskInput(
            client_request_id=uuid4(),
            title=title,
            objective="Objetivo privado não deve entrar no aviso",
        ),
    )
    with client.app.state.store.transaction() as unit:
        unit.tasks.update(task.model_copy(update={"status": "failed"}), task.revision)
    return task


def test_get_requires_session_and_preserves_empty_database(state):
    client, agent, _ = state
    path = f"/api/v1/agents/{agent.id}/attention"
    anonymous = TestClient(client.app, base_url=ORIGIN)
    assert anonymous.get(path).status_code == 401
    assert anonymous.get(path + "/preferences").status_code == 401
    assert client.get(path).json() == {"items": [], "next_before": None, "unread_count": 0}
    preferences = client.get(path + "/preferences").json()
    assert preferences["agent_id"] == str(agent.id)
    assert len(preferences["items"]) == 4
    assert all(item["enabled"] and item["revision"] == 0 for item in preferences["items"])
    with client.app.state.database.transaction(write=False) as connection:
        assert connection.execute("SELECT count(*) FROM attention_preferences").get == 0
        assert connection.execute("SELECT count(*) FROM attention_items").get == 0
    assert client.get(path + "?limit=0").status_code == 422
    assert client.get(path + "?limit=101").status_code == 422
    assert client.get(path + "?before=0").status_code == 422
    assert client.get(path + f"?before={2**100}").status_code == 422
    assert client.get(f"/api/v1/agents/{uuid4()}/attention").status_code == 404


def test_read_requires_csrf_uuid_strict_input_cas_and_agent_binding(state):
    client, agent, _ = state
    task = failed(client, agent, "<script>Texto escapado pelo React</script>")
    path = f"/api/v1/agents/{agent.id}/attention"
    item = client.get(path).json()["items"][0]
    assert item["task_title"] == task.title and item["task_id"] == str(task.id)
    assert set(item) == {
        "id",
        "agent_id",
        "task_id",
        "task_title",
        "approval_id",
        "kind",
        "created_at",
        "revision",
        "read",
        "requires_attention",
        "muted",
    }
    assert task.objective not in client.get(path).text
    read_path = path + f"/{item['id']}/read"
    body = {"client_request_id": str(uuid4()), "expected_revision": 1, "read": True}
    assert client.put(read_path, json=body, headers={"Origin": ORIGIN}).status_code in (401, 403)
    for invalid in (
        {"read": "true"},
        {"expected_revision": "1"},
        {"extra": True},
        {"client_request_id": "invalid"},
    ):
        assert (
            client.put(read_path, json=body | invalid, headers=headers(client)).status_code == 422
        )
    response = client.put(read_path, json=body, headers=headers(client))
    assert response.status_code == 200 and response.json()["revision"] == 2
    assert response.json()["read"] and client.get(path).json()["unread_count"] == 0
    assert client.put(read_path, json=body, headers=headers(client)).json() == response.json()
    assert (
        client.put(read_path, json=body | {"read": False}, headers=headers(client)).status_code
        == 409
    )
    assert (
        client.put(
            read_path, json=body | {"client_request_id": str(uuid4())}, headers=headers(client)
        ).status_code
        == 409
    )
    another = Agent(name="Outra")
    with client.app.state.store.transaction() as unit:
        unit.agents.create(another)
    assert (
        client.put(
            f"/api/v1/agents/{another.id}/attention/{item['id']}/read",
            json=body,
            headers=headers(client),
        ).status_code
        == 404
    )
    with client.app.state.store.transaction(write=False) as unit:
        assert unit.tasks.get(task.id).status == "failed"


def test_preferences_silence_badge_but_keep_failures_and_replay_returns_current(state):
    client, agent, _ = state
    failed(client, agent)
    path = f"/api/v1/agents/{agent.id}/attention"
    preference_path = path + "/preferences/task_failed"
    body = {"client_request_id": str(uuid4()), "expected_revision": 0, "enabled": False}
    assert client.put(preference_path, json=body, headers={"Origin": ORIGIN}).status_code in (
        401,
        403,
    )
    assert (
        client.put(
            preference_path, json=body | {"enabled": "false"}, headers=headers(client)
        ).status_code
        == 422
    )
    changed = client.put(preference_path, json=body, headers=headers(client))
    assert changed.status_code == 200
    assert changed.json() == {"kind": "task_failed", "enabled": False, "revision": 1}
    page = client.get(path).json()
    assert page["unread_count"] == 0 and len(page["items"]) == 1
    assert page["items"][0]["requires_attention"] and page["items"][0]["muted"]
    next_body = {"client_request_id": str(uuid4()), "expected_revision": 1, "enabled": True}
    client.put(preference_path, json=next_body, headers=headers(client))
    replay = client.put(preference_path, json=body, headers=headers(client))
    assert replay.json() == {"kind": "task_failed", "enabled": True, "revision": 2}
    assert (
        client.put(
            path + "/preferences/task_completed", json=body, headers=headers(client)
        ).status_code
        == 409
    )
    assert (
        client.put(path + "/preferences/malformed", json=body, headers=headers(client)).status_code
        == 422
    )


def test_pending_decision_read_is_not_consent_and_resolved_decision_leaves_badge(state):
    client, agent, _ = state
    database = client.app.state.database
    PolicyService(database).create(agent.id, PolicyInput(name="Perguntar", effect="ask"))
    task = TaskService(database).create(
        agent.id,
        TaskInput(
            client_request_id=uuid4(),
            title="Decisão concreta",
            objective="Nunca copiar conteúdo privado no aviso",
        ),
    )
    assert asyncio.run(TaskWorker(database).run_once())
    path = f"/api/v1/agents/{agent.id}/attention"
    item = client.get(path).json()["items"][0]
    assert item["kind"] == "approval_required" and item["requires_attention"]
    response = client.put(
        path + f"/{item['id']}/read",
        headers=headers(client),
        json={"client_request_id": str(uuid4()), "expected_revision": 1, "read": True},
    )
    assert response.json()["requires_attention"]
    approvals = ApprovalService(database)
    pending = approvals.get(agent.id, item["approval_id"])
    assert pending.status == "pending"
    assert TaskService(database).detail(agent.id, task.id).task.calls_started == 0
    approvals.decide(
        agent.id,
        pending.id,
        ApprovalDecisionInput(
            client_request_id=uuid4(), expected_revision=pending.revision, decision="allow_once"
        ),
    )
    resolved = client.get(path).json()["items"][0]
    assert resolved["id"] == item["id"] and not resolved["requires_attention"]
    assert client.get(path).json()["unread_count"] == 0


def test_pagination_can_follow_cursor_without_duplicates(state):
    client, agent, _ = state
    for index in range(3):
        failed(client, agent, f"Falha {index}")
    path = f"/api/v1/agents/{agent.id}/attention"
    first = client.get(path + "?limit=2").json()
    assert isinstance(first["next_before"], int) and first["unread_count"] == 3
    second = client.get(path + f"?limit=2&before={first['next_before']}").json()
    assert second["next_before"] is None
    assert len({item["id"] for item in first["items"] + second["items"]}) == 3
