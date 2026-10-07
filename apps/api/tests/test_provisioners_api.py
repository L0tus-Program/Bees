"""Gestão humana restrita de cadastros; tokens privados nunca chegam à interface."""

from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from test_host_links_api import pairing, runtime_headers
from test_provisioner_runtime_api import prepared
from test_tasks import ORIGIN, headers
from test_tasks import state as state

from bees_api.app import create_app


def fixture_provisioner(state):
    client, _, _, host, issued, diagnostic, _, service = prepared(state)
    path = f"/api/v1/environments/hosts/{host['host_id']}/provisioners"
    return client, path, host, issued, diagnostic, service


def revoke_body(revision=1):
    return {"expected_revision": revision, "client_request_id": str(uuid4())}


def test_list_readonly_empty_then_private_issue_safe_snapshots(state):
    client, path, host, issued, diagnostic, service = fixture_provisioner(state)
    with client.app.state.database.transaction(write=False) as connection:
        before = connection.execute("SELECT count(*) FROM domain_events").get
    for _ in range(2):
        response = client.get(path)
        assert response.status_code == 200, response.text
        assert response.headers["cache-control"] == "no-store"
        assert response.json() == {
            "provisioners": [
                {
                    "provisioner_id": str(issued.provisioner_id),
                    "installation_id": str(issued.installation_id),
                    "host_id": host["host_id"],
                    "status": "active",
                    "revision": 1,
                }
            ],
            "has_more": False,
            "next_offset": None,
        }
        assert (
            issued.credential.get_secret_value() not in response.text
            and diagnostic not in response.text
        )
    with client.app.state.database.transaction(write=False) as connection:
        assert connection.execute("SELECT count(*) FROM domain_events").get == before
        assert connection.execute("SELECT count(*) FROM provisioning_credentials").get == 1
        assert connection.execute("SELECT count(*) FROM provisioning_effects").get == 0
    other, _, _ = pairing(client)
    assert (
        client.get(f"/api/v1/environments/hosts/{other['host_id']}/provisioners").json()[
            "provisioners"
        ]
        == []
    )


def test_revoke_cas_replay_terminal_and_scope_are_human_only(state):
    client, path, host, issued, diagnostic, service = fixture_provisioner(state)
    url = path + f"/{issued.provisioner_id}/revoke"
    body = revoke_body()
    wrong_revision = client.post(url, json=body | {"expected_revision": 9}, headers=headers(client))
    assert wrong_revision.status_code == 409
    other, _, _ = pairing(client)
    wrong_path = (
        f"/api/v1/environments/hosts/{other['host_id']}/provisioners/{issued.provisioner_id}/revoke"
    )
    assert client.post(wrong_path, json=body, headers=headers(client)).status_code == 404
    assert service.session(issued.credential.get_secret_value())["status"] == "active"
    response = client.post(url, json=body, headers=headers(client))
    assert response.status_code == 200 and response.json()["status"] == "revoked"
    assert response.json()["revision"] == 2
    assert client.post(url, json=body, headers=headers(client)).json() == response.json()
    assert client.post(wrong_path, json=body, headers=headers(client)).status_code == 404
    assert client.post(url, json=revoke_body(2), headers=headers(client)).status_code == 409
    assert client.get(path).json()["provisioners"][0] == response.json()
    assert (
        diagnostic not in response.text
        and issued.credential.get_secret_value() not in response.text
    )


def test_pagination_and_pending_host_isolation(state):
    client, path, host, issued, _, service = fixture_provisioner(state)
    ids = [str(issued.provisioner_id)]
    for _ in range(3):
        service.revoke_provisioner(issued.provisioner_id, revoke_body())
        issued = service.issue_provisioner(
            UUID(host["host_id"]),
            {
                "expected_host_revision": host["revision"],
                "client_request_id": uuid4(),
            },
        )
        ids.append(str(issued.provisioner_id))
    first = client.get(path, params={"limit": 2}).json()
    second = client.get(path, params={"limit": 2, "offset": first["next_offset"]}).json()
    assert first["has_more"] and first["next_offset"] == 2
    assert not second["has_more"] and second["next_offset"] is None
    assert {row["provisioner_id"] for row in first["provisioners"] + second["provisioners"]} == set(
        ids
    )
    assert client.get(path, params={"offset": 99}).json()["provisioners"] == []


@pytest.mark.parametrize(
    "params", [{"offset": -1}, {"offset": 1000001}, {"limit": 0}, {"limit": 101}]
)
def test_pagination_bounds_rejected(state, params):
    client, path, _, _, _, _ = fixture_provisioner(state)
    assert client.get(path, params=params).status_code == 422


def test_unknown_host_and_provisioner_not_found(state):
    client, path, _, _, _, _ = fixture_provisioner(state)
    assert client.get(f"/api/v1/environments/hosts/{uuid4()}/provisioners").status_code == 404
    assert (
        client.post(
            path + f"/{uuid4()}/revoke", json=revoke_body(), headers=headers(client)
        ).status_code
        == 404
    )


def test_host_revoked_listing_and_cleanup_survive_service_restart(state):
    client, path, host, issued, _, _ = fixture_provisioner(state)
    response = client.post(
        f"/api/v1/environments/hosts/{host['host_id']}/revoke",
        json={"expected_revision": host["revision"], "client_request_id": str(uuid4())},
        headers=headers(client),
    )
    assert response.status_code == 200
    assert client.get(path).json()["provisioners"][0]["status"] == "active"
    with TestClient(create_app(state[4]), base_url=ORIGIN) as restarted:
        restarted.cookies.update(client.cookies)
        assert restarted.get(path).json() == client.get(path).json()
        revoked = restarted.post(
            path + f"/{issued.provisioner_id}/revoke",
            json=revoke_body(),
            headers=headers(restarted),
        )
        assert revoked.status_code == 200 and revoked.json()["status"] == "revoked"
    assert client.get(path).json()["provisioners"][0]["status"] == "revoked"


def test_no_emission_bootstrap_execution_or_mutable_provisioner_payloads(state):
    client, path, _, issued, _, _ = fixture_provisioner(state)
    for suffix in ("", "/issue", "/bootstrap", "/claim", "/execute", "/retry"):
        response = client.post(path + suffix, json={}, headers=headers(client))
        assert response.status_code in (404, 405)
    for field in ("token", "credential_hash", "host_id", "owner_id", "status"):
        response = client.post(
            path + f"/{issued.provisioner_id}/revoke",
            json=revoke_body() | {field: "foreign"},
            headers=headers(client),
        )
        assert response.status_code == 422 and "foreign" not in response.text


def test_missing_session_bearer_domains_csrf_and_origin_cannot_revoke(state):
    client, path, _, issued, diagnostic, service = fixture_provisioner(state)
    url = path + f"/{issued.provisioner_id}/revoke"
    body = revoke_body()
    assert client.post(url, json=body).status_code == 403
    assert client.post(url, json=body, headers={"Origin": ORIGIN}).status_code == 403
    assert (
        client.post(
            url, json=body, headers=headers(client) | {"Origin": "https://foreign.invalid"}
        ).status_code
        == 403
    )
    client.cookies.clear()
    for credential in (diagnostic, issued.credential.get_secret_value()):
        assert client.get(path, headers=runtime_headers(credential)).status_code == 401
        assert client.post(url, json=body, headers=runtime_headers(credential)).status_code in (
            401,
            403,
        )
    assert service.session(issued.credential.get_secret_value())["status"] == "active"


def test_revoke_live_claim_blocks_guard_without_replaying_effect(state):
    client, path, _, issued, _, service = fixture_provisioner(state)
    plans = client.app.state.database
    with plans.transaction(write=False) as connection:
        plan_id, plan_hash = connection.execute(
            "SELECT id,plan_hash FROM provisioning_plans"
        ).fetchone()
    token = issued.credential.get_secret_value()
    claim = service.claim(
        token,
        {
            "plan_id": plan_id,
            "plan_hash": plan_hash,
            "owner_id": uuid4(),
            "client_request_id": uuid4(),
        },
    )
    binding = {key: claim[key] for key in ("claim_id", "owner_id", "generation")}
    effect = service.begin_dispatch(
        token, binding | {"operation": "create_vhd", "client_request_id": uuid4()}
    )
    response = client.post(
        path + f"/{issued.provisioner_id}/revoke", json=revoke_body(), headers=headers(client)
    )
    assert response.status_code == 200
    from bees_core.provisioning import ProvisioningError

    with pytest.raises(ProvisioningError, match="credentials_invalid"):
        service.assert_current(token, binding)
    with plans.transaction(write=False) as connection:
        assert (
            connection.execute(
                "SELECT status FROM provisioning_effects WHERE effect_request_id=?",
                (effect["effect_request_id"],),
            ).get
            == "dispatch_started"
        )
        assert connection.execute("SELECT count(*) FROM provisioning_effects").get == 1
