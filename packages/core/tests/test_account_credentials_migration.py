"""Upgrade de credenciais existentes com metadados privados da conta."""

import json
from uuid import uuid4

from bees_core.models import Agent
from bees_core.providers.contracts import ProviderConfig
from bees_core.providers.credentials import account_credentials, select_credential
from bees_core.storage.database import Database
from bees_core.storage.store import StateStore


def test_upgrade_adopts_existing_vault_references_once_without_reading_secrets(
    tmp_path, monkeypatch
):
    database = Database(tmp_path / "state.sqlite3")
    migrations = Database._migrations
    all_migrations = migrations(database)
    with monkeypatch.context() as stage:
        stage.setattr(Database, "_migrations", lambda self: all_migrations[:24])
        database.initialize()
    reference = "vault:" + str(uuid4())
    config = ProviderConfig(
        kind="openai_compatible",
        endpoint="https://api.openai.com/v1",
        model="old",
        capabilities={"text": True, "tool_calls": True},
        secret_ref=reference,
    )
    with database.transaction() as connection:
        connection.execute(
            "INSERT INTO identity_users(id,name,password_hash,created_at) VALUES(1,?,?,0)",
            ("Pessoa", "hash-only"),
        )
    with StateStore(database).transaction() as unit:
        for name in ("Uma", "Duas"):
            unit.agents.create(Agent(name=name, provider_config=config.model_dump(mode="json")))
        env = config.model_copy(update={"secret_ref": "env:PRIVATE_ENV"})
        unit.agents.create(Agent(name="Ambiente", provider_config=env.model_dump(mode="json")))
    database.initialize()
    database.initialize()
    with StateStore(database).transaction(write=False) as unit:
        assert account_credentials(unit) == [
            {"id": reference, "kind": config.kind, "endpoint": config.endpoint}
        ]
        no_key = config.model_copy(update={"secret_ref": None, "model": "new"})
        assert select_credential(unit, no_key, reference).secret_ref == reference
    assert len(list(tmp_path.glob("*.backup-*.sqlite3"))) == 1
    with database.transaction(write=False) as connection:
        assert (
            "PRIVATE_ENV"
            not in connection.execute("SELECT llm_credentials_json FROM identity_users").get
        )
        assert (
            json.loads(connection.execute("SELECT llm_credentials_json FROM identity_users").get)[
                0
            ]["id"]
            == reference
        )
