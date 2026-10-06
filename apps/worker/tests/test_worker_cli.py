"""O executor abre a base existente, compartilha o cofre e nunca faz migração."""

from datetime import UTC, datetime, timedelta

import pytest
from cryptography.fernet import Fernet
from pydantic import SecretStr

from bees_api.config import Settings
from bees_api.worker_status import HEARTBEAT_KEY, worker_status
from bees_core.providers.vault import FileSecretVault
from bees_core.storage.database import Database, MigrationError
from bees_core.storage.store import StateStore
from bees_worker.cli import create_worker, existing_database


def settings(tmp_path):
    return Settings(
        data_dir=tmp_path / "state",
        web_dist=tmp_path / "missing",
        vault_key=SecretStr(Fernet.generate_key().decode()),
    )


def test_worker_requires_initialized_current_database(tmp_path, monkeypatch):
    config = settings(tmp_path)
    with pytest.raises(RuntimeError):
        existing_database(config)
    assert not (config.data_dir / "bees.sqlite3").exists()
    database = Database(config.data_dir / "bees.sqlite3")
    database.initialize()
    monkeypatch.setattr(Database, "initialize", lambda *_: pytest.fail("Worker tentou migrar."))
    assert existing_database(config).path == database.path
    with database.transaction() as connection:
        connection.execute("DELETE FROM schema_migrations WHERE version=5")
    with pytest.raises(MigrationError):
        existing_database(config)


def test_worker_reuses_existing_vault_without_secret_in_configuration(tmp_path, monkeypatch):
    config = settings(tmp_path)
    database = Database(config.data_dir / "bees.sqlite3")
    database.initialize()
    vault = FileSecretVault(config.data_dir / "vault", key=config.vault_key)
    reference = vault.put(SecretStr("credencial-descartavel"))
    captured = {}

    def fake_worker(db, resolver):
        captured["database"] = db
        captured["secret"] = resolver.resolve(reference).get_secret_value()
        return object()

    monkeypatch.setattr("bees_worker.cli.TaskWorker", fake_worker)
    create_worker(config, database)
    assert captured["database"] is database
    assert captured["secret"] == "credencial-descartavel"
    assert "credencial-descartavel" not in repr(config)


def test_presence_is_observational_and_expires(tmp_path):
    database = Database(tmp_path / "state" / "bees.sqlite3")
    database.initialize()
    store = StateStore(database)
    assert worker_status(store) == {"available": False, "last_seen": None}
    store.put_cache(HEARTBEAT_KEY, {"last_seen": datetime.now(UTC).isoformat()}, ttl_seconds=20)
    assert worker_status(store)["available"] is True
    store.put_cache(
        HEARTBEAT_KEY, {"last_seen": (datetime.now(UTC) - timedelta(seconds=30)).isoformat()}
    )
    assert worker_status(store)["available"] is False
    store.put_cache(HEARTBEAT_KEY, {"last_seen": "invalid"})
    assert worker_status(store)["available"] is False
