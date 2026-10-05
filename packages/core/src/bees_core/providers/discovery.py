"""Catálogo de candidatos de conversa. Listagem não comprova geração/capacidades.

OpenAI/Gemini usam heurística conservadora por família; OpenRouter usa modalidade
de saída declarada. Ollama tags prova presença local, não capacidade completion.
Metadados/nome são dados não confiáveis e só os campos id/name saem do adaptador.
"""

import re

from pydantic import Field, ValidationError, field_validator

from bees_core.providers.base import HTTPAdapter
from bees_core.providers.catalog import ProviderId, provider_preset
from bees_core.providers.contracts import ConnectionConfig, Contract
from bees_core.providers.errors import ProviderError

MAX_CATALOG_MODELS = 4096
_OPENAI_CHAT = re.compile(r"^(gpt-|chatgpt-|o(?:1|3|4)(?:-|$))")
_NON_CHAT = re.compile(
    r"(?:^|-)(?:embedding|embeddings|audio|realtime|moderation|image|tts|transcribe)(?:-|$)"
)
_OPENAI_ONLY = re.compile(r"(?:^|-)(?:codex|pro|instruct|base)(?:-|$)")


class DiscoveredModel(Contract):
    id: str = Field(min_length=1, max_length=200)
    name: str = Field(min_length=1, max_length=240)

    @field_validator("id", "name")
    @classmethod
    def valid_text(cls, value: str) -> str:
        if value != value.strip() or any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise ValueError("Identificador/nome deve ter texto sem controles.")
        return value


def _candidates(items: object, provider_id: ProviderId) -> list[DiscoveredModel]:
    if not isinstance(items, list) or len(items) > MAX_CATALOG_MODELS:
        raise ProviderError("invalid_response")
    result = []
    seen = set()
    try:
        for item in items:
            if not isinstance(item, dict):
                raise ValueError()
            raw_id = (
                item.get("name") if provider_id in ("ollama", "custom_ollama") else item.get("id")
            )
            label = (
                raw_id if provider_id in ("ollama", "custom_ollama") else item.get("name", raw_id)
            )
            candidate = DiscoveredModel(id=raw_id, name=label)
            if candidate.id in seen:
                raise ValueError()
            seen.add(candidate.id)
            identifier = candidate.id.lower()
            if provider_id in ("ollama", "custom_ollama"):
                if any(item.get(key) not in (None, "") for key in ("remote_host", "remote_model")):
                    continue
                if "cloud" in identifier or "@" in identifier or "://" in identifier:
                    continue
            elif provider_id == "openrouter":
                architecture = item.get("architecture")
                modalities = (
                    architecture.get("output_modalities")
                    if isinstance(architecture, dict)
                    else None
                )
                if not isinstance(modalities, list) or not all(
                    isinstance(m, str) for m in modalities
                ):
                    raise ValueError()
                if "text" not in modalities:
                    continue
                if "input_modalities" in architecture:
                    inputs = architecture["input_modalities"]
                    if not isinstance(inputs, list) or not all(isinstance(m, str) for m in inputs):
                        raise ValueError()
                    if "text" not in inputs:
                        continue
            elif provider_id == "openai":
                if (
                    not _OPENAI_CHAT.match(identifier)
                    or _NON_CHAT.search(identifier)
                    or _OPENAI_ONLY.search(identifier)
                ):
                    continue
            elif provider_id == "gemini":
                if not identifier.removeprefix("models/").startswith("gemini-") or _NON_CHAT.search(
                    identifier
                ):
                    continue
            result.append(candidate)
    except ValidationError, ValueError, TypeError:
        raise ProviderError("invalid_response") from None
    return sorted(result, key=lambda model: (model.name.casefold(), model.id))


class DiscoveryAdapter(HTTPAdapter):
    async def discover(
        self, config: ConnectionConfig, provider_id: ProviderId
    ) -> list[DiscoveredModel]:
        try:
            config = ConnectionConfig.model_validate(config.model_dump())
        except ValidationError, ValueError, TypeError:
            raise ProviderError("invalid_config") from None
        preset = provider_preset(provider_id)
        if config.kind != preset.kind or (preset.endpoint and config.endpoint != preset.endpoint):
            raise ProviderError("invalid_config")
        # Só o catálogo usa até4MiB; geração mantém seu próprio limite.
        catalog = ConnectionConfig.model_validate(
            config.model_dump() | {"max_response_bytes": config.max_catalog_bytes}
        )
        route = "api/tags" if config.kind == "ollama" else "models"
        async with self._session(catalog) as client:
            response = await self._json(client, catalog, "GET", route)
        return _candidates(
            response.get("models" if config.kind == "ollama" else "data"), provider_id
        )
