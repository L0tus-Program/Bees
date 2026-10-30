"""API de canais: token no cofre, código único, replay sem novo código, revogação."""

from uuid import uuid4

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from pydantic import SecretStr

from bees_api.app import create_app
from bees_api.config import Settings
from bees_core.channels.adapters import DiscordAdapter, TelegramAdapter
from bees_core.models import Agent, Conversation

ORIGIN = "http://127.0.0.1:8000"
PASSWORD = "senha teste canais 123456"
TOKEN = "123456:ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def headers(client):
    return {"Origin": ORIGIN, "X-Bees-CSRF": client.get("/api/v1/auth/status").json()["csrf_token"]}


def telegram(request):
    if request.url.path.endswith("/getMe"):
        if TOKEN not in request.url.path:
            return httpx.Response(401, json={"ok": False})
        return httpx.Response(
            200, json={"ok": True, "result": {"id": 9, "first_name": "Bee", "username": "bee_bot"}}
        )
    return httpx.Response(404)


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
        transport = httpx.MockTransport(telegram)
        client.app.state.channel_adapters = {
            "telegram": TelegramAdapter(transport),
            "discord": DiscordAdapter(transport),
        }
        yield client, agent


def test_create_pair_code_replay_and_revoke(state):
    client, agent = state
    base = f"/api/v1/agents/{agent.id}/channels"
    request = {"client_request_id": str(uuid4()), "kind": "telegram", "token": TOKEN}
    created = client.post(base, json=request, headers=headers(client))
    assert created.status_code == 201, created.text
    body = created.json()
    assert TOKEN not in created.text
    assert len(body["pairing_code"]) == 8
    assert body["pairing_link"] == f"https://t.me/bee_bot?start={body['pairing_code']}"
    channel = body["channel"]
    assert channel["status"] == "pending" and channel["bot_username"] == "bee_bot"
    replay = client.post(base, json=request, headers=headers(client)).json()
    assert replay["replayed"] and replay["pairing_code"] is None
    assert replay["channel"]["id"] == channel["id"]
    listed = client.get(base).json()["channels"]
    assert [item["id"] for item in listed] == [channel["id"]]
    fresh = client.post(
        f"{base}/{channel['id']}/code", json={"expected_revision": 1}, headers=headers(client)
    ).json()
    assert fresh["pairing_code"] != body["pairing_code"]
    stale = client.post(
        f"{base}/{channel['id']}/update",
        json={"expected_revision": 1, "mode": "agent"},
        headers=headers(client),
    )
    assert stale.status_code == 409
    updated = client.post(
        f"{base}/{channel['id']}/update",
        json={"expected_revision": 2, "mode": "agent"},
        headers=headers(client),
    ).json()
    assert updated["mode"] == "agent"
    revoked = client.post(
        f"{base}/{channel['id']}/revoke", json={"expected_revision": 3}, headers=headers(client)
    ).json()
    assert revoked["status"] == "revoked"
    assert client.get(base).json()["channels"] == []


def test_invalid_token_and_discord_without_channel_are_refused(state):
    client, agent = state
    base = f"/api/v1/agents/{agent.id}/channels"
    bad = client.post(
        base,
        json={"client_request_id": str(uuid4()), "kind": "telegram", "token": "999:" + "x" * 30},
        headers=headers(client),
    )
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "channel_token_invalid"
    discord = client.post(
        base,
        json={"client_request_id": str(uuid4()), "kind": "discord", "token": "y" * 60},
        headers=headers(client),
    )
    assert discord.status_code == 422
    assert client.get(base).json()["channels"] == []
