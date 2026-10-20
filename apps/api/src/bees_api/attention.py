"""Avisos locais e preferências acessíveis somente pela sessão humana."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request

from bees_api.auth import require_session
from bees_core.attention import (
    AttentionConversations,
    AttentionItem,
    AttentionKind,
    AttentionPage,
    AttentionPreference,
    AttentionService,
    ConversationAttentionPage,
    PreferenceInput,
    ReadInput,
)
from bees_core.security.identity import Session

_SESSION = Annotated[Session, Depends(require_session)]
router = APIRouter(prefix="/api/v1/agents", tags=["attention"])


@router.get("/{agent_id}/attention/conversations", response_model=AttentionConversations)
def get_conversations(
    agent_id: UUID,
    request: Request,
    session: _SESSION,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0, le=2**63 - 1)] = 0,
) -> AttentionConversations:
    return AttentionService(request.app.state.database).conversations(
        agent_id, limit=limit, offset=offset
    )


@router.get("/{agent_id}/attention", response_model=AttentionPage | ConversationAttentionPage)
def get_attention(
    agent_id: UUID,
    request: Request,
    session: _SESSION,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    before: Annotated[int | None, Query(ge=1, le=2**63 - 1)] = None,
    conversation_id: UUID | None = None,
) -> AttentionPage | ConversationAttentionPage:
    return AttentionService(request.app.state.database).list(
        agent_id, limit=limit, before=before, conversation_id=conversation_id
    )


@router.get("/{agent_id}/attention/preferences")
def get_preferences(agent_id: UUID, request: Request, session: _SESSION) -> dict:
    return AttentionService(request.app.state.database).preferences(agent_id)


@router.put("/{agent_id}/attention/preferences/{kind}", response_model=AttentionPreference)
def put_preference(
    agent_id: UUID, kind: AttentionKind, body: PreferenceInput, request: Request, session: _SESSION
) -> AttentionPreference:
    return AttentionService(request.app.state.database).set_preference(agent_id, kind, body)


@router.put("/{agent_id}/attention/{item_id}/read", response_model=AttentionItem)
def put_read(
    agent_id: UUID,
    item_id: UUID,
    body: ReadInput,
    request: Request,
    session: _SESSION,
    conversation_id: UUID | None = None,
) -> AttentionItem:
    return AttentionService(request.app.state.database).set_read(
        agent_id, item_id, body, conversation_id=conversation_id
    )
