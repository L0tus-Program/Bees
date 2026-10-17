"""Restauração humana com destino confiável, ZIP limitado e nenhuma ativação."""

import pytest
from fastapi.testclient import TestClient
from test_global_stop_api import ORIGIN, headers

from bees_api.app import create_app
from bees_api.cli import parse_settings
from bees_api.config import Settings
from bees_core.models import Agent, Memory
from bees_core.portability import ExportService, Limits
from bees_core.restoration import RestoreService
from bees_core.storage.database import Database

BASE = "/api/v1/restoration/destinations"
CONSENT = {
    "Content-Type": "application/zip",
    "X-Bees-Restore-Consent": "restore_inactive_installation",
}


@pytest.fixture
def state(tmp_path):
    target = tmp_path / "inactive"
    settings = Settings(
        data_dir=tmp_path / "active",
        web_dist=tmp_path / "missing",
        restoration_destinations={"fresh": target},
    )
    with TestClient(create_app(settings), base_url=ORIGIN) as client:
        bootstrap = client.app.state.identity.issue_bootstrap()
        assert (
            client.post(
                "/api/v1/auth/setup",
                json={
                    "bootstrap_token": bootstrap,
                    "name": "Teste",
                    "password": "senha teste 123456",
                },
                headers={"Origin": ORIGIN},
            ).status_code
            == 200
        )
        with client.app.state.store.transaction() as unit:
            agent = unit.agents.create(Agent(name="Fonte de teste"))
            unit.memories.create(Memory(agent_id=agent.id, scope="agent", content="D" * 70000))
        verifier = ExportService(
            client.app.state.database, client.app.state.artifacts, tmp_path / "exports"
        )
        value = verifier.create()
        yield client, target, verifier.download(value.id), verifier


def test_configured_zip_restore_exceeds_json_limit_but_preserves_private_source(state):
    client, target, raw, _ = state
    assert len(raw) > 65536
    listed = client.get(BASE).json()
    assert listed == [{"id": "fresh", "available": True, "state": "inactive_only"}]
    response = client.post(BASE + "/fresh/restore", content=raw, headers=headers(client) | CONSENT)
    assert response.status_code == 200, response.text
    report = response.json()
    assert report["status"] == "inactive" and str(target) not in response.text
    assert "no-store" in response.headers["cache-control"]
    assert client.get(BASE + "/fresh/report").json() == report
    destination = Database(target / "bees.sqlite3")
    with destination.transaction(write=False) as connection:
        assert connection.execute("SELECT status FROM safety_control").get == "stopped"
        assert connection.execute("SELECT count(*) FROM identity_users").get == 0
        assert connection.execute("SELECT status FROM agents").get == "paused"
    assert (
        client.post(
            BASE + "/fresh/restore", content=raw, headers=headers(client) | CONSENT
        ).status_code
        == 409
    )
    assert (target / "source.zip").read_bytes() == raw


def test_auth_csrf_host_origin_and_explicit_consent_precede_body_effects(state):
    client, target, raw, _ = state
    anonymous = TestClient(client.app, base_url=ORIGIN)
    received = []

    def forbidden_body():
        received.append(True)
        raise AssertionError("Corpo não deve ser recebido antes da autenticação")
        yield b""

    response = anonymous.post(
        BASE + "/fresh/restore", content=forbidden_body(), headers={"Origin": ORIGIN} | CONSENT
    )
    assert response.status_code == 401 and not received
    assert anonymous.get(BASE).status_code == 401
    assert client.post(
        BASE + "/fresh/restore", content=raw, headers={"Origin": ORIGIN} | CONSENT
    ).status_code in (401, 403)
    for override in ({"Origin": "https://attacker.example"}, {"Host": "attacker.example"}):
        assert client.post(
            BASE + "/fresh/restore", content=raw, headers=headers(client) | CONSENT | override
        ).status_code in (400, 403)
    assert (
        client.post(
            BASE + "/fresh/restore",
            content=raw,
            headers=headers(client) | {"Content-Type": "application/zip"},
        ).status_code
        == 422
    )
    assert (
        client.post(
            BASE + "/fresh/restore",
            content=raw,
            headers=headers(client) | CONSENT | {"Content-Type": "application/json"},
        ).status_code
        == 415
    )
    assert (
        client.post(
            BASE + "/unknown/restore", content=raw, headers=headers(client) | CONSENT
        ).status_code
        == 404
    )
    assert not target.exists()


def test_limits_busy_invalid_package_and_gets_never_publish(state):
    client, target, raw, verifier = state
    assert client.get(BASE + "/fresh/report").status_code == 404
    slots = client.app.state.restoration_upload_slots
    slots.acquire()
    try:
        assert (
            client.post(
                BASE + "/fresh/restore", content=raw, headers=headers(client) | CONSENT
            ).json()["error"]["code"]
            == "restoration_busy"
        )
    finally:
        slots.release()
    assert (
        client.post(
            BASE + "/fresh/restore",
            content=b"private-corruption",
            headers=headers(client) | CONSENT,
        ).status_code
        == 409
    )
    limited = ExportService(
        verifier.database, verifier.artifacts, verifier.root, limits=Limits(max_bytes=32)
    )
    client.app.state.restoration = RestoreService(limited, {"fresh": target})
    assert (
        client.post(
            BASE + "/fresh/restore", content=b"x" * 33, headers=headers(client) | CONSENT
        ).status_code
        == 413
    )
    assert (
        client.post(
            BASE + "/fresh/restore",
            content=b"x",
            headers=headers(client) | CONSENT | {"Content-Length": "-1"},
        ).status_code
        == 400
    )
    assert (
        client.post(
            BASE + "/fresh/restore",
            content=b"x",
            headers=headers(client) | CONSENT | {"Content-Length": "9" * 5000},
        ).status_code
        == 400
    )
    assert not target.exists()
    assert (
        client.post(
            "/api/v1/auth/login",
            content=b"x" * 65537,
            headers=headers(client) | {"Content-Type": "application/json"},
        ).status_code
        == 413
    )


def test_cli_destinations_are_explicit_absolute_and_not_repeated(tmp_path):
    path = tmp_path / "inactive"
    assert parse_settings(["--restore-destination", f"fresh={path}"]).restoration_destinations == {
        "fresh": path
    }
    for args in (
        ["--restore-destination", "fresh=relative"],
        ["--restore-destination", f"fresh={path}", "--restore-destination", f"fresh={path}"],
        ["--restore-destination", "bad"],
    ):
        with pytest.raises(SystemExit):
            parse_settings(args)


def test_stalled_upload_has_total_deadline_releases_slot_and_never_publishes(state, monkeypatch):
    import asyncio

    client, target, _, _ = state
    monkeypatch.setattr("bees_api.restoration.UPLOAD_SECONDS", 0.02)
    request = client.build_request(
        "POST", BASE + "/fresh/restore", content=b"x", headers=headers(client) | CONSENT
    )
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": request.url.path,
        "raw_path": request.url.path.encode(),
        "query_string": b"",
        "headers": [(name.lower(), value) for name, value in request.headers.raw],
        "server": ("127.0.0.1", 8000),
        "client": ("127.0.0.1", 1234),
    }
    messages = []

    async def receive():
        await asyncio.sleep(0.2)
        return {"type": "http.request", "body": b"x", "more_body": True}

    async def send(message):
        messages.append(message)

    asyncio.run(client.app(scope, receive, send))
    assert next(m["status"] for m in messages if m["type"] == "http.response.start") == 408
    assert not target.exists()
    slots = client.app.state.restoration_upload_slots
    assert slots.acquire(blocking=False)
    slots.release()


def test_unconfigured_restore_is_unavailable_without_default_target(state):
    client, target, raw, _ = state
    client.app.state.restoration = None
    assert client.get(BASE).status_code == 503
    assert (
        client.post(
            BASE + "/fresh/restore", content=raw, headers=headers(client) | CONSENT
        ).status_code
        == 503
    )
    assert not target.exists()
