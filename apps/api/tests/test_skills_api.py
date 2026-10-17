"""Sessão humana, isolamento de abelha, importação fechada e histórico da biblioteca."""

from uuid import uuid4

from fastapi.testclient import TestClient
from test_global_stop_api import ORIGIN, headers
from test_global_stop_api import state as state


def definition():
    return {
        "name": "Relatório",
        "purpose": "Comparar fontes",
        "inputs": [],
        "steps": [{"title": "Pesquisar", "instruction": "Conferir referências"}],
        "resources": {"environment": "none", "tools": []},
        "output": "Relatório",
        "completion": "Fontes conferidas",
    }


def test_library_requires_session_csrf_and_closed_contract(state):
    client, agent, _ = state
    path = f"/api/v1/agents/{agent.id}/skills"
    anonymous = TestClient(client.app, base_url=ORIGIN)
    assert anonymous.get(path).status_code == 401
    assert client.get(path).json() == []
    body = {"client_request_id": str(uuid4()), "definition": definition()}
    assert client.post(path, json=body, headers={"Origin": ORIGIN}).status_code in (401, 403)
    for invalid in (
        {"extra": True},
        {"client_request_id": "bad"},
        {"definition": definition() | {"code": "exec()"}},
    ):
        response = client.post(path, json=body | invalid, headers=headers(client))
        assert response.status_code == 422
        assert "exec()" not in response.text
    created = client.post(path, json=body, headers=headers(client))
    assert created.status_code == 200
    assert client.post(path, json=body, headers=headers(client)).json() == created.json()
    for query in ("limit=0", "limit=101", "offset=-1"):
        assert client.get(path + "?" + query).status_code == 422
    assert client.get(f"/api/v1/agents/{uuid4()}/skills").status_code == 404


def test_revise_enable_export_and_import_preserve_revision_binding(state):
    client, agent, _ = state
    path = f"/api/v1/agents/{agent.id}/skills"
    first = client.post(
        path,
        json={"client_request_id": str(uuid4()), "definition": definition()},
        headers=headers(client),
    ).json()
    target = path + "/" + first["id"]
    activate = {
        "client_request_id": str(uuid4()),
        "expected_revision": 1,
        "version": 1,
        "enabled": True,
    }
    assert client.put(
        target + "/enabled", json=activate, headers={"Origin": ORIGIN}
    ).status_code in (401, 403)
    enabled = client.put(target + "/enabled", json=activate, headers=headers(client)).json()
    assert enabled["enabled_version"] == 1
    revise = {
        "client_request_id": str(uuid4()),
        "expected_revision": 2,
        "definition": definition() | {"name": "Melhorado"},
    }
    changed = client.post(target + "/revisions", json=revise, headers=headers(client)).json()
    assert changed["current_version"] == 2 and changed["enabled_version"] is None
    assert client.get(target).json() == changed
    assert len(client.get(target + "/revisions").json()) == 2
    assert client.get(target + "/revisions?limit=1&offset=1").json()[0] == first["current"]
    wrong = activate | {"client_request_id": str(uuid4()), "expected_revision": 3}
    assert client.put(target + "/enabled", json=wrong, headers=headers(client)).status_code == 409
    assert client.get(target + "/revisions/0/export").status_code == 422
    assert client.get(target + "/revisions/99/export").status_code == 404
    document = client.get(target + "/revisions/1/export").json()
    imported = client.post(
        path + "/import",
        json={"client_request_id": str(uuid4()), "document": document},
        headers=headers(client),
    ).json()
    assert imported["current"]["definition"] == first["current"]["definition"]
    assert imported["current"]["origin"] == "import" and imported["enabled_version"] is None
    for extra in (
        {"scope": "global"},
        {"format": "unknown"},
        {"agent_id": str(uuid4())},
        {"script": "exec()"},
    ):
        assert (
            client.post(
                path + "/import",
                json={"client_request_id": str(uuid4()), "document": document | extra},
                headers=headers(client),
            ).status_code
            == 422
        )
    assert client.get(f"/api/v1/agents/{uuid4()}/skills/{first['id']}").status_code == 404
    with client.app.state.database.transaction(write=False) as connection:
        assert connection.execute("SELECT count(*) FROM tool_grants").get == 0
        assert connection.execute("SELECT count(*) FROM tasks").get == 0
