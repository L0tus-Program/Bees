"""Gestão de extensões exige sessão, CSRF e revisões humanas; consultas não executam."""

from uuid import uuid4

from fastapi.testclient import TestClient
from test_tasks import headers
from test_tasks import state as state

from bees_api.app import create_app


def install(client):
    catalog = client.get("/api/v1/plugins/catalog")
    assert catalog.status_code == 200
    manifest = catalog.json()["plugins"][0]
    value = {"manifest": manifest, "client_request_id": str(uuid4())}
    result = client.post("/api/v1/plugins", json=value, headers=headers(client))
    assert result.status_code == 201, result.text
    return result.json(), value


def test_local_install_replay_pagination_and_restart(state):
    client, first, _, _, settings = state
    plugin, value = install(client)
    assert plugin["enabled"] is False
    assert "metadata" not in plugin
    assert client.post("/api/v1/plugins", json=value, headers=headers(client)).json() == plugin
    changed = value | {"manifest": value["manifest"] | {"name": "Outro nome"}}
    assert client.post("/api/v1/plugins", json=changed, headers=headers(client)).status_code == 409
    listed = client.get("/api/v1/plugins?limit=1").json()
    assert listed == {"plugins": [plugin], "has_more": False, "next_offset": None}
    assert client.get("/api/v1/plugins?offset=1").json()["plugins"] == []
    with TestClient(create_app(settings), base_url="http://127.0.0.1:8000") as restarted:
        restarted.cookies.update(client.cookies)
        assert restarted.get("/api/v1/plugins").json() == listed
        tools = restarted.get(f"/api/v1/agents/{first.id}/tools").json()["tools"]
        assert len(tools) == 1 and tools[0]["available"] is False
        assert tools[0]["granted"] is False
    with client.app.state.store.transaction(write=False) as unit:
        assert unit.tasks.list(limit=10) == []


def test_mutations_require_session_csrf_origin_and_no_client_authority(state):
    client, first, _, _, _ = state
    plugin, value = install(client)
    anonymous = TestClient(client.app, base_url="http://127.0.0.1:8000")
    assert anonymous.get("/api/v1/plugins/catalog").status_code == 401
    path = f"/api/v1/plugins/{plugin['id']}"
    mutation = {"expected_revision": plugin["revision"], "enabled": True}
    assert client.patch(path, json=mutation).status_code == 403
    assert (
        client.patch(
            path, json=mutation, headers=headers(client) | {"Origin": "http://untrusted.example"}
        ).status_code
        == 403
    )
    assert (
        client.patch(path, json=mutation | {"actor": "model"}, headers=headers(client)).status_code
        == 422
    )
    grant_path = f"/api/v1/agents/{first.id}/tools/{plugin['id']}/text.normalize"
    assert client.put(grant_path, json={"expected_revision": 0, "enabled": True}).status_code == 403
    injected = value | {"manifest": value["manifest"] | {"secret_ref": "segredo-descartavel"}}
    rejected = client.post("/api/v1/plugins", json=injected, headers=headers(client))
    assert rejected.status_code == 422 and "segredo-descartavel" not in rejected.text


def test_grant_is_per_tool_and_agent_and_disable_has_immediate_projection(state):
    client, first, other, _, _ = state
    plugin, _ = install(client)
    plugin_path = f"/api/v1/plugins/{plugin['id']}"
    enabled = client.patch(
        plugin_path,
        json={"expected_revision": plugin["revision"], "enabled": True},
        headers=headers(client),
    )
    assert enabled.status_code == 200, enabled.text
    assert (
        client.patch(
            plugin_path,
            json={"expected_revision": plugin["revision"], "enabled": False},
            headers=headers(client),
        ).status_code
        == 409
    )
    base = f"/api/v1/agents/{first.id}/tools"
    grant_path = base + f"/{plugin['id']}/text.normalize"
    value = {"expected_revision": 0, "enabled": True}
    grant = client.put(grant_path, json=value, headers=headers(client))
    assert grant.status_code == 200, grant.text
    assert grant.json()["agent_id"] == str(first.id)
    assert client.put(grant_path, json=value, headers=headers(client)).status_code == 409
    assert client.get(base).json()["tools"][0]["available"] is True
    assert client.get(f"/api/v1/agents/{other.id}/tools").json()["tools"][0]["granted"] is False
    disabled = client.patch(
        plugin_path,
        json={"expected_revision": enabled.json()["revision"], "enabled": False},
        headers=headers(client),
    )
    assert disabled.status_code == 200
    item = client.get(base).json()["tools"][0]
    assert item["granted"] is True and item["available"] is False
    revoked = client.put(
        grant_path,
        json={"expected_revision": grant.json()["revision"], "enabled": False},
        headers=headers(client),
    )
    assert revoked.status_code == 200
    assert client.get(base).json()["tools"][0]["granted"] is False


def test_unknown_agent_tool_and_invalid_pagination_fail_restrictively(state):
    client, first, _, _, _ = state
    plugin, _ = install(client)
    assert client.get(f"/api/v1/agents/{uuid4()}/tools").status_code == 404
    assert client.get("/api/v1/plugins?offset=-1").status_code == 422
    assert client.get(f"/api/v1/agents/{first.id}/tools?limit=0").status_code == 422
    assert client.put(
        f"/api/v1/agents/{first.id}/tools/{plugin['id']}/unknown.tool",
        json={"expected_revision": 0, "enabled": True},
        headers=headers(client),
    ).status_code in (404, 409)
    assert (
        client.put(
            f"/api/v1/agents/{first.id}/tools/{plugin['id']}/text.normalize",
            json={"expected_revision": 0, "enabled": True, "identity": "administrator"},
            headers=headers(client),
        ).status_code
        == 422
    )
