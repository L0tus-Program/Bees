"""Destinos conhecidos são conveniências explícitas, sem prender estado ao fornecedor."""

from typing import Literal

from pydantic import ValidationError

from bees_core.providers.contracts import ConnectionConfig, Contract, ProviderKind
from bees_core.providers.errors import ProviderError

ProviderId = Literal["openai", "openrouter", "gemini", "ollama", "custom", "custom_ollama"]


class ProviderPreset(Contract):
    id: ProviderId
    name: str
    kind: ProviderKind
    endpoint: str
    requires_api_key: bool


PROVIDERS = (
    ProviderPreset(
        id="openai",
        name="OpenAI",
        kind="openai_compatible",
        endpoint="https://api.openai.com/v1",
        requires_api_key=True,
    ),
    ProviderPreset(
        id="openrouter",
        name="OpenRouter",
        kind="openai_compatible",
        endpoint="https://openrouter.ai/api/v1",
        requires_api_key=True,
    ),
    ProviderPreset(
        id="gemini",
        name="Google Gemini",
        kind="openai_compatible",
        endpoint="https://generativelanguage.googleapis.com/v1beta/openai",
        requires_api_key=True,
    ),
    ProviderPreset(
        id="ollama",
        name="Ollama",
        kind="ollama",
        endpoint="http://127.0.0.1:11434",
        requires_api_key=False,
    ),
    ProviderPreset(
        id="custom",
        name="OpenAI compatible",
        kind="openai_compatible",
        endpoint="",
        requires_api_key=False,
    ),
    ProviderPreset(
        id="custom_ollama", name="Ollama custom", kind="ollama", endpoint="", requires_api_key=False
    ),
)


def provider_preset(provider_id: ProviderId) -> ProviderPreset:
    for preset in PROVIDERS:
        if preset.id == provider_id:
            return preset
    raise ProviderError("invalid_config")


def connection_for_provider(
    provider_id: ProviderId, *, endpoint: str | None = None, secret_ref: str | None = None
) -> ConnectionConfig:
    preset = provider_preset(provider_id)
    try:
        config = ConnectionConfig(
            kind=preset.kind, endpoint=endpoint or preset.endpoint, secret_ref=secret_ref
        )
    except ValidationError:
        raise ProviderError("invalid_config") from None
    if preset.endpoint and config.endpoint != preset.endpoint:
        raise ProviderError("invalid_config")
    return config
