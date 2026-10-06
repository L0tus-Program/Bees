"""Convite é autenticação própria, confirmação humana e escopo diagnóstico fechado."""

import secrets
from uuid import uuid4

from test_tasks import ORIGIN, headers
from test_tasks import state as state

from bees_core.security.hosts import HostService


def pairing(client):
    invite = HostService(client.app.state.database).issue_invite(uuid4())
    credential = "bh_" + secrets.token_urlsafe(32)
    body = {
        "installation_id": str(invite.installation_id),
        "invite_token": invite.invite_token.get_secret_value(),
        "host_id": str(uuid4()),
        "host_credential": credential,
        "client_request_id": str(uuid4()),
    }
    response = client.post(
        "/api/v1/host-link/runtime/exchange", json=body, headers={"Origin": ORIGIN}
    )
    assert response.status_code == 200, response.text
    return response.json(), credential, body


def runtime_headers(credential):
    return {"Origin": ORIGIN, "Authorization": "Bearer " + credential}


def test_ticket_authentication_domain_separation_and_human_confirmation(state):
    client, _, _, _, _ = state
    host, credential, body = pairing(client)
    assert host["status"] == "pending" and host["confirmable"] is True
    assert host["diagnostic"] is None and host["online"] is False
    assert credential not in str(host) and body["invite_token"] not in str(host)
    path = "/api/v1/environments/hosts/" + host["host_id"]
    assert (
        client.post(
            "/api/v1/host-link/runtime/exchange", json=body, headers={"Origin": ORIGIN}
        ).json()
        == host
    )
    assert client.get("/api/v1/host-link/runtime/session").status_code == 401
    assert (
        client.get("/api/v1/host-link/runtime/session", headers=runtime_headers(credential)).json()[
            "status"
        ]
        == "pending"
    )
    command = {
        "expected_revision": host["revision"],
        "client_request_id": str(uuid4()),
        "fingerprint": host["fingerprint"],
    }
    assert client.post(path + "/confirm", json=command).status_code == 403
    bad_code = command | {"fingerprint": "0000-0000-0000-0000"}
    assert client.post(path + "/confirm", json=bad_code, headers=headers(client)).status_code == 409
    confirmed = client.post(path + "/confirm", json=command, headers=headers(client))
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["status"] == "active" and confirmed.json()["provisionable"] is False
    assert (
        client.post(path + "/confirm", json=command, headers=headers(client)).json()
        == confirmed.json()
    )
    # Um bearer de host não substitui sessão nas rotas humanas.
    client.cookies.clear()
    assert (
        client.get("/api/v1/environments/hosts", headers=runtime_headers(credential)).status_code
        == 401
    )
    assert client.get("/api/v1/onboarding", headers=runtime_headers(credential)).status_code == 401
    assert (
        client.get(
            "/api/v1/host-link/runtime/session", headers=runtime_headers(credential)
        ).status_code
        == 200
    )


def test_report_replay_revocation_origin_and_closed_contract(state):
    client, _, _, _, _ = state
    host, credential, _ = pairing(client)
    runtime = runtime_headers(credential)
    report = {
        "host_id": host["host_id"],
        "sequence": 1,
        "expected_revision": host["report_revision"],
        "client_request_id": str(uuid4()),
        "driver": "hyperv",
        "probe": {"platform": True, "module": False, "service": False},
    }
    endpoint = "/api/v1/host-link/runtime/report"
    assert client.post(endpoint, json=report, headers=runtime).status_code == 401
    path = "/api/v1/environments/hosts/" + host["host_id"]
    active = client.post(
        path + "/confirm",
        json={
            "expected_revision": host["revision"],
            "client_request_id": str(uuid4()),
            "fingerprint": host["fingerprint"],
        },
        headers=headers(client),
    ).json()
    sent = client.post(endpoint, json=report, headers=runtime)
    assert sent.status_code == 200, sent.text
    assert (
        sent.json()["online"] is True
        and sent.json()["diagnostic"]["status"] == "driver_unavailable"
    )
    assert sent.json()["provisionable"] is False
    assert client.post(endpoint, json=report, headers=runtime).json() == sent.json()
    assert client.post(endpoint, json=report | {"sequence": 2}, headers=runtime).status_code == 409
    assert (
        client.post(
            endpoint, json=report, headers=runtime | {"Origin": "https://external.example"}
        ).status_code
        == 403
    )
    assert (
        client.post(
            endpoint,
            json=report | {"probe": report["probe"] | {"secret": "private-test-value"}},
            headers=runtime,
        ).status_code
        == 422
    )
    assert (
        client.post(endpoint, json=report | {"operation": "create_vm"}, headers=runtime).status_code
        == 422
    )
    revoked = client.post(
        path + "/revoke",
        json={
            "expected_revision": active["revision"],
            "client_request_id": str(uuid4()),
        },
        headers=headers(client),
    )
    assert revoked.status_code == 200, revoked.text
    assert revoked.json()["status"] == "revoked" and revoked.json()["online"] is False
    assert client.post(endpoint, json=report, headers=runtime).status_code == 401
    assert client.get("/api/v1/host-link/runtime/session", headers=runtime).status_code == 401
    assert client.get("/api/v1/environments/host").json()["provisionable"] is False
