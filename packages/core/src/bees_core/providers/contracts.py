"""Texto e function calls normalizados; não executa ferramentas nem concede acesso."""

import ipaddress
import json
from typing import Literal, Protocol, Self
from urllib.parse import urlsplit, urlunsplit

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from jsonschema.exceptions import ValidationError as SchemaValidationError
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    SecretStr,
    ValidationError,
    field_validator,
    model_validator,
)

from bees_core.providers.errors import ProviderError

ProviderKind = Literal["openai_compatible", "ollama"]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)


class ProviderCapabilities(Contract):
    text: bool = True
    tool_calls: bool = False


CapabilityRequirements = ProviderCapabilities


def is_loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


class ProviderConfig(Contract):
    kind: ProviderKind
    endpoint: str = Field(min_length=1, max_length=2048)
    model: str = Field(min_length=1, max_length=200)
    secret_ref: str | None = Field(
        default=None, max_length=128, pattern=r"^env:[A-Za-z_][A-Za-z0-9_]*$"
    )
    capabilities: ProviderCapabilities
    timeout_seconds: float = Field(default=30, gt=0, le=300, allow_inf_nan=False)
    deadline_seconds: float = Field(default=60, gt=0, le=600, allow_inf_nan=False)
    max_response_bytes: int = Field(default=1048576, ge=1024, le=16777216)
    max_request_bytes: int = Field(default=1048576, ge=1024, le=16777216)

    @field_validator("model")
    @classmethod
    def explicit_model(cls, value: str) -> str:
        if value != value.strip() or any(ord(char) < 32 for char in value):
            raise ValueError("Modelo precisa ter identificador explícito sem controles.")
        return value

    @model_validator(mode="after")
    def safe_endpoint(self) -> Self:
        try:
            parsed = urlsplit(self.endpoint)
            host = parsed.hostname
            port = parsed.port
        except ValueError:
            raise ValueError("Endpoint inválido.") from None
        if (
            not host
            or parsed.scheme not in ("http", "https")
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or "\\" in self.endpoint
            or any(ord(char) <= 32 for char in self.endpoint)
            or (port is not None and not 1 <= port <= 65535)
            or any(segment in (".", "..") for segment in parsed.path.split("/"))
        ):
            raise ValueError("Endpoint deve ser URL explícita sem credenciais/query/fragmento.")
        if parsed.scheme == "http" and not is_loopback(host):
            raise ValueError("HTTP sem TLS exige loopback.")
        if self.kind == "ollama":
            if not is_loopback(host):
                raise ValueError("Ollama exige endpoint loopback neste adaptador.")
            if self.secret_ref is not None:
                raise ValueError("Ollama local não aceita credencial de serviço cloud.")
            if "cloud" in self.model.lower() or "@" in self.model or "://" in self.model:
                raise ValueError("Ollama exige identificador de modelo local.")
        normalized = urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", ""))
        object.__setattr__(self, "endpoint", normalized)
        return self


class ToolCall(Contract):
    id: str = Field(min_length=1, max_length=200, pattern=r"^[A-Za-z0-9_-]+$")
    name: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    arguments: dict[str, JsonValue]


def validate_schema(schema: dict[str, JsonValue]) -> None:
    encoded = json.dumps(schema, allow_nan=False)
    if len(encoded.encode("utf-8")) > 65536 or schema.get("type") != "object":
        raise ValueError("JSONSchema deve descrever objeto e ter até 64 KiB.")
    count = 0

    def inspect(value: JsonValue, depth: int = 0) -> None:
        nonlocal count
        count += 1
        if count > 2048 or depth > 20:
            raise ValueError("JSONSchema excede complexidade suportada.")
        if isinstance(value, dict):
            for key, child in value.items():
                if key in ("$ref", "$dynamicRef", "$recursiveRef"):
                    # Subconjunto inicial sem referências: elimina rede e ciclos de resolução.
                    raise ValueError("Referências JSONSchema não são suportadas neste recorte.")
                if key in (
                    "pattern",
                    "patternProperties",
                    "uniqueItems",
                    "allOf",
                    "anyOf",
                    "oneOf",
                    "not",
                    "if",
                    "then",
                    "else",
                    "contains",
                    "dependentSchemas",
                    "unevaluatedItems",
                    "unevaluatedProperties",
                ):
                    raise ValueError(
                        "Keyword JSONSchema fora do subconjunto limitado deste recorte."
                    )
                inspect(child, depth + 1)
        elif isinstance(value, list):
            for child in value:
                inspect(child, depth + 1)

    inspect(schema)
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError:
        raise ValueError("JSONSchema inválido.") from None


class ToolDefinition(Contract):
    name: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    description: str = Field(default="", max_length=4096)
    parameters: dict[str, JsonValue]

    @field_validator("parameters")
    @classmethod
    def checked_schema(cls, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        validate_schema(value)
        return value


class ChatMessage(Contract):
    role: Literal["system", "user", "assistant", "tool"]
    content: str | None = None
    tool_calls: list[ToolCall] = Field(default_factory=list, max_length=64)
    tool_call_id: str | None = None

    @model_validator(mode="after")
    def valid_role_payload(self) -> Self:
        if self.role != "assistant" and self.tool_calls:
            raise ValueError("Somente assistant pode conter chamadas de ferramenta.")
        if self.role == "tool":
            if not self.tool_call_id or self.content is None:
                raise ValueError("Resultado de ferramenta exige conteúdo e tool_call_id.")
        elif self.tool_call_id is not None:
            raise ValueError("tool_call_id só pertence a resultado de ferramenta.")
        if self.content is None and not self.tool_calls:
            raise ValueError("Mensagem exige texto ou chamada de ferramenta.")
        if len({call.id for call in self.tool_calls}) != len(self.tool_calls):
            raise ValueError("IDs de chamadas precisam ser únicos.")
        return self


def validate_calls(calls: list[ToolCall], tools: list[ToolDefinition]) -> None:
    definitions = {tool.name: tool for tool in tools}
    try:
        for call in calls:
            tool = definitions.get(call.name)
            if tool is None:
                raise ValueError("Ferramenta não declarada.")
            nodes = 0

            def bound(value: JsonValue, depth: int = 0) -> None:
                nonlocal nodes
                nodes += 1
                if nodes > 2048 or depth > 20:
                    raise ValueError("Argumentos excedem complexidade suportada.")
                if isinstance(value, (list, dict)):
                    if len(value) > 256:
                        raise ValueError("Container de argumentos excede limite.")
                    for child in value.values() if isinstance(value, dict) else value:
                        bound(child, depth + 1)

            bound(call.arguments)
            Draft202012Validator(tool.parameters).validate(call.arguments)
            json.dumps(call.arguments, allow_nan=False)
    except ValueError, TypeError, SchemaValidationError, RecursionError:
        raise ProviderError("invalid_response") from None


class ChatRequest(Contract):
    messages: list[ChatMessage] = Field(min_length=1, max_length=10000)
    tools: list[ToolDefinition] = Field(default_factory=list, max_length=128)
    stream: bool = False

    @model_validator(mode="after")
    def valid_history(self) -> Self:
        if len({tool.name for tool in self.tools}) != len(self.tools):
            raise ValueError("Ferramentas precisam ter nomes únicos.")
        seen: set[str] = set()
        pending: set[str] = set()
        for message in self.messages:
            if message.role == "tool":
                if message.tool_call_id not in pending:
                    raise ValueError("Resultado sem chamada pendente correspondente.")
                pending.remove(message.tool_call_id)
            else:
                if pending:
                    raise ValueError(
                        "Resultados de ferramentas pendentes antes da próxima mensagem."
                    )
                for call in message.tool_calls:
                    if call.id in seen:
                        raise ValueError("ID de chamada reutilizado no histórico.")
                    seen.add(call.id)
                    pending.add(call.id)
        if pending:
            raise ValueError("Conclua resultados de ferramentas antes de pedir outra resposta.")
        return self


class Usage(Contract):
    kind: Literal["reported", "estimated", "unknown"] = "unknown"
    input_tokens: int | None = Field(default=None, ge=0, strict=True)
    output_tokens: int | None = Field(default=None, ge=0, strict=True)
    total_tokens: int | None = Field(default=None, ge=0, strict=True)

    @model_validator(mode="after")
    def coherent_counts(self) -> Self:
        counts = (self.input_tokens, self.output_tokens, self.total_tokens)
        if self.kind == "unknown" and any(value is not None for value in counts):
            raise ValueError("Uso desconhecido não pode inventar contagens.")
        if self.kind != "unknown" and all(value is None for value in counts):
            raise ValueError("Uso informado/estimado exige ao menos uma contagem.")
        if all(value is not None for value in counts):
            if self.total_tokens != self.input_tokens + self.output_tokens:
                raise ValueError("Contagens de uso inconsistentes.")
        return self


class ChatResponse(Contract):
    message: ChatMessage
    finish_reason: Literal["stop", "length", "tool_calls", "content_filter"]
    usage: Usage = Field(default_factory=Usage)

    @field_validator("message")
    @classmethod
    def assistant_only(cls, value: ChatMessage) -> ChatMessage:
        if value.role != "assistant":
            raise ValueError("Resposta do backend precisa ser assistant.")
        return value

    @model_validator(mode="after")
    def coherent_finish_reason(self) -> Self:
        if bool(self.message.tool_calls) != (self.finish_reason == "tool_calls"):
            raise ValueError("finish_reason não corresponde às chamadas de ferramenta.")
        return self


class Diagnostic(Contract):
    status: Literal["ok"] = "ok"
    code: str = "model_available"
    message: str = "Modelo disponível; capacidades dependem do contrato declarado."
    capabilities: ProviderCapabilities


class SecretResolver(Protocol):
    def resolve(self, secret_ref: str) -> SecretStr: ...


class ProviderAdapter(Protocol):
    async def complete(self, config: ProviderConfig, request: ChatRequest) -> ChatResponse: ...

    async def diagnose(self, config: ProviderConfig) -> Diagnostic: ...


def validate_capabilities(
    config: ProviderConfig,
    request: ChatRequest | None = None,
    requirements: ProviderCapabilities | None = None,
) -> None:
    try:
        config = ProviderConfig.model_validate(config.model_dump())
        if requirements is not None:
            requirements = ProviderCapabilities.model_validate(requirements.model_dump())
    except ValidationError, ValueError, TypeError:
        raise ProviderError("invalid_config") from None
    if not config.capabilities.text or (
        requirements and requirements.text and not config.capabilities.text
    ):
        raise ProviderError("unsupported_capability")
    needs_tools = bool(requirements and requirements.tool_calls)
    if request is not None:
        if request.stream:
            raise ProviderError("unsupported_capability")
        needs_tools |= bool(
            request.tools
            or any(message.tool_calls or message.role == "tool" for message in request.messages)
        )
    if needs_tools and not config.capabilities.tool_calls:
        raise ProviderError("unsupported_capability")


def snapshots(
    config: ProviderConfig,
    request: ChatRequest | None = None,
    requirements: ProviderCapabilities | None = None,
) -> tuple[ProviderConfig, ChatRequest | None]:
    try:
        checked_config = ProviderConfig.model_validate(config.model_dump())
        checked_request = ChatRequest.model_validate(request.model_dump()) if request else None
        checked_requirements = (
            ProviderCapabilities.model_validate(requirements.model_dump()) if requirements else None
        )
    except ValidationError, ValueError, TypeError:
        raise ProviderError("invalid_config" if request is None else "invalid_request") from None
    validate_capabilities(checked_config, checked_request, checked_requirements)
    return checked_config, checked_request


def validate_request(
    config: ProviderConfig, request: ChatRequest, requirements: ProviderCapabilities | None = None
) -> tuple[ProviderConfig, ChatRequest]:
    checked_config, checked_request = snapshots(config, request, requirements)
    assert checked_request is not None
    return checked_config, checked_request
