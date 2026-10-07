"""Autoridade HTTP distinta, reavaliação durável e nenhum executor de hardware."""

import secrets
import time
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from test_host_links_api import runtime_headers
from test_provisioning_api import context, decision
from test_tasks import ORIGIN, headers
from test_tasks import state as state

from bees_api.app import create_app
from bees_core.provisioning import ProvisioningService

PREFIX = "/api/v1/provisioner/runtime"
COMMANDS = ("claim", "current", "renew", "begin", "receipt", "unknown")


def prepared(state):
    client, path, body, host, diagnostic, environment = context(state)
    report = client.post(
        "/api/v1/host-link/runtime/report",
        json={
            "host_id": host["host_id"],
            "sequence": 1,
            "expected_revision": host["report_revision"],
            "client_request_id": str(uuid4()),
            "driver": "hyperv",
            "probe": {"platform": True, "module": True, "service": True},
        },
        headers=runtime_headers(diagnostic),
    )
    assert report.status_code == 200, report.text
    plan = client.post(path + "/prepare", json=body, headers=headers(client)).json()
    response = client.post(
        path + "/" + plan["plan_id"] + "/authorize",
        json=decision(plan),
        headers=headers(client),
    )
    assert response.status_code == 200, response.text
    service = ProvisioningService(client.app.state.database)
    issued = service.issue_provisioner(
        UUID(host["host_id"]),
        {"expected_host_revision": host["revision"], "client_request_id": uuid4()},
    )
    return client, path, response.json(), host, issued, diagnostic, environment, service


def command(plan):
    return {
        "plan_id": plan["plan_id"],
        "plan_hash": plan["plan_hash"],
        "owner_id": str(uuid4()),
        "client_request_id": str(uuid4()),
    }


def binding(claim):
    return {key: claim[key] for key in ("claim_id", "owner_id", "generation")}


def post(client, operation, body, issued):
    return client.post(
        PREFIX + "/" + operation,
        json=body,
        headers=runtime_headers(issued.credential.get_secret_value()),
    )


@pytest.mark.parametrize("operation", COMMANDS)
def test_human_cookie_diagnostic_missing_invalid_and_duplicate_bearer_cannot_authenticate(
    state, operation
):
    client, _, _, _, issued, diagnostic, _, _ = prepared(state)
    token = issued.credential.get_secret_value()
    alternatives = (
        {"Origin": ORIGIN},
        runtime_headers(diagnostic),
        runtime_headers("bp_" + secrets.token_urlsafe(32)),
        runtime_headers(token + " extra"),
        [
            ("Origin", ORIGIN),
            ("Authorization", "Bearer " + token),
            ("Authorization", "Bearer " + diagnostic),
        ],
    )
    for request_headers in alternatives:
        response = client.post(PREFIX + "/" + operation, json={}, headers=request_headers)
        assert response.status_code == 401, response.text
        assert response.json()["error"]["code"] == "provisioning_credentials_invalid"
        assert token not in response.text and diagnostic not in response.text
    # DTO inválido só aparece após autenticar a autoridade específica.
    assert post(client, operation, {}, issued).status_code == 422


def test_session_scopes_secrets_and_human_endpoints_remain_separate(state):
    client, path, _, host, issued, _, _, _ = prepared(state)
    token = issued.credential.get_secret_value()
    client.cookies.clear()
    session = client.get(PREFIX + "/session", headers=runtime_headers(token))
    assert session.status_code == 200, session.text
    assert session.json() == {
        "provisioner_id": str(issued.provisioner_id),
        "installation_id": str(issued.installation_id),
        "host_id": host["host_id"],
        "revision": 1,
        "status": "active",
    }
    assert session.headers["cache-control"] == "no-store"
    assert token not in session.text
    assert client.get(path, headers=runtime_headers(token)).status_code == 401
    assert (
        client.get("/api/v1/host-link/runtime/session", headers=runtime_headers(token)).status_code
        == 401
    )
    assert client.get(PREFIX + "/session").status_code == 401


def test_reissued_credential_cannot_take_over_existing_claim_or_replay(state):
    client, _, plan, host, issued, _, _, service = prepared(state)
    value = command(plan)
    claim = post(client, "claim", value, issued).json()
    service.revoke_provisioner(
        issued.provisioner_id,
        {"expected_revision": issued.revision, "client_request_id": uuid4()},
    )
    for operation in COMMANDS:
        assert post(client, operation, {}, issued).status_code == 401
    assert (
        client.get(
            PREFIX + "/session", headers=runtime_headers(issued.credential.get_secret_value())
        ).status_code
        == 401
    )
    replacement = service.issue_provisioner(
        UUID(host["host_id"]),
        {"expected_host_revision": host["revision"], "client_request_id": uuid4()},
    )
    assert post(client, "claim", value, replacement).status_code == 409
    assert post(client, "claim", command(plan), replacement).status_code == 409
    for operation, extra in (
        ("current", {}),
        ("renew", {"client_request_id": str(uuid4())}),
        ("begin", {"client_request_id": str(uuid4()), "operation": "create_vhd"}),
    ):
        result = post(client, operation, binding(claim) | extra, replacement)
        assert result.status_code == 409, result.text
        assert result.json()["error"]["code"] == "provisioning_claim_fenced"
    with client.app.state.database.transaction(write=False) as connection:
        assert connection.execute("SELECT count(*) FROM provisioning_effects").get == 0


def test_restart_keeps_claim_and_intent_but_never_renews_dispatch_on_replay(state):
    client, _, plan, _, issued, _, _, _ = prepared(state)
    value = command(plan)
    claim = post(client, "claim", value, issued).json()
    start = binding(claim) | {"operation": "create_vhd", "client_request_id": str(uuid4())}
    effect = post(client, "begin", start, issued).json()
    assert effect["dispatch_allowed"] is True
    with TestClient(create_app(state[4]), base_url=ORIGIN) as restarted:
        assert post(restarted, "claim", value, issued).json()["claim_id"] == claim["claim_id"]
        observed = post(restarted, "begin", start, issued)
        assert observed.status_code == 200, observed.text
        assert observed.json()["effect_request_id"] == effect["effect_request_id"]
        assert observed.json()["cached"] is True
        assert observed.json()["dispatch_allowed"] is False
        assert observed.json()["claim"]["lease_expires_at"] == claim["lease_expires_at"]


def test_claim_renew_intent_receipt_replay_and_unknown_are_durable(state):
    client, path, plan, _, issued, _, _, _ = prepared(state)
    client.cookies.clear()
    value = command(plan)
    claimed = post(client, "claim", value, issued)
    assert claimed.status_code == 200, claimed.text
    claim = claimed.json()
    assert claim["plan"] == plan["plan"] and claim["status"] == "claimed"
    assert post(client, "claim", value, issued).json() == claim
    assert post(client, "claim", value | {"owner_id": str(uuid4())}, issued).status_code == 409
    assert post(client, "current", binding(claim), issued).json() == claim
    renewal = binding(claim) | {"client_request_id": str(uuid4())}
    renewed = post(client, "renew", renewal, issued)
    assert renewed.status_code == 200, renewed.text
    assert post(client, "renew", renewal, issued).json() == renewed.json()
    start = binding(claim) | {"operation": "create_vhd", "client_request_id": str(uuid4())}
    begun = post(client, "begin", start, issued)
    assert begun.status_code == 200, begun.text
    effect = begun.json()
    assert effect["dispatch_allowed"] is True and effect["cached"] is False
    replay = post(client, "begin", start, issued).json()
    assert replay["dispatch_allowed"] is False and replay["cached"] is True
    receipt = binding(claim) | {
        "effect_request_id": effect["effect_request_id"],
        "client_request_id": str(uuid4()),
        "result": {"vm_id": None, "verified": True},
    }
    confirmed = post(client, "receipt", receipt, issued)
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["status"] == "confirmed"
    assert confirmed.json()["dispatch_allowed"] is False
    assert post(client, "receipt", receipt, issued).json()["cached"] is True
    conflict = receipt | {"result": {"vm_id": None, "verified": False}}
    assert post(client, "receipt", conflict, issued).status_code == 409
    started_vm = post(
        client,
        "begin",
        binding(claim) | {"operation": "create_vm", "client_request_id": str(uuid4())},
        issued,
    )
    assert started_vm.status_code == 200, started_vm.text
    lost = binding(claim) | {
        "effect_request_id": started_vm.json()["effect_request_id"],
        "client_request_id": str(uuid4()),
    }
    unknown = post(client, "unknown", lost, issued)
    assert unknown.status_code == 200, unknown.text
    assert unknown.json()["status"] == "outcome_unknown"
    assert post(client, "unknown", lost, issued).json() == unknown.json()
    assert post(client, "current", binding(claim), issued).status_code == 409
    assert post(client, "claim", command(plan), issued).status_code == 409
    with client.app.state.database.transaction(write=False) as connection:
        assert connection.execute("SELECT count(*) FROM provisioning_effects").get == 2
        assert connection.execute("SELECT status FROM environments").get == "outcome_unknown"
        assert connection.execute("SELECT count(*) FROM provisioning_vm_bindings").get == 0
    assert (
        client.get(path, headers=runtime_headers(issued.credential.get_secret_value())).status_code
        == 401
    )


@pytest.mark.parametrize("change", ["owner_id", "generation", "claim_id"])
def test_foreign_claim_bindings_fail_before_intent(state, change):
    client, _, plan, _, issued, _, _, _ = prepared(state)
    claim = post(client, "claim", command(plan), issued).json()
    foreign = binding(claim) | {
        change: claim["generation"] + 1 if change == "generation" else str(uuid4())
    }
    for operation, extra in (
        ("current", {}),
        ("renew", {"client_request_id": str(uuid4())}),
        ("begin", {"client_request_id": str(uuid4()), "operation": "create_vhd"}),
    ):
        assert post(client, operation, foreign | extra, issued).status_code in (404, 409)
    with client.app.state.database.transaction(write=False) as connection:
        assert connection.execute("SELECT count(*) FROM provisioning_effects").get == 0


@pytest.mark.parametrize("change", ["lease", "report", "authorization", "credential", "host"])
def test_stale_or_revoked_authority_cannot_begin(state, change, monkeypatch):
    client, path, plan, host, issued, _, _, service = prepared(state)
    claim = post(client, "claim", command(plan), issued).json()
    if change in ("lease", "report"):
        with client.app.state.database.transaction() as connection:
            if change == "lease":
                connection.execute(
                    "UPDATE provisioning_claims SET lease_expires_at=created_at+0.001"
                )
            else:
                connection.execute("UPDATE host_links SET last_seen=1")
        if change == "lease":
            later = time.time() + 1
            monkeypatch.setattr(ProvisioningService, "_now", lambda self: later)
    elif change == "authorization":
        assert (
            client.post(
                path + "/" + plan["plan_id"] + "/revoke",
                json=decision(plan),
                headers=headers(client),
            ).status_code
            == 200
        )
    elif change == "credential":
        service.revoke_provisioner(
            issued.provisioner_id,
            {"expected_revision": issued.revision, "client_request_id": uuid4()},
        )
    else:
        assert (
            client.post(
                "/api/v1/environments/hosts/" + host["host_id"] + "/revoke",
                json={"expected_revision": host["revision"], "client_request_id": str(uuid4())},
                headers=headers(client),
            ).status_code
            == 200
        )
    response = post(
        client,
        "begin",
        binding(claim) | {"operation": "create_vhd", "client_request_id": str(uuid4())},
        issued,
    )
    assert response.status_code == (401 if change == "credential" else 409), response.text
    with client.app.state.database.transaction(write=False) as connection:
        assert connection.execute("SELECT count(*) FROM provisioning_effects").get == 0


def test_middleware_and_closed_contracts_remain_enforced(state):
    client, _, plan, _, issued, _, _, _ = prepared(state)
    token = issued.credential.get_secret_value()
    value = command(plan)
    for request_headers, status in (
        ({"Authorization": "Bearer " + token}, 403),
        (runtime_headers(token) | {"Origin": "https://foreign.example"}, 403),
        (runtime_headers(token) | {"Host": "foreign.example"}, 400),
    ):
        response = client.post(PREFIX + "/claim", json=value, headers=request_headers)
        assert response.status_code == status, response.text
    for extra in ({"shell": "private script"}, {"boot": True}, {"file": "/secret"}):
        response = post(client, "claim", value | extra, issued)
        assert response.status_code == 422
        assert "private script" not in response.text and "/secret" not in response.text
    response = client.post(
        PREFIX + "/claim",
        content=b"x" * 65537,
        headers=runtime_headers(token) | {"Content-Type": "application/json"},
    )
    assert response.status_code == 413
    for operation in ("issue", "bootstrap", "shell", "recover", "acknowledge", "execute"):
        assert post(client, operation, {}, issued).status_code in (404, 405)
    assert post(client, "claim", value, issued).status_code == 200
    # Bearer não flexibiliza a guarda CSRF de mutações humanas.
    assert (
        client.post(
            "/api/v1/agents/" + str(state[1].id) + "/environments",
            json={"name": "Computador", "client_request_id": str(uuid4())},
            headers=runtime_headers(token),
        ).status_code
        == 403
    )
