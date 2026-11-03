"""Referências de LLM pertencem à conta local, vinculadas ao destino explícito."""

import json

from bees_core.providers.contracts import ConnectionConfig
from bees_core.providers.errors import ProviderError


def account_credentials(unit) -> list[dict[str, str]]:
    row = unit.agents._context.connection.execute(
        "SELECT llm_credentials_json FROM identity_users WHERE id = 1"
    ).fetchone()
    return json.loads(row[0]) if row else []


def register_credential(unit, config: ConnectionConfig) -> None:
    if not config.secret_ref or not config.secret_ref.startswith("vault:"):
        return
    entry = {"id": config.secret_ref, "kind": config.kind, "endpoint": config.endpoint}
    entries = account_credentials(unit)
    if entry not in entries:
        unit.agents._context.connection.execute(
            "UPDATE identity_users SET llm_credentials_json = ? WHERE id = 1",
            (json.dumps([*entries, entry], ensure_ascii=False),),
        )


def select_credential(unit, config: ConnectionConfig, credential_id: str) -> ConnectionConfig:
    if config.secret_ref is not None or config.kind != "openai_compatible":
        raise ProviderError("invalid_config")
    if {
        "id": credential_id,
        "kind": config.kind,
        "endpoint": config.endpoint,
    } not in account_credentials(unit):
        raise ProviderError("invalid_secret_reference")
    return type(config).model_validate(config.model_dump() | {"secret_ref": credential_id})
