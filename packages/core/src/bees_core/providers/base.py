"""Transporte limitado e fábrica explícita; uma tentativa, sem fallback."""

import asyncio
import json
import re
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any

import httpx
from pydantic import SecretStr

from bees_core.providers.contracts import (
    ConnectionConfig,
    ProviderAdapter,
    ProviderKind,
    SecretResolver,
)
from bees_core.providers.errors import ProviderError, public_provider_message


def validate_bearer_secret(value: SecretStr) -> str:
    """Formato privado de header; validar não comprova aceitação pelo provedor."""
    if not isinstance(value, SecretStr):
        raise ProviderError("invalid_secret")
    secret = value.get_secret_value()
    if (
        not isinstance(secret, str)
        or not secret.strip()
        or len(secret) > 8192
        or any(ord(char) < 32 or ord(char) > 126 for char in secret)
    ):
        raise ProviderError("invalid_secret")
    return secret


def encode_json(body: Any, limit: int) -> bytes:
    try:
        encoded = json.dumps(
            body, ensure_ascii=False, allow_nan=False, separators=(",", ":")
        ).encode("utf-8")
    except ValueError, TypeError, RecursionError:
        raise ProviderError("invalid_request") from None
    if len(encoded) > limit:
        raise ProviderError("request_too_large")
    return encoded


def _strict_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Chave JSON duplicada.")
        result[key] = value
    return result


def decode_json(raw: bytes) -> dict[str, Any]:
    try:
        value = json.loads(
            raw,
            object_pairs_hook=_strict_pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError()),
        )
    except ValueError, UnicodeDecodeError, RecursionError:
        raise ProviderError("invalid_response") from None
    if not isinstance(value, dict):
        raise ProviderError("invalid_response")
    return value


async def rejection(response: httpx.Response) -> ProviderError:
    """Classifica uma recusa limitada e remove dados privados do diagnóstico."""
    code = {
        401: "authentication_failed",
        403: "access_denied",
        404: "model_unavailable",
        429: "rate_limited",
    }.get(
        response.status_code,
        "provider_unavailable" if response.status_code >= 500 else "provider_rejected",
    )
    parameter = None
    provider_message = None
    if response.headers.get("content-encoding", "identity").lower() == "identity":
        try:
            raw = bytearray()
            if response.is_stream_consumed:
                if len(response.content) <= 8192:
                    raw.extend(response.content)
            else:
                async for chunk in response.aiter_raw():
                    if len(raw) + len(chunk) > 8192:
                        raw.clear()
                        break
                    raw.extend(chunk)
            if raw:
                error = decode_json(bytes(raw)).get("error")
                if isinstance(error, str):
                    error = {"message": error}
                if isinstance(error, dict):
                    private = []
                    authorization = response.request.headers.get("authorization", "")
                    if authorization:
                        private.extend([authorization, authorization.removeprefix("Bearer ")])

                    # Nomes de modelo e campos de schema ajudam no diagnóstico.
                    def collect(value, sensitive=False):
                        if isinstance(value, dict):
                            for key, child in value.items():
                                collect(
                                    child,
                                    sensitive
                                    or key
                                    in {"content", "text", "arguments", "images", "image_url"},
                                )
                        elif isinstance(value, list):
                            for child in value:
                                collect(child, sensitive)
                        elif sensitive and isinstance(value, str):
                            private.append(value)

                    try:
                        collect(decode_json(response.request.content))
                    except ProviderError:
                        pass
                    provider_message = public_provider_message(error.get("message"), private)
                    field = error.get("param")
                    if isinstance(field, str):
                        field = re.sub(r"\[\d{1,3}\]", "", field)
                        if field in {
                            "tools",
                            "tools.function.parameters",
                            "tools.function.strict",
                            "messages",
                            "model",
                            "store",
                            "stream",
                        }:
                            parameter = field
                    reason = error.get("code")
                    if response.status_code not in (400, 422):
                        pass
                    elif reason == "context_length_exceeded":
                        code = "context_window_exceeded"
                    elif (
                        reason in ("invalid_function_parameters", "invalid_tool_schema")
                        or parameter == "tools.function.parameters"
                    ):
                        code = "tool_schema_rejected"
                    elif reason in ("unsupported_parameter", "unsupported_value"):
                        code = (
                            "model_tools_unsupported"
                            if parameter == "tools"
                            else "unsupported_model_parameter"
                        )
        except ProviderError, httpx.RequestError:
            pass
    return ProviderError(
        code,
        upstream_status=response.status_code,
        parameter=parameter,
        provider_message=provider_message,
        retryable=response.status_code == 429 or response.status_code >= 500,
    )


class HTTPAdapter:
    def __init__(
        self,
        resolver: SecretResolver | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        before_generation: Callable[[], None] | None = None,
        *,
        tool_proposals: bool = False,
    ) -> None:
        if type(tool_proposals) is not bool:
            raise ValueError("Modo de propostas exige composição explícita.")
        self._resolver = resolver
        self._transport = transport
        self._generation_guard = before_generation
        self._tool_proposals = tool_proposals

    def _authorize_generation(self) -> None:
        if self._generation_guard is not None:
            self._generation_guard()

    @asynccontextmanager
    async def _session(self, config: ConnectionConfig) -> AsyncIterator[httpx.AsyncClient]:
        headers = {"Accept": "application/json", "Accept-Encoding": "identity"}
        if config.secret_ref is not None:
            if self._resolver is None:
                raise ProviderError("secret_unavailable")
            try:
                secret = validate_bearer_secret(self._resolver.resolve(config.secret_ref))
            except Exception:
                raise ProviderError("secret_unavailable") from None
            headers["Authorization"] = f"Bearer {secret}"
        try:
            async with asyncio.timeout(config.deadline_seconds):
                async with httpx.AsyncClient(
                    timeout=httpx.Timeout(config.timeout_seconds),
                    follow_redirects=False,
                    trust_env=False,
                    transport=self._transport,
                    headers=headers,
                ) as client:
                    yield client
        except httpx.ConnectTimeout:
            raise ProviderError("timeout", retryable=True, undelivered=True) from None
        except TimeoutError, httpx.TimeoutException:
            raise ProviderError("timeout", retryable=True) from None
        except httpx.ConnectError:
            # Falha ao estabelecer a conexão: o pedido nunca foi enviado.
            raise ProviderError("connection_failed", retryable=True, undelivered=True) from None
        except httpx.RequestError:
            raise ProviderError("connection_failed", retryable=True) from None

    async def _json(
        self,
        client: httpx.AsyncClient,
        config: ConnectionConfig,
        method: str,
        route: str,
        *,
        body: dict | None = None,
    ) -> dict[str, Any]:
        encoded = encode_json(body, config.max_request_bytes) if body is not None else None
        headers = {"Content-Type": "application/json"} if encoded is not None else None
        async with client.stream(
            method, f"{config.endpoint}/{route}", content=encoded, headers=headers
        ) as response:
            status = response.status_code
            if status < 200 or status > 599:
                raise ProviderError("invalid_response")
            if 300 <= status < 400:
                raise ProviderError("redirect_refused")
            if status >= 400:
                raise await rejection(response)
            if status < 200 or status >= 300:
                raise ProviderError("provider_rejected", upstream_status=status)
            if response.headers.get("content-encoding", "identity").lower() != "identity":
                # Não descompactar respostas externas sem limite anterior à expansão.
                raise ProviderError("invalid_response")
            content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
            if content_type != "application/json" and not content_type.endswith("+json"):
                raise ProviderError("invalid_response")
            length = response.headers.get("content-length")
            if length is not None:
                try:
                    if int(length) < 0:
                        raise ValueError()
                    if int(length) > config.max_response_bytes:
                        raise ProviderError("response_too_large")
                except ValueError:
                    raise ProviderError("invalid_response") from None
            chunks = bytearray()
            if response.is_stream_consumed:
                if len(response.content) > config.max_response_bytes:
                    raise ProviderError("response_too_large")
                return decode_json(response.content)
            async for chunk in response.aiter_raw():
                if len(chunks) + len(chunk) > config.max_response_bytes:
                    raise ProviderError("response_too_large")
                chunks.extend(chunk)
            return decode_json(bytes(chunks))


def create_adapter(
    kind: ProviderKind,
    resolver: SecretResolver | None = None,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    before_generation: Callable[[], None] | None = None,
    tool_proposals: bool = False,
) -> ProviderAdapter:
    from bees_core.providers.ollama import OllamaAdapter
    from bees_core.providers.openai import OpenAICompatibleAdapter

    if kind == "openai_compatible":
        return OpenAICompatibleAdapter(
            resolver, transport, before_generation, tool_proposals=tool_proposals
        )
    if kind == "ollama":
        return OllamaAdapter(resolver, transport, before_generation, tool_proposals=tool_proposals)
    raise ProviderError("invalid_config")
