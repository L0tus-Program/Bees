"""API de conectores MCP: catálogo lido no cadastro, credencial no cofre, CAS e remoção."""

import json
from uuid import uuid4

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from pydantic import SecretStr

from bees_api.app import create_app
from bees_api.config import Settings
from bees_core.models import Agent, Conversation

ORIGIN = "http://127.0.0.1:8000"
PASSWORD = "senha teste mcp 1234567"


def headers(client):
    return {"Origin": ORIGIN, "X-Bees-CSRF": client.get("/api/v1/auth/status").json()["csrf_token"]}


def server(request: httpx.Request):
    if request.headers.get("authorization") != "Bearer segredo":
        return httpx.Response(401)
    body = json.loads(request.content)
    if "id" not in body:
        return httpx.Response(202)
    result = (
        {"serverInfo": {"name": "Notas"}}
        if body["method"] == "initialize"
        else {"tools": [{"name": "search_notes", "inputSchema": {"type": "object"}}]}
    )
    return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": result})


@pytest.fixture
def state(tmp_path):
    settings = Settings(
        data_dir=tmp_path / "state",
        web_dist=tmp_path / "missing",
        vault_key=SecretStr(Fernet.generate_key().decode()),
    )
    with TestClient(create_app(settings), base_url=ORIGIN, raise_server_exceptions=False) as client:
        client.post(
            "/api/v1/auth/setup",
            json={
                "bootstrap_token": client.app.state.identity.issue_bootstrap(),
                "name": "Teste",
                "password": PASSWORD,
            },
            headers={"Origin": ORIGIN},
        ).raise_for_status()
        agent = Agent(name="Teste")
        with client.app.state.store.transaction() as unit:
            unit.agents.create(agent)
            unit.conversations.create(Conversation(agent_id=agent.id))
        client.app.state.mcp_transport = httpx.MockTransport(server)
        yield client, agent


def test_register_refresh_disable_and_remove(state):
    client, agent = state
    base = f"/api/v1/agents/{agent.id}/mcp-servers"
    body = {
        "client_request_id": str(uuid4()),
        "name": "notas",
        "url": "https://mcp.example.org/mcp",
        "token": "segredo",
    }
    created = client.post(base, json=body, headers=headers(client))
    assert created.status_code == 201, created.text
    server_view = created.json()
    assert "segredo" not in created.text and server_view["has_token"]
    assert server_view["tools"][0]["name"] == "search_notes"
    assert server_view["server_name"] == "Notas"
    assert client.post(base, json=body, headers=headers(client)).json()["id"] == server_view["id"]
    duplicate = client.post(
        base, json=body | {"client_request_id": str(uuid4())}, headers=headers(client)
    )
    assert duplicate.status_code == 409
    refreshed = client.post(
        f"{base}/{server_view['id']}/refresh",
        json={"expected_revision": 1},
        headers=headers(client),
    ).json()
    assert refreshed["status"] == "ready" and refreshed["revision"] == 2
    disabled = client.post(
        f"{base}/{server_view['id']}/enabled",
        json={"expected_revision": 2, "enabled": False},
        headers=headers(client),
    ).json()
    assert disabled["enabled"] is False
    removed = client.post(
        f"{base}/{server_view['id']}/remove", json={"expected_revision": 3}, headers=headers(client)
    ).json()
    assert removed["status"] == "removed" and client.get(base).json()["servers"] == []


def test_bad_token_and_plain_http_are_refused(state):
    client, agent = state
    base = f"/api/v1/agents/{agent.id}/mcp-servers"
    wrong = client.post(
        base,
        json={
            "client_request_id": str(uuid4()),
            "name": "notas",
            "url": "https://mcp.example.org/mcp",
            "token": "errado",
        },
        headers=headers(client),
    )
    assert wrong.status_code == 422 and wrong.json()["error"]["code"] == "mcp_auth_failed"
    plain = client.post(
        base,
        json={"client_request_id": str(uuid4()), "name": "x", "url": "http://mcp.example.org/m"},
        headers=headers(client),
    )
    assert plain.status_code == 422
    assert client.get(base).json()["servers"] == []
