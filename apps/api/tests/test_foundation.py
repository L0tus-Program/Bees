from pathlib import Path

import apsw
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from bees_api.app import create_app
from bees_api.cli import parse_settings
from bees_api.config import Settings
from bees_api.runtime import validate_sqlite_runtime


def test_health_without_frontend(tmp_path: Path) -> None:
    with TestClient(
        create_app(Settings(web_dist=tmp_path / "missing", data_dir=tmp_path / "state")),
        base_url="http://127.0.0.1",
    ) as client:
        response = client.get("/api/v1/health")
        assert response.status_code == 200
        assert response.json() == {
            "status": "ok",
            "service": "bees-api",
            "version": "0.1.0",
            "stage": "foundation",
        }
        assert client.get("/").status_code == 503
        assert client.get("/api/v1/unknown").status_code == 404


def test_spa_does_not_hide_api_or_asset_errors(tmp_path: Path) -> None:
    (tmp_path / "index.html").write_text("<html>Bees de teste</html>", encoding="utf-8")
    (tmp_path / "app.js").write_text("console.log('teste');", encoding="utf-8")
    with TestClient(
        create_app(Settings(web_dist=tmp_path, data_dir=tmp_path / "state")),
        base_url="http://127.0.0.1",
    ) as client:
        assert client.get("/").status_code == 200
        assert "Bees de teste" in client.get("/connections").text
        assert client.get("/app.js").headers["content-type"].startswith("text/javascript")
        assert client.get("/missing.js").status_code == 404
        assert client.get("/api/v1/health").headers["content-type"] == "application/json"
        for path in ("/api", "/api/unknown", "/api/v1/unknown"):
            response = client.get(path)
            assert response.status_code == 404
            assert response.headers["content-type"] == "application/json"


@pytest.mark.parametrize("version", ["3.51.3", "3.52.1", "3.53.4"])
def test_supported_sqlite(version: str) -> None:
    validate_sqlite_runtime(version)


@pytest.mark.parametrize("version", ["3.50.4", "3.51.2", "3.52.0", "invalid"])
def test_unsafe_sqlite_is_rejected(version: str) -> None:
    with pytest.raises(RuntimeError):
        validate_sqlite_runtime(version)


def test_effective_runtime_is_checked_at_startup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(apsw, "sqlitelibversion", lambda: "3.52.0")
    with pytest.raises(RuntimeError, match="SQLite 3.52.0"):
        with TestClient(create_app(Settings(web_dist=tmp_path, data_dir=tmp_path / "state"))):
            pass


def test_default_bind_is_loopback() -> None:
    settings = parse_settings([])
    assert settings.host == "127.0.0.1"
    assert settings.port == 8000


@pytest.mark.parametrize("host", ["0.0.0.0", "::", "192.168.1.20", "localhost"])
def test_external_bind_is_rejected(host: str) -> None:
    with pytest.raises(ValidationError):
        Settings(host=host)
    with pytest.raises(SystemExit) as error:
        parse_settings(["--host", host])
    assert error.value.code == 2


@pytest.mark.parametrize("port", [0, 65536])
def test_invalid_port_is_rejected(port: int) -> None:
    with pytest.raises(SystemExit) as error:
        parse_settings(["--port", str(port)])
    assert error.value.code == 2


def test_environment_defaults_and_cli_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BEES_PORT", "8123")
    monkeypatch.setenv("BEES_WEB_DIST", str(tmp_path / "from-env"))
    settings = parse_settings([])
    assert settings.port == 8123
    assert settings.web_dist == tmp_path / "from-env"
    overridden = parse_settings(["--port", "8124", "--web-dist", str(tmp_path / "from-cli")])
    assert overridden.port == 8124
    assert overridden.web_dist == tmp_path / "from-cli"


def test_external_environment_host_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BEES_HOST", "0.0.0.0")
    with pytest.raises(SystemExit) as error:
        parse_settings([])
    assert error.value.code == 2
    assert parse_settings(["--host", "127.0.0.1"]).host == "127.0.0.1"


def test_untrusted_host_is_rejected(tmp_path: Path) -> None:
    with TestClient(
        create_app(Settings(web_dist=tmp_path, data_dir=tmp_path / "state")),
        base_url="http://127.0.0.1",
    ) as client:
        response = client.get("/api/v1/health", headers={"host": "untrusted.example"})
        assert response.status_code == 400
