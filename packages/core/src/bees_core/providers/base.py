"""Transporte limitado e fábrica explícita; uma tentativa, sem fallback."""

import asyncio
import json
from collections.abc import AsyncIterator
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
from bees_core.providers.errors import ProviderError


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


class HTTPAdapter:
    def __init__(
        self,
        resolver: SecretResolver | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._resolver = resolver
        self._transport = transport

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
        except TimeoutError, httpx.TimeoutException:
            raise ProviderError("timeout", retryable=True) from None
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
            if status == 401:
                raise ProviderError("authentication_failed", upstream_status=401)
            if status == 403:
                raise ProviderError("access_denied", upstream_status=403)
            if status == 429:
                raise ProviderError("rate_limited", retryable=True, upstream_status=status)
            if status == 404:
                raise ProviderError("model_unavailable", upstream_status=status)
            if status >= 500:
                raise ProviderError("provider_unavailable", retryable=True, upstream_status=status)
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
) -> ProviderAdapter:
    from bees_core.providers.ollama import OllamaAdapter
    from bees_core.providers.openai import OpenAICompatibleAdapter

    if kind == "openai_compatible":
        return OpenAICompatibleAdapter(resolver, transport)
    if kind == "ollama":
        return OllamaAdapter(resolver, transport)
    raise ProviderError("invalid_config")
