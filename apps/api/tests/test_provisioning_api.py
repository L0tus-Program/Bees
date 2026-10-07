"""Aprovação humana não é execução; escopo, replay, CSRF e reabertura reais."""

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from test_host_links_api import pairing, runtime_headers
from test_tasks import ORIGIN, headers
from test_tasks import state as state

from bees_api.app import create_app
from bees_core.provisioning import DEFAULT_PROVISIONING_CATALOG, ArtifactPin


def context(state):
    client, agent, _, _, _ = state
    host, credential, _ = pairing(client)
    confirmed = client.post(
        f"/api/v1/environments/hosts/{host['host_id']}/confirm",
        json={
            "expected_revision": host["revision"],
            "client_request_id": str(uuid4()),
            "fingerprint": host["fingerprint"],
        },
        headers=headers(client),
    )
    assert confirmed.status_code == 200, confirmed.text
    host = confirmed.json()
    environment = client.post(
        f"/api/v1/agents/{agent.id}/environments",
        json={"name": "Computador de teste", "client_request_id": str(uuid4())},
        headers=headers(client),
    ).json()
    path = f"/api/v1/agents/{agent.id}/environments/{environment['id']}/provisioning"
    body = {
        "host_id": host["host_id"],
        "expected_host_revision": host["revision"],
        "expected_environment_revision": environment["revision"],
        "client_request_id": str(uuid4()),
    }
    return client, path, body, host, credential, environment


def decision(plan):
    return {
        "plan_hash": plan["plan_hash"],
        "expected_revision": plan["revision"],
        "client_request_id": str(uuid4()),
    }


def test_readonly_preparation_authorization_restart_and_revocation(state):
    client, path, body, host, credential, environment = context(state)
    with client.app.state.database.transaction(write=False) as connection:
        count = connection.execute("SELECT count(*) FROM domain_events").get
    for _ in range(2):
        overview = client.get(path)
        assert overview.status_code == 200
        assert overview.json() == {
            "plan": None,
            "host": {key: host[key] for key in ("host_id", "revision", "fingerprint")},
            "preparation_available": True,
        }
    with client.app.state.database.transaction(write=False) as connection:
        assert connection.execute("SELECT count(*) FROM domain_events").get == count
    prepared = client.post(path + "/prepare", json=body, headers=headers(client))
    assert prepared.status_code == 201, prepared.text
    plan = prepared.json()
    assert plan["status"] == "prepared" and plan["context_valid"] is True
    assert plan["usable"] is False and plan["reason_code"] is None
    assert plan["plan"]["cpu_count"] == environment["cpu_count"]
    assert plan["plan"]["boot"] is False and plan["plan"]["network"] == "none"
    assert plan["plan"]["mode"] == "create_stopped_hardware"
    assert not {"credential", "path", "vm_id", "owner_id"} & plan["plan"].keys()
    assert credential not in prepared.text
    assert client.post(path + "/prepare", json=body, headers=headers(client)).json() == plan
    command = decision(plan)
    route = path + "/" + plan["plan_id"]
    authorized = client.post(route + "/authorize", json=command, headers=headers(client))
    assert authorized.status_code == 200, authorized.text
    approved = authorized.json()
    assert approved["status"] == "authorized" and approved["authorization_expires_at"]
    assert (
        client.post(route + "/authorize", json=command, headers=headers(client)).json() == approved
    )
    # Nenhuma aprovação/consulta deve despachar job ou criar credencial de executor.
    with client.app.state.database.transaction(write=False) as connection:
        assert connection.execute("SELECT count(*) FROM provisioning_credentials").get == 0
        assert connection.execute("SELECT count(*) FROM provisioning_effects").get == 0
        assert connection.execute("SELECT status FROM host_jobs").get == "awaiting_host"
    with TestClient(create_app(state[4]), base_url=ORIGIN) as restarted:
        restarted.cookies.update(client.cookies)
        assert restarted.get(path).json()["plan"] == approved
        revoke = decision(approved)
        revoked = restarted.post(route + "/revoke", json=revoke, headers=headers(restarted))
        assert revoked.status_code == 200, revoked.text
        assert revoked.json()["status"] == "revoked"
        assert (
            restarted.post(route + "/revoke", json=revoke, headers=headers(restarted)).json()
            == revoked.json()
        )
        assert (
            restarted.post(
                route + "/authorize", json=decision(revoked.json()), headers=headers(restarted)
            ).status_code
            == 409
        )


def test_ownership_csrf_replay_and_diagnostic_bearer_are_not_authority(state):
    client, path, body, _, credential, _ = context(state)
    assert client.post(path + "/prepare", json=body).status_code == 403
    plan = client.post(path + "/prepare", json=body, headers=headers(client)).json()
    foreign = path.replace(str(state[1].id), str(state[2].id))
    assert client.get(foreign).status_code == 404
    assert client.post(foreign + "/prepare", json=body, headers=headers(client)).status_code in (
        404,
        409,
    )
    for operation in ("authorize", "revoke"):
        route = f"/{plan['plan_id']}/{operation}"
        assert (
            client.post(foreign + route, json=decision(plan), headers=headers(client)).status_code
            == 404
        )
        assert client.post(path + route, json=decision(plan)).status_code == 403
        assert (
            client.post(
                path + route,
                json=decision(plan),
                headers=headers(client) | {"Origin": "https://external.example"},
            ).status_code
            == 403
        )
    changed = body | {"expected_environment_revision": 999}
    assert client.post(path + "/prepare", json=changed, headers=headers(client)).status_code == 409
    command = decision(plan)
    route = path + "/" + plan["plan_id"] + "/authorize"
    assert client.post(route, json=command, headers=headers(client)).status_code == 200
    assert (
        client.post(
            route, json=command | {"plan_hash": "0" * 64}, headers=headers(client)
        ).status_code
        == 409
    )
    assert client.post(route, json=decision(plan), headers=headers(client)).status_code == 409
    client.cookies.clear()
    assert client.get(path, headers=runtime_headers(credential)).status_code == 401
    assert client.post(route, json=command, headers=runtime_headers(credential)).status_code == 401


def test_context_change_is_visible_and_cleanup_remains_possible(state):
    client, path, body, host, _, _ = context(state)
    plan = client.post(path + "/prepare", json=body, headers=headers(client)).json()
    route = path + "/" + plan["plan_id"]
    client.app.state.provisioning_catalog = DEFAULT_PROVISIONING_CATALOG.model_copy(
        update={"image_iso": ArtifactPin(sha256="1" * 64, size=123)}
    )
    stale = client.get(path).json()["plan"]
    assert stale["context_valid"] is False and stale["reason_code"] == "catalog_changed"
    assert (
        client.post(route + "/authorize", json=decision(plan), headers=headers(client)).status_code
        == 409
    )
    assert (
        client.post(route + "/revoke", json=decision(plan), headers=headers(client)).status_code
        == 200
    )
    client.app.state.provisioning_catalog = DEFAULT_PROVISIONING_CATALOG
    body["client_request_id"] = str(uuid4())
    new_plan = client.post(path + "/prepare", json=body, headers=headers(client)).json()
    revoked_host = client.post(
        f"/api/v1/environments/hosts/{host['host_id']}/revoke",
        json={"expected_revision": host["revision"], "client_request_id": str(uuid4())},
        headers=headers(client),
    )
    assert revoked_host.status_code == 200
    overview = client.get(path).json()
    assert overview["host"] is None and overview["preparation_available"] is False
    assert overview["plan"]["context_valid"] is False
    assert overview["plan"]["reason_code"] == "host_changed"
    assert (
        client.post(
            path + "/" + new_plan["plan_id"] + "/revoke",
            json=decision(new_plan),
            headers=headers(client),
        ).status_code
        == 200
    )


@pytest.mark.parametrize(
    "extra",
    [
        {"boot": True},
        {"network": "default"},
        {"shell": "secret-script"},
        {"host_credential": "private-token"},
        {"image_url": "https://private.example"},
    ],
)
def test_human_payload_cannot_inject_execution_parameters(state, extra):
    client, path, body, _, _, _ = context(state)
    result = client.post(path + "/prepare", json=body | extra, headers=headers(client))
    assert result.status_code == 422
    assert "private-token" not in result.text and "private.example" not in result.text
    assert client.get(path).json()["plan"] is None


def test_missing_catalog_and_runtime_routes_do_not_activate_executor(state):
    client, path, body, _, _, _ = context(state)
    client.app.state.provisioning_catalog = None
    assert client.get(path).json()["preparation_available"] is False
    assert client.post(path + "/prepare", json=body, headers=headers(client)).status_code == 409
    for route in (
        "/api/v1/provisioning/runtime/claim",
        "/api/v1/provisioning/runtime/dispatch",
        path + "/issue-provisioner",
    ):
        assert client.post(route, json={}, headers=headers(client)).status_code in (404, 405)
