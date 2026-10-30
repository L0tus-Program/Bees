"""Mensagem de conversa executada pelo agente; a resposta volta para a mesma conversa.

Compartilhado pela API web e pelos canais externos: grava a mensagem do usuário e
cria a tarefa do agente na mesma transação, com replay idempotente por pedido.
"""

import hashlib
import json
from typing import Literal
from uuid import UUID, uuid5

from bees_core.agent_tools.cycle import (
    DEFAULT_ACTIVE_SECONDS,
    DEFAULT_MAX_CALLS,
    AgentTaskInput,
    AgentTaskService,
)
from bees_core.providers.contracts import ChatMessage
from bees_core.providers.errors import ProviderError
from bees_core.providers.service import ProviderService

CONTEXT_MESSAGES = 12
CONTEXT_CHARS = 8000


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def recent_context(records) -> str:
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


def submit_agent_message(
    database,
    unit,
    agent_id: UUID | str,
    conversation_id: UUID | str,
    content: str,
    client_request_id: UUID,
    *,
    mode: Literal["agent", "research"] = "agent",
    max_calls: int = DEFAULT_MAX_CALLS,
    max_active_seconds: int = DEFAULT_ACTIVE_SECONDS,
    message_metadata: dict | None = None,
    attachments: tuple[list[dict], list[dict]] = ([], []),
) -> dict:
    views, entries = attachments
    # Idempotência cobre texto e versões exatas dos anexos.
    fingerprint = (
        content_hash(content)
        if not views
        else content_hash(content + json.dumps(views, sort_keys=True, ensure_ascii=False))
    )
    agent = ProviderService._agent(unit, agent_id)
    conversation = ProviderService._conversation(unit, agent, conversation_id)
    if conversation.metadata.get("execution_kind"):
        raise ProviderError("invalid_request")
    prior = unit.tasks.find_submission(client_request_id)
    if prior is not None:
        origin = prior.metadata.get("agent_chat", {})
        if (
            prior.agent_id != agent.id
            or origin.get("conversation_id") != str(conversation.id)
            or origin.get("content_hash") != fingerprint
        ):
            raise ProviderError("state_conflict")
        return {
            "task_id": str(prior.id),
            "message_id": origin.get("source_message_id"),
            "status": prior.status,
        }
    records = ProviderService._records(unit, conversation.id)
    context = recent_context(records)
    message = ProviderService._message(
        conversation.id,
        ChatMessage(role="user", content=content),
        records,
        source="user",
        metadata={"agent_request": {"id": str(client_request_id)}}
        | ({"attachments": views} if views else {})
        | (message_metadata or {}),
    ).model_copy(update={"id": uuid5(client_request_id, "bees.agent-chat-message/v1")})
    unit.messages.create(message)
    unit.conversations.update(conversation, conversation.revision)
    title = " ".join(content.split())
    task = AgentTaskService(database).create_bound_in_unit(
        unit,
        agent.id,
        AgentTaskInput(
            client_request_id=client_request_id,
            title=title[:80] + ("…" if len(title) > 80 else ""),
            objective=content,
            max_calls=max_calls,
            max_active_seconds=max_active_seconds,
        ),
        metadata=({"research": True} if mode == "research" else {})
        | {
            "agent_chat": {
                "conversation_id": str(conversation.id),
                "source_message_id": str(message.id),
                "content_hash": fingerprint,
                "context": context,
            }
            | ({"attachments": entries} if entries else {}),
            "delegation": {
                "origin_conversation_id": str(conversation.id),
                "source_message_id": str(message.id),
                "response_message_id": str(message.id),
                "tool_call_id": "agent",
                "request_id": str(client_request_id),
            },
        },
    )
    return {"task_id": str(task.id), "message_id": str(message.id), "status": task.status}
