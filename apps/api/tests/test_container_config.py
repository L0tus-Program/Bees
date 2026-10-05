from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError

from bees_api.app import create_app
from bees_api.cli import main, parse_settings
from bees_api.config import Settings


def container_settings(tmp_path: Path, **kwargs) -> Settings:
    return Settings(
        deployment_mode="container",
        host="0.0.0.0",
        browser_port=8080,
        data_dir=tmp_path / "data",
        vault_key_file=tmp_path / "secrets" / "vault.key",
        web_dist=tmp_path / "missing",
        **kwargs,
    )


def test_container_environment_is_explicit_and_cli_flags_override(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("BEES_DEPLOYMENT_MODE", "container")
    monkeypatch.setenv("BEES_HOST", "0.0.0.0")
    monkeypatch.setenv("BEES_BROWSER_PORT", "8080")
    monkeypatch.setenv("BEES_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("BEES_VAULT_KEY_FILE", str(tmp_path / "secrets" / "vault.key"))
    config = parse_settings([])
    assert config.deployment_mode == "container" and config.host == "0.0.0.0"
    assert config.allowed_origins == ("http://127.0.0.1:8080", "http://localhost:8080")
    assert not config.secure_cookie
    overridden = parse_settings(["--browser-port", "8123", "--host", "127.0.0.1"])
    assert overridden.browser_port == 8123 and overridden.host == "127.0.0.1"


@pytest.mark.parametrize("key_location", ["data", "parent", "traversal"])
def test_key_volume_cannot_overlap_data(tmp_path, key_location) -> None:
    data = tmp_path / "data"
    if key_location == "data":
        key = data / "key"
    elif key_location == "parent":
        key = tmp_path / "key"
    else:
        key = tmp_path / "secrets" / ".." / "data" / "key"
    with pytest.raises(ValidationError):
        Settings(deployment_mode="container", data_dir=data, vault_key_file=key)


@pytest.mark.parametrize(
    "invalid",
    [
        {"host": "0.0.0.0"},
        {"deployment_mode": "container"},
        {"deployment_mode": "container", "vault_key_file": Path("relative/key")},
        {"vault_key_file": Path("/run/bees-secrets/vault.key")},
    ],
)
def test_incomplete_container_or_implicit_external_bind_is_rejected(invalid) -> None:
    with pytest.raises(ValidationError):
        Settings(**invalid)


def test_container_rejects_key_in_environment(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("BEES_VAULT_KEY", "must-not-be-used-in-container")
    with pytest.raises(SystemExit):
        parse_settings(
            [
                "--deployment-mode",
                "container",
                "--data-dir",
                str(tmp_path / "data"),
                "--vault-key-file",
                str(tmp_path / "secrets" / "vault.key"),
            ]
        )


def test_bind_does_not_trust_docker_gateway_proxy_headers(tmp_path, monkeypatch) -> None:
    config = container_settings(tmp_path)
    monkeypatch.setattr("bees_api.cli.parse_settings", lambda: config)
    captured = {}
    monkeypatch.setattr("bees_api.cli.uvicorn.run", lambda app, **kwargs: captured.update(kwargs))
    main()
    assert captured["host"] == "0.0.0.0"
    assert captured["proxy_headers"] is False
    assert captured["forwarded_allow_ips"] == ""


@pytest.fixture
def container_client(tmp_path, monkeypatch):
    # Chave de teste injetada só nesta fixture: os testes POSIX cobrem provisionamento real.
    monkeypatch.setattr(
        "bees_api.app.managed_vault_key",
        lambda *_: SecretStr(Fernet.generate_key().decode("ascii")),
    )
    monkeypatch.setattr("bees_api.app.prepare_managed_directories", lambda *_: None)
    with TestClient(
        create_app(container_settings(tmp_path)), base_url="http://localhost:8080"
    ) as c:
        yield c


def test_container_published_origin_and_internal_health_are_distinct(container_client) -> None:
    client = container_client
    assert client.get("/api/v1/auth/status").json() == {
        "configured": False,
        "authenticated": False,
    }
    assert client.get("/api/v1/health", headers={"host": "127.0.0.1:8000"}).status_code == 200
    assert client.get("/api/v1/auth/status", headers={"host": "127.0.0.1:8000"}).status_code == 400
    for host in ("172.18.0.1:8000", "bees:8000", "localhost:9999", "attacker.example:8080"):
        assert client.get("/api/v1/auth/status", headers={"host": host}).status_code == 400


@pytest.mark.parametrize(
    "origin",
    [
        None,
        "http://localhost:8000",
        "http://localhost:5173",
        "http://172.18.0.1:8080",
        "https://attacker.example",
    ],
)
def test_gateway_or_unconfigured_origin_cannot_claim_identity(container_client, origin) -> None:
    headers = {"origin": origin} if origin else {}
    response = container_client.post(
        "/api/v1/auth/setup",
        json={
            "bootstrap_token": "x" * 43,
            "name": "Teste",
            "password": "senha descartável de teste",
        },
        headers=headers,
    )
    assert response.status_code == 403
    assert not container_client.app.state.identity.configured()


def test_published_origin_requires_possession_and_csrf(container_client) -> None:
    client = container_client
    origin = {"origin": "http://localhost:8080"}
    token = client.app.state.identity.issue_bootstrap()
    bad = client.post(
        "/api/v1/auth/setup",
        json={
            "bootstrap_token": "x" * 43,
            "name": "Teste",
            "password": "senha descartável de teste",
        },
        headers=origin,
    )
    assert bad.status_code == 401
    setup = client.post(
        "/api/v1/auth/setup",
        json={
            "bootstrap_token": token,
            "name": "Teste",
            "password": "senha descartável de teste",
        },
        headers=origin,
    )
    assert setup.status_code == 200
    assert token not in setup.text
    cookie = setup.headers["set-cookie"]
    assert "HttpOnly" in cookie and "SameSite=strict" in cookie
    assert client.post("/api/v1/auth/logout", json={}, headers=origin).status_code == 403
    csrf = {**origin, "x-bees-csrf": setup.json()["csrf_token"]}
    assert client.post("/api/v1/auth/logout", json={}, headers=csrf).status_code == 204


def test_container_https_cannot_be_spoofed_with_forwarded_headers(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        "bees_api.app.managed_vault_key",
        lambda *_: SecretStr(Fernet.generate_key().decode("ascii")),
    )
    monkeypatch.setattr("bees_api.app.prepare_managed_directories", lambda *_: None)
    settings = container_settings(tmp_path, public_url="https://bees.example")
    with TestClient(create_app(settings), base_url="http://bees.example") as client:
        response = client.get(
            "/api/v1/auth/status",
            headers={
                "x-forwarded-proto": "https",
                "forwarded": "proto=https;host=bees.example",
            },
        )
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "https_required"
