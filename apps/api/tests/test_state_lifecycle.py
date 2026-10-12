from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import bees_core.storage.store as store_module
from bees_api.app import create_app
from bees_api.cli import parse_settings
from bees_api.config import Settings
from bees_core.models import Agent, Memory, Policy
from bees_core.storage.database import MigrationError


def settings_for(data_dir: Path, prune_limit: int = 1000) -> Settings:
    return Settings(
        data_dir=data_dir, web_dist=data_dir / "missing-web", cache_prune_limit=prune_limit
    )


def test_service_restarts_with_persisted_agent_memory_and_decision(tmp_path: Path) -> None:
    settings = settings_for(tmp_path / "private-state")
    first = create_app(settings)
    with TestClient(first, base_url="http://127.0.0.1:8000") as client:
        assert client.get("/api/v1/state/status").json() == {
            "status": "ready",
            "engine": "sqlite",
            "schema_version": 11,
        }
        with first.state.store.transaction() as uow:
            agent = uow.agents.create(Agent(name="Estado privado de teste"))
            memory = uow.memories.create(
                Memory(agent_id=agent.id, scope="agent", content="Conteúdo privado")
            )
            policy = uow.policies.create(
                Policy(agent_id=agent.id, name="Decisão persistida", effect="ask")
            )
        response = client.get("/api/v1/state/status")
        assert "Conteúdo privado" not in response.text
        assert str(settings.data_dir) not in response.text
        assert (
            client.post(
                "/api/v1/agents",
                json={"name": "Externo"},
                headers={"Origin": "http://127.0.0.1:8000"},
            ).status_code
            == 401
        )
    assert not hasattr(first.state, "store")

    second = create_app(settings)
    with TestClient(second, base_url="http://127.0.0.1:8000"):
        with second.state.store.transaction(write=False) as uow:
            assert uow.agents.get(agent.id) == agent
            assert uow.memories.get(memory.id) == memory
            assert uow.policies.get(policy.id) == policy


def test_startup_prunes_only_expired_cache_in_configured_batch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = settings_for(tmp_path / "state", prune_limit=1)
    first = create_app(settings)
    with TestClient(first, base_url="http://127.0.0.1:8000"):
        with first.state.store.transaction() as uow:
            agent = uow.agents.create(Agent(name="Canônico"))
        past = datetime.now(UTC) - timedelta(days=2)
        with monkeypatch.context() as clock:
            clock.setattr(store_module, "utc_now", lambda: past)
            for index in range(3):
                first.state.store.put_cache(f"expired-{index}", {"discardable": True}, 1)
        first.state.store.put_cache("valid", {"keep": True})

    second = create_app(settings)
    with TestClient(second, base_url="http://127.0.0.1:8000"):
        with second.state.store.transaction(write=False) as uow:
            assert uow.agents.get(agent.id) == agent
            assert len(uow.events.list()) == 1
        assert second.state.store.get_cache("valid") == {"keep": True}
        with second.state.database.transaction(write=False) as connection:
            assert connection.execute("SELECT count(*) FROM cache_entries").get == 3


def test_service_refuses_altered_migration_history(tmp_path: Path) -> None:
    settings = settings_for(tmp_path / "state")
    first = create_app(settings)
    with TestClient(first, base_url="http://127.0.0.1:8000"):
        with first.state.database.transaction() as connection:
            connection.execute("UPDATE schema_migrations SET checksum=?", ("0" * 64,))
    with pytest.raises(MigrationError, match="Checksum"):
        with TestClient(create_app(settings), base_url="http://127.0.0.1:8000"):
            pass


def test_storage_environment_defaults_and_flags_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BEES_DATA_DIR", str(tmp_path / "from-env"))
    monkeypatch.setenv("BEES_CACHE_TTL_SECONDS", "3600")
    monkeypatch.setenv("BEES_CACHE_PRUNE_LIMIT", "50")
    settings = parse_settings([])
    assert settings.data_dir == tmp_path / "from-env"
    assert settings.cache_ttl_seconds == 3600
    assert settings.cache_prune_limit == 50
    overridden = parse_settings(
        [
            "--data-dir",
            str(tmp_path / "from-cli"),
            "--cache-ttl-seconds",
            "1800",
            "--cache-prune-limit",
            "25",
        ]
    )
    assert overridden.data_dir == tmp_path / "from-cli"
    assert overridden.cache_ttl_seconds == 1800
    assert overridden.cache_prune_limit == 25


@pytest.mark.parametrize(
    "args",
    [
        ["--cache-ttl-seconds", "0"],
        ["--cache-ttl-seconds", "31536001"],
        ["--cache-prune-limit", "0"],
        ["--cache-prune-limit", "1001"],
    ],
)
def test_invalid_retention_configuration_is_rejected(args: list[str]) -> None:
    with pytest.raises(SystemExit) as error:
        parse_settings(args)
    assert error.value.code == 2
