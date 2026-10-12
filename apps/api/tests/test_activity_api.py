"""Contrato autenticado e fechado da atividade, orçamento e parada em banco próprio."""

from datetime import datetime, timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from bees_api.app import create_app
from bees_api.config import Settings
from bees_core.models import Agent, Conversation, utc_now
from bees_core.providers.contracts import ProviderConfig
from bees_core.tasks import TaskInput, TaskService

ORIGIN = "http://127.0.0.1:8000"


@pytest.fixture
def state(tmp_path):
    settings = Settings(data_dir=tmp_path / "state", web_dist=tmp_path / "missing")
    with TestClient(create_app(settings), base_url=ORIGIN) as client:
        token = client.app.state.identity.issue_bootstrap()
        assert (
            client.post(
                "/api/v1/auth/setup",
                json={
                    "bootstrap_token": token,
                    "name": "Teste",
                    "password": "senha teste 123456",
                },
                headers={"Origin": ORIGIN},
            ).status_code
            == 200
        )
        config = ProviderConfig(
            kind="openai_compatible",
            endpoint="https://private.example/v1",
            model="private-model",
            capabilities={"text": True},
        )
        agent = Agent(name="Atividade de teste", provider_config=config.model_dump(mode="json"))
        other = Agent(name="Outra", provider_config=config.model_dump(mode="json"))
        with client.app.state.store.transaction() as unit:
            unit.agents.create(agent)
            unit.agents.create(other)
            unit.conversations.create(Conversation(agent_id=agent.id))
        task = TaskService(client.app.state.database).create(
            agent.id,
            TaskInput(
                client_request_id=uuid4(),
                title="Título público",
                objective="PRIVATE OBJECTIVE",
                expected_result="PRIVATE RESULT",
            ),
        )
        yield client, agent, other, task


def headers(client):
    return {"Origin": ORIGIN, "X-Bees-CSRF": client.get("/api/v1/auth/status").json()["csrf_token"]}


def snapshot(client):
    with client.app.state.database.transaction(write=False) as connection:
        return {
            table: connection.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall()
            for table in ("domain_events", "tasks", "runs", "usage_entries", "safety_control")
        }


def test_authenticated_get_is_pure_closed_and_public_scope_is_factual(state):
    client, agent, other, task = state
    path = f"/api/v1/agents/{agent.id}/activity"
    anonymous = TestClient(client.app, base_url=ORIGIN)
    assert anonymous.get(path).status_code == 401
    before = snapshot(client)
    result = client.get(path)
    assert result.status_code == 200, result.text
    body = result.json()
    assert body["agent_id"] == str(agent.id)
    assert set(body) == {
        "agent_id",
        "snapshot",
        "scope",
        "interval",
        "unit",
        "usage",
        "operations",
        "items",
        "next_before",
    }
    assert set(body["snapshot"]) == {"event_seq", "observed_at"}
    assert body["scope"] == {"task_id": None, "run_id": None}
    assert body["unit"] == "tokens"
    assert body["usage"]["entries"] == 0
    assert body["items"]
    assert all(
        item["environment"] == {"kind": "textual", "environment_id": None} for item in body["items"]
    )
    assert all(
        set(item)
        == {
            "id",
            "seq",
            "occurred_at",
            "entity_type",
            "entity_id",
            "task_id",
            "task_title",
            "run_id",
            "action_id",
            "status",
            "revision",
            "error_code",
            "environment",
            "usage",
        }
        for item in body["items"]
    )
    for secret in (
        "PRIVATE",
        "private.example",
        "private-model",
        "lease_owner",
        "metadata",
        "provider_config",
        "request_hash",
    ):
        assert secret not in result.text
    assert snapshot(client) == before
    assert (
        client.get(
            f"/api/v1/agents/{other.id}/activity",
            params={"task_id": str(task.id), "snapshot_seq": 0},
        ).status_code
        == 404
    )
    assert client.get(f"/api/v1/agents/{uuid4()}/activity").status_code == 404


@pytest.mark.parametrize(
    "parameters",
    [
        {"unknown": "ignored"},
        {"limit": 0},
        {"limit": 101},
        {"before": 2**100},
        {"snapshot_seq": 2**100},
        {"before": 1, "snapshot_seq": 0},
        {"run_id": str(uuid4())},
        {"since": "2026-01-01T00:00:00"},
        {"since": (utc_now() - timedelta(days=32)).isoformat()},
        {"until": (utc_now() + timedelta(days=1)).isoformat()},
        {"window_seconds": 60},
        {"window_seconds": 86400, "until": utc_now().isoformat()},
    ],
)
def test_invalid_query_is_422_without_writes_or_payload_echo(state, parameters):
    client, agent, _, _ = state
    before = snapshot(client)
    response = client.get(f"/api/v1/agents/{agent.id}/activity", params=parameters)
    assert response.status_code == 422, response.text
    assert "PRIVATE" not in response.text
    assert snapshot(client) == before


def test_server_clock_windows_pagination_and_snapshot_refresh_are_explicit(state):
    client, agent, _, task = state
    path = f"/api/v1/agents/{agent.id}/activity"
    for seconds in (86400, 604800, 2592000):
        result = client.get(
            path, params={"window_seconds": seconds, "task_id": str(task.id), "limit": 1}
        )
        assert result.status_code == 200, result.text
        body = result.json()
        since = datetime.fromisoformat(body["interval"]["since"])
        until = datetime.fromisoformat(body["interval"]["until"])
        assert (until - since).total_seconds() == seconds
        assert until == datetime.fromisoformat(body["snapshot"]["observed_at"])
    continuation = {
        "before": body["next_before"],
        "snapshot_seq": body["snapshot"]["event_seq"],
        "since": body["interval"]["since"],
        "until": body["interval"]["until"],
        "task_id": str(task.id),
        "limit": 1,
    }
    next_page = client.get(path, params=continuation)
    assert next_page.status_code == 200, next_page.text
    assert next_page.json()["items"][0]["seq"] < body["items"][0]["seq"]
    assert next_page.json()["interval"] == body["interval"]
    edited = client.put(
        f"/api/v1/agents/{agent.id}/budget", json={"token_limit": 7000}, headers=headers(client)
    )
    assert edited.status_code == 200
    assert client.get(path, params=continuation).status_code == 409


def test_budget_and_safety_cas_responses_include_binding_and_same_sql_snapshot(state):
    client, agent, _, _ = state
    path = f"/api/v1/agents/{agent.id}/budget"
    current = client.get(path).json()
    assert current["agent_id"] == str(agent.id)
    assert set(current["snapshot"]) == {"event_seq", "observed_at"}
    updated = client.put(
        path, json={"token_limit": 8000, "window_seconds": 3600}, headers=headers(client)
    ).json()
    assert updated["agent_id"] == str(agent.id)
    assert updated["snapshot"]["event_seq"] > current["snapshot"]["event_seq"]
    assert (
        datetime.fromisoformat(updated["interval"]["until"])
        - datetime.fromisoformat(updated["interval"]["since"])
    ).total_seconds() == 3600
    assert client.put(path, json={"token_limit": 9000}, headers=headers(client)).status_code == 409
    safety = client.get("/api/v1/safety").json()
    assert safety["snapshot"]["event_seq"] == updated["snapshot"]["event_seq"]
    stopped = client.post(
        "/api/v1/safety/commands",
        json={
            "client_request_id": str(uuid4()),
            "kind": "stop",
            "expected_revision": safety["revision"],
        },
        headers=headers(client),
    )
    assert stopped.status_code == 200
    assert stopped.json()["status"] == "stopped"
    assert stopped.json()["snapshot"]["event_seq"] > safety["snapshot"]["event_seq"]
    after = snapshot(client)
    assert (
        client.get("/api/v1/safety").json()["snapshot"]["event_seq"]
        == stopped.json()["snapshot"]["event_seq"]
    )
    assert snapshot(client) == after
