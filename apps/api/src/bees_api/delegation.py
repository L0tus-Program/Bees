"""Projeção autenticada de tarefas vinculadas à conversa; GET não gera respostas."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request

from bees_api.auth import require_session
from bees_api.tasks import _view
from bees_core.delegation import ConversationDelegationService
from bees_core.security.identity import Session
from bees_core.tasks import TaskService

router = APIRouter(prefix="/api/v1/agents", tags=["delegations"])
_SESSION = Annotated[Session, Depends(require_session)]


def chat_result(request: Request, tasks) -> list[dict]:
    service = TaskService(request.app.state.database)
    return [
        {
            "id": str(task.id),
            "source_message_id": task.metadata["delegation"]["source_message_id"],
            "response_message_id": task.metadata["delegation"]["response_message_id"],
            "task": _view(service.detail(task.agent_id, task.id)),
        }
        for task in tasks
    ]


@router.get("/{agent_id}/delegations")
def list_delegations(
    agent_id: UUID,
    conversation_id: UUID,
    request: Request,
    session: _SESSION,
    offset: Annotated[int, Query(ge=0, le=1000000)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 100,
) -> dict:
    service = ConversationDelegationService(request.app.state.providers)
    tasks = service.list(agent_id, conversation_id, limit=limit + 1, offset=offset)
    has_more = len(tasks) > limit
    return {
        "delegations": chat_result(request, tasks[:limit]),
        "has_more": has_more,
        "next_offset": offset + limit if has_more else None,
    }
