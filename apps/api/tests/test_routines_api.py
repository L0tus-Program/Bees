"""Agenda humana sob sessão, origem confiável, CSRF, UUID e CAS."""

from uuid import uuid4

from fastapi.testclient import TestClient
from test_global_stop_api import ORIGIN, headers
from test_global_stop_api import state as state
from test_skills_api import definition as skill_definition

from bees_core.models import Conversation
from bees_core.storage.store import StateStore


def routine_body(client, agent):
    skill = client.post(
        f"/api/v1/agents/{agent.id}/skills",
        json={"client_request_id": str(uuid4()), "definition": skill_definition()},
        headers=headers(client),
    ).json()
    with StateStore(client.app.state.database).transaction() as unit:
        conversation = unit.conversations.create(Conversation(agent_id=agent.id))
    return {
        "client_request_id": str(uuid4()),
        "definition": {
            "name": "Resumo diário",
            "instructions": "Resumir fontes para revisão",
            "schedule": {"frequency": "daily", "time": "09:00"},
            "timezone": "America/Sao_Paulo",
            "skill_id": skill["id"],
            "skill_version": 1,
            "inputs": {},
            "environment": "none",
            "destination": {"kind": "conversation", "conversation_id": str(conversation.id)},
            "notification": "always",
        },
    }


def test_routines_require_session_csrf_host_origin_and_closed_contract(state):
    client, agent, _ = state
    path = f"/api/v1/agents/{agent.id}/routines"
    anonymous = TestClient(client.app, base_url=ORIGIN)
    assert anonymous.get(path).status_code == 401
    assert client.get(path).json() == []
    body = routine_body(client, agent)
    assert client.post(path, json=body, headers={"Origin": ORIGIN}).status_code in (401, 403)
    for bad in (
        {"client_request_id": "bad"},
        {"client_request_id": "00000000-0000-0000-0000-000000000000"},
        {"origin": "model"},
        {"definition": body["definition"] | {"misfire_policy": "catch_up"}},
        {"definition": body["definition"] | {"overlap_policy": "queue"}},
        {"definition": body["definition"] | {"timezone": "not-a-timezone"}},
    ):
        assert client.post(path, json=body | bad, headers=headers(client)).status_code == 422
    for hostile in ({"Origin": "https://attacker.example"}, {"Host": "attacker.example"}):
        assert client.post(path, json=body, headers=headers(client) | hostile).status_code in (
            400,
            403,
        )
    created = client.post(path, json=body, headers=headers(client))
    assert created.status_code == 200, created.text
    assert created.json()["status"] == "paused"
    assert created.json()["origin"] == "human"
    assert client.post(path, json=body, headers=headers(client)).json() == created.json()
    divergent = body | {"definition": body["definition"] | {"name": "Outra intenção"}}
    assert client.post(path, json=divergent, headers=headers(client)).status_code == 409
    for query in ("limit=0", "limit=101", "offset=-1"):
        assert client.get(path + "?" + query).status_code == 422
    assert client.get(f"/api/v1/agents/{uuid4()}/routines").status_code == 404
    assert (
        client.get(f"/api/v1/agents/{uuid4()}/routines/{created.json()['id']}").status_code == 404
    )


def test_routine_activation_cas_edit_pause_and_reads_never_create_tasks(state):
    client, agent, _ = state
    path = f"/api/v1/agents/{agent.id}/routines"
    body = routine_body(client, agent)
    first = client.post(path, json=body, headers=headers(client)).json()
    target = path + "/" + first["id"]
    activate = {"client_request_id": str(uuid4()), "expected_revision": 1, "status": "active"}
    # Saving an agenda cannot implicitly enable its selected skill.
    assert client.put(target + "/status", json=activate, headers=headers(client)).status_code == 409
    skill_id = body["definition"]["skill_id"]
    enable = {
        "client_request_id": str(uuid4()),
        "expected_revision": 1,
        "version": 1,
        "enabled": True,
    }
    assert (
        client.put(
            f"/api/v1/agents/{agent.id}/skills/{skill_id}/enabled",
            json=enable,
            headers=headers(client),
        ).status_code
        == 200
    )
    assert client.put(
        target + "/status", json=activate, headers={"Origin": ORIGIN}
    ).status_code in (401, 403)
    enabled = client.put(target + "/status", json=activate, headers=headers(client)).json()
    assert enabled["status"] == "active" and enabled["revision"] == 2
    assert enabled["next_run_at"] is not None
    assert client.put(target + "/status", json=activate, headers=headers(client)).json() == enabled
    stale = activate | {"client_request_id": str(uuid4()), "status": "paused"}
    assert client.put(target + "/status", json=stale, headers=headers(client)).status_code == 409
    revised = client.post(
        target + "/revisions",
        json={
            "client_request_id": str(uuid4()),
            "expected_revision": 2,
            "definition": body["definition"] | {"name": "Agenda revisada"},
        },
        headers=headers(client),
    ).json()
    assert revised["status"] == "paused" and revised["revision"] == 3
    assert revised["next_run_at"] is None
    for _ in range(3):
        assert client.get(target).json() == revised
        assert client.get(target + "/occurrences").json() == []
    with client.app.state.database.transaction(write=False) as connection:
        assert connection.execute("SELECT count(*) FROM tasks").get == 0
        assert connection.execute("SELECT count(*) FROM routine_occurrences").get == 0


def test_skill_input_and_environment_mismatch_are_safe_422_with_no_write(state):
    client, agent, _ = state
    path = f"/api/v1/agents/{agent.id}/routines"
    body = routine_body(client, agent)
    for definition in (
        body["definition"] | {"inputs": {"unexpected": "PRIVATE_SENTINEL"}},
        body["definition"] | {"environment": "own"},
    ):
        response = client.post(
            path, json=body | {"definition": definition}, headers=headers(client)
        )
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "routine_input_invalid"
        assert "PRIVATE_SENTINEL" not in response.text
    first = client.post(path, json=body, headers=headers(client)).json()
    response = client.post(
        path + "/" + first["id"] + "/revisions",
        json={
            "client_request_id": str(uuid4()),
            "expected_revision": first["revision"],
            "definition": body["definition"] | {"inputs": {"unexpected": "PRIVATE_SENTINEL"}},
        },
        headers=headers(client),
    )
    assert response.status_code == 422 and "PRIVATE_SENTINEL" not in response.text
    assert client.get(path + "/" + first["id"]).json() == first
    with client.app.state.database.transaction(write=False) as connection:
        assert connection.execute("SELECT count(*) FROM routines").get == 1
        assert connection.execute("SELECT count(*) FROM routine_commands").get == 1
        assert connection.execute("SELECT count(*) FROM routine_definitions").get == 1
