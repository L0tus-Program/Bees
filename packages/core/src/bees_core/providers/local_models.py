"""Preparo de modelo local (Ollama) por decisão humana: listar, baixar e remover.

Nada é baixado automaticamente. O download exige confirmação explícita, roda no
backend local em loopback e expõe progresso agregado. Reinício do processo perde
o acompanhamento, não os blobs: o Ollama retoma o download parcial ao repetir.
"""

import asyncio
import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID, uuid4

import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator

from bees_core.providers.contracts import ConnectionConfig

DEFAULT_ENDPOINT = "http://127.0.0.1:11434"
MODEL_NAME = re.compile(
    r"^[a-z0-9][a-z0-9._-]{0,99}(/[a-z0-9][a-z0-9._-]{0,99})?(:[A-Za-z0-9][A-Za-z0-9._-]{0,63})?$"
)
LocalModelCode = Literal[
    "backend_unavailable",
    "model_not_found",
    "insufficient_storage",
    "pull_failed",
    "pull_not_found",
    "pull_conflict",
    "invalid_model",
    "model_in_use",
    "registry_unreachable",
]


class LocalModelError(Exception):
    def __init__(self, code: LocalModelCode) -> None:
        super().__init__(code)
        self.code = code


class SuggestedLocalModel(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    name: str
    download_bytes: int
    min_memory_gb: int
    tool_calls: bool
    source: str
    note: str


# Tamanhos aproximados da biblioteca Ollama (quantização padrão); servem para a
# decisão humana, não como garantia de compatibilidade com a máquina.
SUGGESTED: tuple[SuggestedLocalModel, ...] = (
    SuggestedLocalModel(
        id="qwen3:1.7b",
        name="Qwen 3 1.7B",
        download_bytes=1_400_000_000,
        min_memory_gb=4,
        tool_calls=True,
        source="https://ollama.com/library/qwen3",
        note="small",
    ),
    SuggestedLocalModel(
        id="llama3.2:3b",
        name="Llama 3.2 3B",
        download_bytes=2_000_000_000,
        min_memory_gb=6,
        tool_calls=True,
        source="https://ollama.com/library/llama3.2",
        note="balanced",
    ),
    SuggestedLocalModel(
        id="qwen3:4b",
        name="Qwen 3 4B",
        download_bytes=2_600_000_000,
        min_memory_gb=8,
        tool_calls=True,
        source="https://ollama.com/library/qwen3",
        note="balanced",
    ),
    SuggestedLocalModel(
        id="qwen3:8b",
        name="Qwen 3 8B",
        download_bytes=5_200_000_000,
        min_memory_gb=12,
        tool_calls=True,
        source="https://ollama.com/library/qwen3",
        note="quality",
    ),
)


def validate_model(value: str) -> str:
    if not MODEL_NAME.fullmatch(value) or "cloud" in value.lower():
        raise LocalModelError("invalid_model")
    return value if ":" in value.rsplit("/", 1)[-1] else f"{value}:latest"


def local_endpoint(value: str | None) -> str:
    try:
        return ConnectionConfig(kind="ollama", endpoint=value or DEFAULT_ENDPOINT).endpoint
    except ValueError:
        raise LocalModelError("backend_unavailable") from None


class PullInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client_request_id: UUID
    endpoint: str | None = Field(default=None, max_length=2048)
    model: str = Field(min_length=1, max_length=200)
    # A decisão humana é parte do contrato: sem confirmação nada é baixado.
    confirm_download: Literal[True]

    @field_validator("model")
    @classmethod
    def known_shape(cls, value: str) -> str:
        if not MODEL_NAME.fullmatch(value):
            raise ValueError("Identificador de modelo local inválido.")
        return value


@dataclass
class Pull:
    id: UUID
    client_request_id: UUID
    endpoint: str
    model: str
    started_at: datetime
    status: Literal["running", "completed", "failed", "cancelled"] = "running"
    phase: str = "starting"
    layers: dict[str, tuple[int, int]] = field(default_factory=dict)
    error: str | None = None
    finished_at: datetime | None = None
    task: asyncio.Task | None = None

    def view(self) -> dict:
        total = sum(item[0] for item in self.layers.values())
        completed = sum(min(item[1], item[0]) for item in self.layers.values())
        return {
            "id": str(self.id),
            "model": self.model,
            "endpoint": self.endpoint,
            "status": self.status,
            "phase": self.phase,
            "total_bytes": total,
            "completed_bytes": completed,
            "error": self.error,
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
        }


def _classify(message: str) -> LocalModelCode:
    text = message.lower()
    if "no space" in text or ("disk" in text and "full" in text):
        return "insufficient_storage"
    if any(
        marker in text
        for marker in (
            "dial tcp",
            "no such host",
            "connection refused",
            "i/o timeout",
            "network is unreachable",
            "proxyconnect",
            "tls:",
        )
    ):
        return "registry_unreachable"
    if "not exist" in text or "not found" in text or "manifest unknown" in text:
        return "model_not_found"
    return "pull_failed"


class LocalModelManager:
    """Acompanha downloads no processo da API; um download ativo por modelo/endpoint."""

    def __init__(
        self,
        transport: httpx.AsyncBaseTransport | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        timeout: float = 10.0,
    ) -> None:
        self._transport = transport
        self._clock = clock
        self._timeout = timeout
        self._pulls: dict[UUID, Pull] = {}

    def _client(self, endpoint: str, *, stream: bool = False) -> httpx.AsyncClient:
        timeout = httpx.Timeout(self._timeout, read=None if stream else self._timeout)
        return httpx.AsyncClient(
            base_url=endpoint,
            transport=self._transport,
            timeout=timeout,
            follow_redirects=False,
            trust_env=False,
        )

    async def status(self, endpoint: str | None) -> dict:
        endpoint = local_endpoint(endpoint)
        base = {
            "endpoint": endpoint,
            "suggested": [item.model_dump() for item in SUGGESTED],
            "pulls": [pull.view() for pull in self._pulls.values() if pull.endpoint == endpoint],
        }
        try:
            async with self._client(endpoint) as client:
                version = await client.get("/api/version")
                tags = await client.get("/api/tags")
            version.raise_for_status()
            tags.raise_for_status()
            models = tags.json().get("models")
            version_body = version.json()
        except httpx.HTTPError, ValueError, AttributeError:
            return base | {"reachable": False, "version": None, "models": []}
        installed = [
            {
                "name": item["name"],
                "size_bytes": item.get("size") if isinstance(item.get("size"), int) else None,
                "modified_at": item.get("modified_at")
                if isinstance(item.get("modified_at"), str)
                else None,
            }
            for item in (models if isinstance(models, list) else [])[:4096]
            if isinstance(item, dict) and isinstance(item.get("name"), str)
        ]
        value = version_body.get("version") if isinstance(version_body, dict) else None
        return base | {
            "reachable": True,
            "version": value if isinstance(value, str) else None,
            "models": installed,
        }

    def start(self, value: PullInput) -> Pull:
        endpoint = local_endpoint(value.endpoint)
        model = validate_model(value.model)
        for pull in self._pulls.values():
            if pull.client_request_id == value.client_request_id:
                if pull.model != model or pull.endpoint != endpoint:
                    raise LocalModelError("pull_conflict")
                return pull
        for pull in self._pulls.values():
            if pull.status == "running" and pull.model == model and pull.endpoint == endpoint:
                return pull
        pull = Pull(
            id=uuid4(),
            client_request_id=value.client_request_id,
            endpoint=endpoint,
            model=model,
            started_at=self._clock(),
        )
        self._pulls[pull.id] = pull
        pull.task = asyncio.get_running_loop().create_task(self._run(pull))
        return pull

    def get(self, pull_id: UUID) -> Pull:
        pull = self._pulls.get(pull_id)
        if pull is None:
            raise LocalModelError("pull_not_found")
        return pull

    async def cancel(self, pull_id: UUID) -> Pull:
        pull = self.get(pull_id)
        if pull.status == "running" and pull.task is not None:
            pull.task.cancel()
            try:
                await pull.task
            except asyncio.CancelledError:
                pass
        return pull

    async def delete(self, endpoint: str | None, model: str) -> None:
        endpoint = local_endpoint(endpoint)
        model = validate_model(model)
        if any(
            p.status == "running" and p.model == model and p.endpoint == endpoint
            for p in self._pulls.values()
        ):
            raise LocalModelError("model_in_use")
        try:
            async with self._client(endpoint) as client:
                response = await client.request("DELETE", "/api/delete", json={"model": model})
        except httpx.HTTPError:
            raise LocalModelError("backend_unavailable") from None
        if response.status_code == 404:
            raise LocalModelError("model_not_found")
        if response.status_code >= 400:
            raise LocalModelError("pull_failed")

    async def close(self) -> None:
        for pull in list(self._pulls.values()):
            if pull.status == "running":
                await self.cancel(pull.id)

    async def _run(self, pull: Pull) -> None:
        try:
            async with (
                self._client(pull.endpoint, stream=True) as client,
                client.stream(
                    "POST", "/api/pull", json={"model": pull.model, "stream": True}
                ) as response,
            ):
                if response.status_code >= 400:
                    body = (await response.aread())[:4096].decode("utf-8", "replace")
                    raise LocalModelError(
                        "model_not_found" if response.status_code == 404 else _classify(body)
                    )
                async for line in response.aiter_lines():
                    if not line.strip():
                        continue
                    if len(line) > 65536:
                        raise LocalModelError("pull_failed")
                    event = json.loads(line)
                    if not isinstance(event, dict):
                        raise LocalModelError("pull_failed")
                    if isinstance(event.get("error"), str):
                        raise LocalModelError(_classify(event["error"]))
                    status = str(event.get("status") or "")[:200]
                    digest = event.get("digest")
                    total, done = event.get("total"), event.get("completed")
                    if isinstance(digest, str) and isinstance(total, int) and total > 0:
                        pull.layers[digest[:200]] = (total, done if isinstance(done, int) else 0)
                        if len(pull.layers) > 256:
                            raise LocalModelError("pull_failed")
                    pull.phase = (
                        "downloading"
                        if status.startswith("pulling") and digest
                        else "verifying"
                        if "verifying" in status
                        else "writing"
                        if "writing" in status
                        else "success"
                        if status == "success"
                        else "manifest"
                    )
                if pull.phase != "success":
                    raise LocalModelError("pull_failed")
            pull.status = "completed"
        except asyncio.CancelledError:
            pull.status = "cancelled"
            raise
        except LocalModelError as error:
            pull.status, pull.error = "failed", error.code
        except httpx.ConnectError:
            pull.status, pull.error = "failed", "backend_unavailable"
        except httpx.HTTPError, ValueError:
            pull.status, pull.error = "failed", "pull_failed"
        finally:
            pull.finished_at = self._clock()
