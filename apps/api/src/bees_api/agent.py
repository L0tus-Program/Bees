"""Tarefas do agente com ferramentas e canal humano das decisões.

Nenhum endpoint executa ferramentas: a API cria tarefas e registra decisões;
somente o worker prepara intenções e despacha efeitos com claim atual.
"""

import hashlib
from typing import Annotated
from uuid import UUID, uuid5

from fastapi import APIRouter, Depends, Query, Request
from pydantic import Field, field_validator

from bees_api.auth import require_session
from bees_api.onboarding import Input
from bees_api.worker_status import worker_status
from bees_core.agent_tools.catalog import bundles, manifest_for
from bees_core.agent_tools.cycle import (
    DEFAULT_ACTIVE_SECONDS,
    DEFAULT_MAX_CALLS,
    AgentTaskInput,
    AgentTaskService,
    available_tools,
)
from bees_core.agent_tools.service import AgentToolDecision, AgentToolRevoke, AgentToolService
from bees_core.providers.contracts import ChatMessage
from bees_core.providers.errors import ProviderError
from bees_core.providers.service import ProviderService
from bees_core.security.identity import Session
from bees_core.storage.store import NotFoundError
from bees_core.tools import PluginManifest, ToolService

_SESSION = Annotated[Session, Depends(require_session)]
router = APIRouter(prefix="/api/v1/agents/{agent_id}", tags=["agent"])
CONTEXT_MESSAGES = 12
CONTEXT_CHARS = 8000


class RecommendedInput(Input):
    client_request_id: UUID


class AgentTaskBody(Input):
    client_request_id: UUID
    title: str = Field(min_length=1, max_length=200)
    objective: str = Field(min_length=1, max_length=12000)
    expected_result: str = Field(default="", max_length=4000)
    max_calls: int = Field(default=DEFAULT_MAX_CALLS, ge=1, le=50, strict=True)
    max_active_seconds: int = Field(default=DEFAULT_ACTIVE_SECONDS, ge=30, le=1800, strict=True)


class AgentMessageBody(Input):
    client_request_id: UUID
    content: str = Field(min_length=1, max_length=12000)
    max_calls: int = Field(default=DEFAULT_MAX_CALLS, ge=1, le=50, strict=True)
    max_active_seconds: int = Field(default=DEFAULT_ACTIVE_SECONDS, ge=30, le=1800, strict=True)

    @field_validator("content")
    @classmethod
    def useful(cls, value):
        if not value.strip() or "\x00" in value:
            raise ValueError("Mensagem vazia.")
        return value.strip()


def _tools_view(unit, agent_id):
    return [
        {
            "tool_name": spec.name,
            "function": spec.function,
            "target": spec.target,
            "effect": spec.effect,
            "default_policy": spec.default_effect,
            "plugin_id": str(plugin_id),
        }
        for spec, plugin_id in available_tools(unit, agent_id)
    ]


@router.get("/agent/capabilities")
def capabilities(agent_id: UUID, request: Request, session: _SESSION) -> dict:
    store = request.app.state.store
    with store.transaction(write=False) as unit:
        agent = ProviderService._agent(unit, agent_id)
        try:
            config = ProviderService._validated_config(agent.provider_config)
            tool_calls = bool(config.capabilities.tool_calls)
        except ProviderError:
            tool_calls = False
        tools = _tools_view(unit, agent.id)
        installed = {
            ToolService._manifest(plugin).plugin_key for plugin in unit.plugins.list(limit=1000)
        }
    status = worker_status(store)
    return {
        "tool_calls_supported": tool_calls,
        "tools": tools,
        "available": tool_calls and bool(tools) and agent.status == "active",
        "recommended_installed": all(bundle["plugin_key"] in installed for bundle in bundles()),
        "worker": status,
        "defaults": {"max_calls": DEFAULT_MAX_CALLS, "max_active_seconds": DEFAULT_ACTIVE_SECONDS},
    }


@router.post("/agent/recommended-tools")
def enable_recommended(
    agent_id: UUID, body: RecommendedInput, request: Request, session: _SESSION
) -> dict:
    """Ação humana explícita: instala, habilita e concede os pacotes recomendados."""
    database = request.app.state.database
    tools = ToolService(database)
    with request.app.state.store.transaction(write=False) as unit:
        if unit.agents.get(agent_id) is None:
            raise NotFoundError("Abelha não encontrada.")
        plugins = unit.plugins.list(limit=1000)
    by_key = {}
    for plugin in plugins:
        try:
            key = ToolService._manifest(plugin).plugin_key
        except Exception:
            continue
        by_key.setdefault(key, plugin)
    granted = []
    for bundle in bundles():
        manifest = PluginManifest.model_validate(manifest_for(bundle)).model_dump(mode="json")
        plugin = by_key.get(bundle["plugin_key"])
        if plugin is None:
            plugin = tools.install(
                {
                    "client_request_id": uuid5(body.client_request_id, bundle["plugin_key"]),
                    "manifest": manifest,
                }
            )
        if not plugin.enabled:
            plugin = tools.update_plugin(
                plugin.id, {"expected_revision": plugin.revision, "enabled": True}
            )
        current = {
            row["tool_name"]: row
            for row in tools.list_tools(agent_id, limit=1000)
            if row["plugin_id"] == str(plugin.id)
        }
        for name in bundle["tools"]:
            row = current.get(name)
            if row is None or not row["granted"]:
                tools.set_grant(
                    agent_id,
                    plugin.id,
                    name,
                    {
                        "expected_revision": row["grant_revision"] if row else 0,
                        "enabled": True,
                    },
                )
            granted.append(name)
    return {"granted": granted}


def _create(request, agent_id, body, *, metadata=None):
    service = AgentTaskService(request.app.state.database)
    return service.create(
        agent_id,
        AgentTaskInput(
            client_request_id=body.client_request_id,
            title=body.title,
            objective=body.objective,
            expected_result=body.expected_result,
            max_calls=body.max_calls,
            max_active_seconds=body.max_active_seconds,
        ),
        metadata=metadata,
    )


@router.post("/agent-tasks")
def create_agent_task(
    agent_id: UUID, body: AgentTaskBody, request: Request, session: _SESSION
) -> dict:
    task = _create(request, agent_id, body)
    return {"task_id": str(task.id), "status": task.status, "revision": task.revision}


def _context(records) -> str:
    lines = []
    total = 0
    for record in reversed(records):
        if record.role not in ("user", "assistant") or not record.content:
            continue
        text = " ".join(record.content.split())
        if len(text) > 1500:
            text = text[:1500] + "…"
        line = ("Usuário: " if record.role == "user" else "Abelha: ") + text
        if total + len(line) > CONTEXT_CHARS or len(lines) >= CONTEXT_MESSAGES:
            break
        lines.append(line)
        total += len(line)
    return "\n".join(reversed(lines))


@router.post("/conversations/{conversation_id}/agent-messages")
def agent_message(
    agent_id: UUID,
    conversation_id: UUID,
    body: AgentMessageBody,
    request: Request,
    session: _SESSION,
) -> dict:
    """Mensagem no chat executada pelo agente; a resposta volta para esta conversa."""
    store = request.app.state.store
    service = AgentTaskService(request.app.state.database)
    with store.transaction(source="agent_chat_message") as unit:
        agent = ProviderService._agent(unit, agent_id)
        conversation = ProviderService._conversation(unit, agent, conversation_id)
        if conversation.metadata.get("execution_kind"):
            raise ProviderError("invalid_request")
        prior = unit.tasks.find_submission(body.client_request_id)
        if prior is not None:
            origin = prior.metadata.get("agent_chat", {})
            if (
                prior.agent_id != agent.id
                or origin.get("conversation_id") != str(conversation.id)
                or origin.get("content_hash") != _hash(body.content)
            ):
                raise ProviderError("state_conflict")
            return {
                "task_id": str(prior.id),
                "message_id": origin.get("source_message_id"),
                "status": prior.status,
            }
        records = ProviderService._records(unit, conversation.id)
        context = _context(records)
        message = ProviderService._message(
            conversation.id,
            ChatMessage(role="user", content=body.content),
            records,
            source="user",
            metadata={"agent_request": {"id": str(body.client_request_id)}},
        ).model_copy(update={"id": uuid5(body.client_request_id, "bees.agent-chat-message/v1")})
        unit.messages.create(message)
        unit.conversations.update(conversation, conversation.revision)
        title = " ".join(body.content.split())
        task = service.create_bound_in_unit(
            unit,
            agent.id,
            AgentTaskInput(
                client_request_id=body.client_request_id,
                title=title[:80] + ("…" if len(title) > 80 else ""),
                objective=body.content,
                max_calls=body.max_calls,
                max_active_seconds=body.max_active_seconds,
            ),
            metadata={
                "agent_chat": {
                    "conversation_id": str(conversation.id),
                    "source_message_id": str(message.id),
                    "content_hash": _hash(body.content),
                    "context": context,
                },
                "delegation": {
                    "origin_conversation_id": str(conversation.id),
                    "source_message_id": str(message.id),
                    "response_message_id": str(message.id),
                    "tool_call_id": "agent",
                    "request_id": str(body.client_request_id),
                },
            },
        )
    return {"task_id": str(task.id), "message_id": str(message.id), "status": task.status}


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


approvals = APIRouter(
    prefix="/api/v1/agents/{agent_id}/agent-tool-approvals", tags=["agent-tool-approvals"]
)


@approvals.get("")
def list_approvals(
    agent_id: UUID,
    request: Request,
    session: _SESSION,
    task_id: UUID | None = None,
    status: str | None = Query(default=None, pattern="^(pending|approved|denied|revoked)$"),
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0, le=2**31 - 1)] = 0,
):
    service = AgentToolService(request.app.state.database)
    items = service.list(agent_id, task_id=task_id, status=status, limit=limit + 1, offset=offset)
    return {"approvals": items[:limit], "has_more": len(items) > limit}


@approvals.get("/{approval_id}")
def get_approval(agent_id: UUID, approval_id: UUID, request: Request, session: _SESSION):
    return AgentToolService(request.app.state.database).view(agent_id, approval_id, detail=True)


@approvals.post("/{approval_id}/decision")
def decide(
    agent_id: UUID,
    approval_id: UUID,
    body: AgentToolDecision,
    request: Request,
    session: _SESSION,
):
    service = AgentToolService(request.app.state.database)
    result = service.decide(agent_id, approval_id, body)
    return _receipt(service, agent_id, approval_id, body, result, "decide")


@approvals.patch("/{approval_id}")
def revoke(
    agent_id: UUID, approval_id: UUID, body: AgentToolRevoke, request: Request, session: _SESSION
):
    service = AgentToolService(request.app.state.database)
    result = service.revoke(agent_id, approval_id, body)
    return _receipt(service, agent_id, approval_id, body, result, "revoke")


def _receipt(service, agent_id, approval_id, body, result, operation):
    return {
        "approval": service.view(agent_id, approval_id, detail=True),
        "receipt": {
            "approval_id": str(approval_id),
            "client_request_id": str(body.client_request_id),
            "operation": operation,
            "expected_revision": body.expected_revision,
            "request_hash": result.metadata["human_requests"][str(body.client_request_id)],
            "confirmed": True,
        },
    }
