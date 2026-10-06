"""Pedido durável sem efeito de VM, escopo, CSRF, CAS e respostas privadas."""

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from test_tasks import ORIGIN, headers
from test_tasks import state as state

from bees_api.app import create_app


def request_body():
    return {"name": "Computador da abelha", "client_request_id": str(uuid4())}


def test_catalog_and_host_are_authenticated_read_only(state):
    client, first, _, _, settings = state
    with client.app.state.store.transaction(write=False) as unit:
        before = unit.events.list(limit=1000)
    for _ in range(2):
        catalog = client.get("/api/v1/environments/catalog").json()
        assert catalog["templates"][0]["status"] == "planned"
        assert catalog["templates"][0]["provisionable"] is False
        assert catalog["host"] == client.get("/api/v1/environments/host").json()
        assert catalog["host"]["driver"] == "none"
        assert catalog["host"]["provisionable"] is False
        assert client.get(f"/api/v1/agents/{first.id}/environments").json()["environments"] == []
    with client.app.state.store.transaction(write=False) as unit:
        assert unit.events.list(limit=1000) == before
    with TestClient(create_app(settings), base_url=ORIGIN) as anonymous:
        assert anonymous.get("/api/v1/environments/catalog").status_code == 401
        assert anonymous.get("/api/v1/environments/host").status_code == 401


def test_create_replay_restart_scope_and_cancel(state):
    client, first, other, _, settings = state
    path = f"/api/v1/agents/{first.id}/environments"
    value = request_body()
    response = client.post(path, json=value, headers=headers(client))
    assert response.status_code == 201, response.text
    record = response.json()
    assert record["status"] == record["operation_status"] == "awaiting_host"
    assert record["reason_code"] == "host_setup_required"
    assert record["usable"] is False
    assert record["operation_id"]
    assert not {"metadata", "request_hash", "client_request_id", "owner_id"} & record.keys()
    assert client.post(path, json=value, headers=headers(client)).json() == record
    assert (
        client.post(path, json=value | {"name": "Outro"}, headers=headers(client)).status_code
        == 409
    )
    detail = path + "/" + record["id"]
    foreign = f"/api/v1/agents/{other.id}/environments/{record['id']}"
    assert client.get(foreign).status_code == 404
    control = {"expected_revision": record["revision"], "client_request_id": str(uuid4())}
    assert (
        client.post(foreign + "/cancel", json=control, headers=headers(client)).status_code == 404
    )
    with TestClient(create_app(settings), base_url=ORIGIN) as restarted:
        restarted.cookies.update(client.cookies)
        assert restarted.get(detail).json() == record
        cancelled = restarted.post(detail + "/cancel", json=control, headers=headers(restarted))
        assert cancelled.status_code == 200, cancelled.text
        terminal = cancelled.json()
        assert terminal["status"] == terminal["operation_status"] == "cancelled"
        assert (
            restarted.post(detail + "/cancel", json=control, headers=headers(restarted)).json()
            == terminal
        )
        assert restarted.post(path, json=value, headers=headers(restarted)).json() == terminal
        stale = control | {"client_request_id": str(uuid4())}
        assert (
            restarted.post(detail + "/cancel", json=stale, headers=headers(restarted)).status_code
            == 409
        )
        changed_replay = control | {"expected_revision": terminal["revision"]}
        assert (
            restarted.post(
                detail + "/cancel", json=changed_replay, headers=headers(restarted)
            ).status_code
            == 409
        )
        assert restarted.get(path).json()["environments"] == [terminal]


def test_page_and_mutation_requirements(state):
    client, first, _, _, _ = state
    path = f"/api/v1/agents/{first.id}/environments"
    assert client.post(path, json=request_body()).status_code == 403
    for _ in range(2):
        assert client.post(path, json=request_body(), headers=headers(client)).status_code == 201
    page = client.get(path + "?limit=1").json()
    assert page["has_more"] is True and page["next_offset"] == 1
    next_page = client.get(path + "?limit=1&offset=1").json()
    assert next_page["has_more"] is False and next_page["next_offset"] is None
    assert page["environments"][0]["id"] != next_page["environments"][0]["id"]
    assert client.get(path + "?limit=101").status_code == 422
    assert client.get(path + "?offset=-1").status_code == 422
    assert client.post(
        path + "/" + str(uuid4()) + "/start", json={}, headers=headers(client)
    ).status_code in (404, 405)


@pytest.mark.parametrize(
    "extra",
    [
        {"cpu_count": 5},
        {"memory_mib": 1},
        {"disk_gib": 101},
        {"cpu_count": True},
        {"driver": "hyperv"},
        {"image_url": "https://secret.example/image"},
        {"shell": "arbitrary"},
        {"host_token": "private-test-token"},
        {"name": "\n"},
    ],
)
def test_input_cannot_select_host_or_escape_resources(state, extra):
    client, first, _, _, _ = state
    path = f"/api/v1/agents/{first.id}/environments"
    result = client.post(path, json=request_body() | extra, headers=headers(client))
    assert result.status_code == 422
    assert "private-test-token" not in result.text
    assert "secret.example" not in result.text
    assert client.get(path).json()["environments"] == []
