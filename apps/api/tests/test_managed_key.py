import os
import stat
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from pydantic import SecretStr

from bees_api.app import create_app
from bees_api.config import Settings
from bees_api.managed_key import MARKER, ManagedKeyError, managed_vault_key
from bees_core.models import Agent
from bees_core.storage.database import Database
from bees_core.storage.store import StateStore

pytestmark = pytest.mark.skipif(os.name != "posix", reason="Provisionamento do container é Linux.")


@pytest.fixture
def installation(tmp_path: Path) -> tuple[Path, Path, Database]:
    data = tmp_path / "data"
    data.mkdir(mode=0o700)
    database = Database(data / "bees.sqlite3")
    database.initialize()
    return tmp_path / "secrets" / "vault.key", data, database


def test_first_start_and_restart_preserve_real_ciphertext(installation, tmp_path: Path) -> None:
    key_file, data, database = installation
    settings = Settings(
        deployment_mode="container",
        host="0.0.0.0",
        browser_port=8080,
        vault_key_file=key_file,
        data_dir=data,
        web_dist=tmp_path / "missing",
    )
    with TestClient(create_app(settings), base_url="http://localhost:8080") as client:
        vault = client.app.state.vault
        assert vault.status().available and vault.status().backend == "fernet"
        reference = vault.put(SecretStr("only-test-key"))
        assert client.get("/api/v1/health").status_code == 200
    key = key_file.read_bytes()
    assert stat.S_IMODE(key_file.stat().st_mode) == 0o600
    assert stat.S_IMODE(key_file.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE((data / MARKER).stat().st_mode) == 0o600
    assert key not in (data / MARKER).read_bytes()
    ciphertext = (data / "vault" / (reference[6:] + ".secret")).read_bytes()
    assert b"only-test-key" not in ciphertext
    with TestClient(create_app(settings), base_url="http://localhost:8080") as client:
        assert client.app.state.vault.resolve(reference).get_secret_value() == "only-test-key"
    assert key_file.read_bytes() == key
    assert managed_vault_key(key_file, data, database).get_secret_value().encode() == key


def test_concurrent_provisioners_share_one_durable_key(installation) -> None:
    key_file, data, database = installation
    with ThreadPoolExecutor(max_workers=8) as pool:
        values = list(
            pool.map(
                lambda _: managed_vault_key(key_file, data, database).get_secret_value(), range(8)
            )
        )
    assert len(set(values)) == 1
    assert key_file.read_text() == values[0]


def test_missing_key_never_regenerates_even_when_vault_empty(installation) -> None:
    key_file, data, database = installation
    managed_vault_key(key_file, data, database)
    marker = (data / MARKER).read_bytes()
    key_file.unlink()
    with pytest.raises(ManagedKeyError):
        managed_vault_key(key_file, data, database)
    assert not key_file.exists()
    assert (data / MARKER).read_bytes() == marker


def test_cli_read_mode_never_provisions_or_repairs_installation(installation) -> None:
    key_file, data, database = installation
    with pytest.raises(ManagedKeyError):
        managed_vault_key(key_file, data, database, provision=False)
    assert not key_file.exists() and not (data / MARKER).exists()
    original = managed_vault_key(key_file, data, database)
    assert managed_vault_key(key_file, data, database, provision=False) == original
    (data / MARKER).unlink()
    with pytest.raises(ManagedKeyError):
        managed_vault_key(key_file, data, database, provision=False)
    assert not (data / MARKER).exists()


@pytest.mark.parametrize("evidence", ["ciphertext", "reference", "staging"])
def test_existing_credentials_without_marker_prevent_key_generation(installation, evidence) -> None:
    key_file, data, database = installation
    if evidence == "reference":
        with StateStore(database).transaction() as unit:
            unit.agents.create(
                Agent(
                    name="Teste",
                    provider_config={
                        "kind": "openai_compatible",
                        "endpoint": "https://example.invalid/v1",
                        "model": "test",
                        "secret_ref": "vault:17aa4ccb-9d31-487a-932b-a6bbaf7fe67a",
                        "capabilities": {"text": True, "tool_calls": False},
                    },
                )
            )
    else:
        (data / "vault").mkdir()
        name = "old.secret" if evidence == "ciphertext" else ".staging-old.tmp"
        (data / "vault" / name).write_bytes(b"never-read-as-plaintext")
    with pytest.raises(ManagedKeyError):
        managed_vault_key(key_file, data, database)
    assert not key_file.exists()


@pytest.mark.parametrize("mutation", ["key", "marker", "partial_key", "public_key", "public_dir"])
def test_changed_or_unsafe_key_is_not_replaced(installation, mutation) -> None:
    key_file, data, database = installation
    managed_vault_key(key_file, data, database)
    if mutation == "key":
        key_file.write_bytes(Fernet.generate_key())
    elif mutation == "marker":
        (data / MARKER).write_bytes(b"0" * 64)
    elif mutation == "partial_key":
        key_file.write_bytes(b"truncated")
    elif mutation == "public_key":
        key_file.chmod(0o644)
    else:
        key_file.parent.chmod(0o755)
    previous = key_file.read_bytes()
    with pytest.raises(ManagedKeyError):
        managed_vault_key(key_file, data, database)
    assert key_file.read_bytes() == previous


@pytest.mark.parametrize("link", ["key", "directory", "hardlink"])
def test_symlinks_and_hardlinks_are_not_followed(installation, tmp_path: Path, link) -> None:
    key_file, data, database = installation
    outside = tmp_path / "outside"
    outside.mkdir(mode=0o700)
    target = outside / "vault.key"
    target.write_bytes(Fernet.generate_key())
    target.chmod(0o600)
    if link == "directory":
        key_file.parent.symlink_to(outside, target_is_directory=True)
    else:
        key_file.parent.mkdir(mode=0o700)
        if link == "key":
            key_file.symlink_to(target)
        else:
            os.link(target, key_file)
    original = target.read_bytes()
    with pytest.raises(ManagedKeyError):
        managed_vault_key(key_file, data, database)
    assert target.read_bytes() == original
    assert not (data / MARKER).exists()


def test_corrupt_state_does_not_authorize_generation(installation) -> None:
    key_file, data, database = installation
    database.path.write_bytes(b"broken state")
    with pytest.raises(ManagedKeyError) as caught:
        managed_vault_key(key_file, data, database)
    assert not key_file.exists()
    assert "broken state" not in str(caught.value)


def test_wrong_owner_is_rejected_before_generation(installation, monkeypatch) -> None:
    key_file, data, database = installation
    current_uid = os.geteuid()
    monkeypatch.setattr(os, "geteuid", lambda: current_uid + 1)
    with pytest.raises(ManagedKeyError):
        managed_vault_key(key_file, data, database)
    assert not key_file.exists()


@pytest.mark.parametrize("link", ["data_directory", "database_file"])
def test_startup_rejects_links_before_opening_sqlite(tmp_path: Path, monkeypatch, link) -> None:
    outside = tmp_path / "outside"
    outside.mkdir(mode=0o700)
    outside_database = outside / "bees.sqlite3"
    outside_database.write_bytes(b"must-never-migrate")
    outside_database.chmod(0o600)
    data = tmp_path / "data"
    if link == "data_directory":
        data.symlink_to(outside, target_is_directory=True)
    else:
        data.mkdir(mode=0o700)
        (data / "bees.sqlite3").symlink_to(outside_database)
    settings = Settings(
        deployment_mode="container",
        data_dir=data,
        vault_key_file=tmp_path / "secrets" / "vault.key",
        web_dist=tmp_path / "missing",
    )

    def unexpected_open(*_):
        pytest.fail("SQLite não deve iniciar antes da validação de caminhos.")

    monkeypatch.setattr("bees_api.app.Database.initialize", unexpected_open)
    with pytest.raises(ManagedKeyError):
        with TestClient(create_app(settings), base_url="http://localhost:8000"):
            pass
    assert outside_database.read_bytes() == b"must-never-migrate"
