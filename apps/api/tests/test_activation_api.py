"""Reactivation is a human CAS under stop, not a replay or permission shortcut."""

from uuid import uuid4

import httpx
from fastapi.testclient import TestClient
from test_tasks import headers
from test_tasks import state as state

from bees_api.app import create_app
from bees_core.safety import SafetyCommand, SafetyService


def paused(client, agent):
    with client.app.state.store.transaction() as unit:
        current = unit.agents.update(agent.model_copy(update={"status": "paused"}), agent.revision)
    safety = SafetyService(client.app.state.database)
    stop = safety.command(
        SafetyCommand(
            client_request_id=uuid4(), kind="stop", expected_revision=safety.current()["revision"]
        )
    )
    seen = []

    def handle(request):
        seen.append((request.method, request.url.path))
        assert request.method == "GET" and request.url.path == "/v1/models"
        return httpx.Response(200, json={"data": [{"id": "test-model"}]})

    client.app.state.providers.transport = httpx.MockTransport(handle)
    value = {
        "expected_revision": current.revision,
        "expected_safety_revision": stop["revision"],
        "consent": "reactivate_paused_agent",
    }
    return f"/api/v1/agents/{agent.id}/reactivate", value, current, seen


def test_human_reactivation_keeps_stop_and_no_generation(state):
    client, first, _, _, _ = state
    path, value, current, seen = paused(client, first)
    before = client.get("/api/v1/safety").json()
    response = client.post(path, json=value, headers=headers(client))
    assert response.status_code == 200
    assert response.json() == {
        "agent_id": str(first.id),
        "revision": current.revision + 1,
        "status": "active",
        "safety_revision": value["expected_safety_revision"],
        "global_status": "stopped",
        "provider_checked": True,
    }
    assert seen == [("GET", "/v1/models")]
    after = client.get("/api/v1/safety").json()
    assert {key: item for key, item in after.items() if key != "snapshot"} == {
        key: item for key, item in before.items() if key != "snapshot"
    }
    assert after["snapshot"]["event_seq"] == before["snapshot"]["event_seq"] + 1
    assert response.headers["cache-control"] == "no-store"
    with client.app.state.database.connect() as c:
        assert c.execute("SELECT COUNT(*) FROM model_calls").get == 0
        assert c.execute("SELECT COUNT(*) FROM tasks").get == 0


def test_session_csrf_origin_and_explicit_consent_precede_probe(state):
    client, first, _, _, _ = state
    path, value, _, seen = paused(client, first)
    assert client.post(path, json=value).status_code == 403
    assert (
        client.post(
            path, json=value, headers=headers(client) | {"Origin": "http://untrusted.example"}
        ).status_code
        == 403
    )
    assert (
        client.post(path, json=value | {"consent": "implicit"}, headers=headers(client)).status_code
        == 422
    )
    client.cookies.clear()
    assert (
        client.post(path, json=value, headers={"Origin": "http://127.0.0.1:8000"}).status_code
        == 401
    )
    assert seen == []


def test_missing_stale_agent_and_stale_global_revision_fail_closed(state):
    client, first, _, _, _ = state
    path, value, _, seen = paused(client, first)
    for patch in [{"expected_revision": 1}, {"expected_safety_revision": 1}]:
        assert client.post(path, json=value | patch, headers=headers(client)).status_code == 409
    assert (
        client.post(
            path.replace(str(first.id), str(uuid4())), json=value, headers=headers(client)
        ).status_code
        == 404
    )
    assert seen == []


def test_missing_reconnection_does_not_activate_or_guess_model(state):
    client, first, _, _, _ = state
    path, value, current, seen = paused(client, first)
    with client.app.state.store.transaction() as unit:
        blank = unit.agents.update(
            current.model_copy(update={"provider_config": {}}), current.revision
        )
    response = client.post(
        path, json=value | {"expected_revision": blank.revision}, headers=headers(client)
    )
    assert (
        response.status_code == 409
        and response.json()["error"]["code"] == "activation_configuration_required"
    )
    assert seen == []


def test_restart_and_lost_response_do_not_repeat_activation(state):
    client, first, _, _, settings = state
    path, value, current, seen = paused(client, first)
    assert client.post(path, json=value, headers=headers(client)).status_code == 200
    with TestClient(create_app(settings), base_url="http://127.0.0.1:8000") as restarted:
        restarted.cookies.update(client.cookies)
        restarted.app.state.providers.transport = httpx.MockTransport(
            lambda request: (_ for _ in ()).throw(AssertionError("Unexpected second probe"))
        )
        assert restarted.post(path, json=value, headers=headers(restarted)).status_code == 409
        actual = [
            a
            for a in restarted.get("/api/v1/onboarding").json()["agents"]
            if a["id"] == str(first.id)
        ][0]
        assert actual["status"] == "active" and actual["revision"] == current.revision + 1
    assert len(seen) == 1


def test_failed_model_check_does_not_reactivate(state):
    client, first, _, _, _ = state
    path, value, current, _ = paused(client, first)
    client.app.state.providers.transport = httpx.MockTransport(
        lambda request: httpx.Response(503, json={"private": "PRIVATE_PROVIDER_ERROR"})
    )
    response = client.post(path, json=value, headers=headers(client))
    assert response.status_code != 200 and "PRIVATE_PROVIDER_ERROR" not in response.text
    with client.app.state.store.transaction(write=False) as unit:
        assert unit.agents.get(first.id) == current
