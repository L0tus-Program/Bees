"""Onboarding autenticado, credenciais privadas e conversa de texto persistida."""

import hashlib
import hmac
import json
import secrets
import time
from collections.abc import Iterator
from contextlib import contextmanager
from threading import BoundedSemaphore, RLock
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError, field_validator

from bees_api.auth import COOKIE_NAME, require_session
from bees_core.models import Agent, Conversation
from bees_core.profiles import memory_enabled
from bees_core.providers.base import create_adapter
from bees_core.providers.catalog import (
    PROVIDERS,
    ProviderId,
    connection_for_provider,
    provider_preset,
)
from bees_core.providers.contracts import ConnectionConfig, ProviderConfig, validate_capabilities
from bees_core.providers.discovery import DiscoveryAdapter
from bees_core.providers.errors import ProviderError
from bees_core.providers.service import ProviderService
from bees_core.security.identity import Session
from bees_core.storage.store import NotFoundError, StateStore

_SESSION = Annotated[Session, Depends(require_session)]


def reject(code: str, message: str, status: int = 409) -> None:
    raise HTTPException(status, detail={"code": code, "message": message})


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)


class ModelInput(Input):
    agent_id: UUID | None = None
    config: ProviderConfig
    api_key: SecretStr | None = Field(default=None, min_length=1, max_length=8192, repr=False)


class DiscoverInput(Input):
    provider_id: ProviderId
    endpoint: str | None = Field(default=None, min_length=1, max_length=2048)
    api_key: SecretStr | None = Field(default=None, min_length=1, max_length=8192, repr=False)
    agent_id: UUID | None = None
    secret_ref: str | None = Field(default=None, max_length=128)


class CreateAgentInput(ModelInput):
    name: str = Field(min_length=1, max_length=200)
    purpose: str = Field(default="", max_length=4096)
    instructions: str = Field(default="", max_length=16384)
    validation_token: str = Field(min_length=43, max_length=43, repr=False)

    @field_validator("name")
    @classmethod
    def useful_name(cls, value: str) -> str:
        value = value.strip()
        if not value or any(ord(character) < 32 for character in value):
            raise ValueError("Informe um nome válido para a abelha.")
        return value


class ChatInput(Input):
    conversation_id: UUID
    task_id: UUID | None = None
    content: str = Field(min_length=1, max_length=32768)


class Receipts:
    """Confirmações efêmeras de teste, vinculadas à sessão e à configuração."""

    def __init__(self) -> None:
        self._items: dict[str, tuple[float, str]] = {}
        self._lock = RLock()
        self._slots = BoundedSemaphore(2)
        self._conversations: set[UUID] = set()

    @contextmanager
    def operation(self, conversation_id: UUID | None = None) -> Iterator[None]:
        with self._lock:
            if conversation_id is not None and conversation_id in self._conversations:
                reject("conversation_busy", "Esta conversa já está respondendo. Aguarde.")
            if conversation_id is not None:
                self._conversations.add(conversation_id)
        if not self._slots.acquire(blocking=False):
            with self._lock:
                self._conversations.discard(conversation_id)
            reject("model_busy", "Há chamadas em andamento. Aguarde e tente novamente.", 429)
        try:
            yield
        finally:
            self._slots.release()
            with self._lock:
                self._conversations.discard(conversation_id)

    def issue(self, signature: str) -> str:
        token = secrets.token_urlsafe(32)
        with self._lock:
            now = time.monotonic()
            self._items = {key: value for key, value in self._items.items() if value[0] > now}
            if len(self._items) >= 128:
                reject("model_busy", "Limite de testes em andamento atingido.", 429)
            self._items[token] = (now + 300, signature)
        return token

    def consume(self, token: str, signature: str) -> None:
        with self._lock:
            item = self._items.get(token)
            if (
                item is None
                or item[0] <= time.monotonic()
                or not secrets.compare_digest(item[1], signature)
            ):
                reject("validation_required", "Teste esta configuração novamente antes de salvar.")
            del self._items[token]


def _binding(request: Request) -> str:
    return request.cookies[COOKIE_NAME]


def _signature(body: ModelInput, binding: str, *, creation: bool = False) -> str:
    data = {
        "config": body.config.model_dump(mode="json"),
        "api_key": body.api_key.get_secret_value() if body.api_key is not None else None,
    }
    if body.agent_id is not None:
        data["agent_id"] = str(body.agent_id)
    if creation:
        data.update(name=body.name, purpose=body.purpose, instructions=body.instructions)
    raw = json.dumps(data, sort_keys=True, ensure_ascii=False, allow_nan=False).encode("utf-8")
    # A impressão persistida não permite testar chaves fracas sem conhecer o cookie privado.
    return hmac.new(binding.encode(), raw, hashlib.sha256).hexdigest()


class InputResolver:
    def __init__(self, secret: SecretStr, fallback) -> None:
        self._secret = secret
        self._fallback = fallback

    def resolve(self, reference: str) -> SecretStr:
        return (
            self._secret
            if reference == "env:BEES_REQUEST_KEY"
            else self._fallback.resolve(reference)
        )


def _connection(request: Request, body: ModelInput):
    config = ProviderConfig.model_validate(body.config.model_dump())
    validate_capabilities(config)
    resolver = request.app.state.resolver
    if body.api_key is not None:
        if config.kind == "ollama" or config.secret_ref is not None:
            raise ProviderError("invalid_config")
        if not request.app.state.vault.status().available:
            raise ProviderError("secret_unavailable")
        config = ProviderConfig.model_validate(
            config.model_dump()
            | {
                "secret_ref": "env:BEES_REQUEST_KEY",
            }
        )
        resolver = InputResolver(body.api_key, resolver)
    return config, resolver


def validate_existing_reference(agent: Agent, config: ConnectionConfig) -> None:
    """Credencial existente só é reaproveitada na mesma conexão explicitamente escolhida."""
    if config.secret_ref is None:
        return
    try:
        previous = (
            ProviderConfig.model_validate(agent.provider_config) if agent.provider_config else None
        )
    except ValidationError:
        raise ProviderError("invalid_config") from None
    if (
        previous is None
        or config.secret_ref != previous.secret_ref
        or config.kind != previous.kind
        or config.endpoint != previous.endpoint
    ):
        raise ProviderError("invalid_config")


def _summary(unit, agent: Agent) -> dict:
    conversations = unit.conversations.list(agent_id=agent.id, status="active", limit=1)
    return {
        "id": str(agent.id),
        "name": agent.name,
        "purpose": agent.purpose,
        "instructions": agent.instructions,
        "revision": agent.revision,
        "status": agent.status,
        "memory_enabled": memory_enabled(agent),
        "provider_config": agent.provider_config or None,
        "conversation_id": str(conversations[0].id) if conversations else None,
    }


router = APIRouter(prefix="/api/v1", tags=["onboarding"])


@router.get("/providers")
def providers(request: Request, session: _SESSION) -> dict:
    return {"providers": [provider.model_dump(mode="json") for provider in PROVIDERS]}


@router.post("/models/discover")
async def discover_models(body: DiscoverInput, request: Request, session: _SESSION) -> dict:
    config = connection_for_provider(
        body.provider_id, endpoint=body.endpoint, secret_ref=body.secret_ref
    )
    previous = None
    if body.secret_ref is not None and (body.agent_id is None or body.api_key is not None):
        raise ProviderError("invalid_config")
    if body.agent_id is not None:
        with request.app.state.store.transaction(write=False) as unit:
            previous = unit.agents.get(body.agent_id)
            if previous is None:
                raise NotFoundError("Abelha não encontrada.")
        if body.secret_ref is not None:
            validate_existing_reference(previous, config)
    resolver = request.app.state.resolver
    if body.api_key is not None:
        if config.kind == "ollama":
            raise ProviderError("invalid_config")
        config = type(config).model_validate(
            config.model_dump() | {"secret_ref": "env:BEES_REQUEST_KEY"}
        )
        resolver = InputResolver(body.api_key, resolver)
    if provider_preset(body.provider_id).requires_api_key and config.secret_ref is None:
        raise ProviderError("invalid_config")
    with request.app.state.receipts.operation():
        models = await DiscoveryAdapter(resolver).discover(config, body.provider_id)
    require_session(request)
    if previous is not None:
        with request.app.state.store.transaction(write=False) as unit:
            current = unit.agents.get(previous.id)
            if current is None or current.revision != previous.revision:
                raise ProviderError("state_conflict")
    return {"provider_id": body.provider_id, "models": [model.model_dump() for model in models]}


@router.get("/onboarding")
def onboarding(request: Request, session: _SESSION) -> dict:
    with request.app.state.store.transaction(write=False) as unit:
        records = unit.agents.list(limit=1000)
        agents = [_summary(unit, agent) for agent in records]
    return {
        "agents": agents,
        "has_more": len(agents) == 1000,
        "vault": request.app.state.vault.status().model_dump(mode="json"),
        "environments": [
            {"id": "bee_computer", "status": "planned"},
            {"id": "personal_computer", "status": "planned"},
        ],
        "next_steps": ["conversation", "memory", "tasks", "environments"],
    }


@router.post("/models/test")
async def test_model(body: ModelInput, request: Request, session: _SESSION) -> dict:
    previous = None
    if body.agent_id is not None:
        with request.app.state.store.transaction(write=False) as unit:
            previous = unit.agents.get(body.agent_id)
            if previous is None:
                raise NotFoundError("Abelha não encontrada.")
        validate_existing_reference(previous, body.config)
    elif body.config.secret_ref and body.config.secret_ref.startswith("vault:"):
        raise ProviderError("invalid_config")
    config, resolver = _connection(request, body)
    with request.app.state.receipts.operation():
        diagnostic = await create_adapter(config.kind, resolver).diagnose(config)
    # Sessão pode ter sido encerrada enquanto o provedor respondia.
    require_session(request)
    if previous is not None:
        with request.app.state.store.transaction(write=False) as unit:
            current = unit.agents.get(previous.id)
            if current is None or current.revision != previous.revision:
                raise ProviderError("state_conflict")
    token = request.app.state.receipts.issue(_signature(body, _binding(request)))
    return diagnostic.model_dump(mode="json") | {"validation_token": token}


@router.post("/agents", status_code=201)
def create_agent(body: CreateAgentInput, request: Request, session: _SESSION) -> dict:
    if body.agent_id is not None:
        raise ProviderError("invalid_config")
    store: StateStore = request.app.state.store
    binding = _binding(request)
    token_hash = hashlib.sha256(body.validation_token.encode()).hexdigest()
    session_hash = hashlib.sha256(binding.encode()).hexdigest()
    request_hash = _signature(body, binding, creation=True)
    # Replay de criação confirmada funciona mesmo depois do reinício do processo.
    with store.transaction(write=False) as unit:
        rows = unit.agents.find_onboarding_receipt(token_hash)
        if rows:
            agent = rows[0]
            command = agent.metadata.get("onboarding_command", {})
            if (
                len(rows) != 1
                or command.get("session_hash") != session_hash
                or not secrets.compare_digest(command.get("request_hash", ""), request_hash)
            ):
                reject("command_conflict", "O pedido já foi usado com outros parâmetros.")
            return _summary(unit, agent)

    _connection(request, body)
    request.app.state.receipts.consume(body.validation_token, _signature(body, binding))
    reference = None
    committed = False
    try:
        config = body.config
        if body.api_key is not None:
            reference = request.app.state.vault.put(body.api_key)
            config = ProviderConfig.model_validate(config.model_dump() | {"secret_ref": reference})
        agent = Agent(
            name=body.name,
            purpose=body.purpose,
            instructions=body.instructions,
            provider_config=config.model_dump(mode="json"),
            metadata={
                "onboarding_command": {
                    "receipt_hash": token_hash,
                    "session_hash": session_hash,
                    "request_hash": request_hash,
                }
            },
        )
        conversation = Conversation(agent_id=agent.id)
        with store.transaction(source="web_onboarding") as unit:
            unit.agents.create(agent)
            unit.conversations.create(conversation)
            result = _summary(unit, agent)
        committed = True
        return result
    finally:
        if reference is not None and not committed:
            request.app.state.vault.delete(reference)


@router.get("/agents/{agent_id}/messages")
def messages(agent_id: UUID, conversation_id: UUID, request: Request, session: _SESSION) -> dict:
    with request.app.state.store.transaction(write=False) as unit:
        agent = unit.agents.get(agent_id)
        conversation = unit.conversations.get(conversation_id)
        if agent is None or conversation is None or conversation.agent_id != agent.id:
            raise NotFoundError("Conversa não encontrada.")
        # Exibir a janela recente; o driver conserva o histórico completo.
        count = unit.messages.count(conversation_id=conversation.id)
        records = unit.messages.list(
            conversation_id=conversation.id,
            limit=1000,
            offset=max(count - 1000, 0),
        )
    return {
        "conversation_id": str(conversation_id),
        "has_more": count > 1000,
        "messages": [
            {
                "id": str(record.id),
                "role": record.role,
                "content": record.content,
                "created_at": record.created_at.isoformat(),
            }
            for record in records
        ],
    }


@router.post("/agents/{agent_id}/chat")
async def chat(agent_id: UUID, body: ChatInput, request: Request, session: _SESSION) -> dict:
    with request.app.state.receipts.operation(body.conversation_id):
        provider: ProviderService = request.app.state.providers
        response = await provider.chat(
            agent_id, body.conversation_id, body.content, task_id=body.task_id
        )
    require_session(request)
    return response.model_dump(mode="json")
