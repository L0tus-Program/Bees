"""Destinos conhecidos são conveniências explícitas, sem prender estado ao fornecedor."""

from typing import Literal

from pydantic import ValidationError

from bees_core.providers.contracts import ConnectionConfig, Contract, ProviderKind
from bees_core.providers.errors import ProviderError

ProviderId = Literal["openai", "openrouter", "gemini", "ollama", "custom", "custom_ollama"]


class SuggestedModel(Contract):
    id: str
    name: str


class ProviderPreset(Contract):
    id: ProviderId
    name: str
    kind: ProviderKind
    endpoint: str
    requires_api_key: bool
    # Sugestões de escolha; não afirmam disponibilidade, acesso ou instalação.
    models: tuple[SuggestedModel, ...] = ()


PROVIDERS = (
    ProviderPreset(
        id="openai",
        name="OpenAI",
        kind="openai_compatible",
        endpoint="https://api.openai.com/v1",
        requires_api_key=True,
        models=(
            SuggestedModel(id="gpt-5-mini", name="GPT-5 mini"),
            SuggestedModel(id="gpt-5-nano", name="GPT-5 nano"),
            SuggestedModel(id="gpt-4.1", name="GPT-4.1"),
            SuggestedModel(id="gpt-4.1-mini", name="GPT-4.1 mini"),
            SuggestedModel(id="gpt-4o-mini", name="GPT-4o mini"),
        ),
    ),
    ProviderPreset(
        id="openrouter",
        name="OpenRouter",
        kind="openai_compatible",
        endpoint="https://openrouter.ai/api/v1",
        requires_api_key=True,
        models=(
            SuggestedModel(id="openai/gpt-4.1-mini", name="OpenAI GPT-4.1 mini"),
            SuggestedModel(id="google/gemini-2.5-flash", name="Google Gemini 2.5 Flash"),
            SuggestedModel(id="anthropic/claude-sonnet-4", name="Anthropic Claude Sonnet 4"),
        ),
    ),
    ProviderPreset(
        id="gemini",
        name="Google Gemini",
        kind="openai_compatible",
        endpoint="https://generativelanguage.googleapis.com/v1beta/openai",
        requires_api_key=True,
        models=(
            SuggestedModel(id="gemini-3.8-flash", name="Gemini 3.8 Flash"),
            SuggestedModel(id="gemini-3.5-flash-lite", name="Gemini 3.5 Flash-Lite"),
        ),
    ),
    ProviderPreset(
        id="ollama",
        name="Ollama",
        kind="ollama",
        endpoint="http://127.0.0.1:11434",
        requires_api_key=False,
        models=(
            SuggestedModel(id="llama3.2:latest", name="Llama 3.2"),
            SuggestedModel(id="qwen3:latest", name="Qwen 3"),
        ),
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
