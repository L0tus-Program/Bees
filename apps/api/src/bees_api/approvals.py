"""Decisões humanas autenticadas, sem exportar o contexto privado do modelo."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request

from bees_api.auth import require_session
from bees_core.approvals import ApprovalDecisionInput, ApprovalRevokeInput, ApprovalService
from bees_core.security.identity import Session

_SESSION = Annotated[Session, Depends(require_session)]
router = APIRouter(prefix="/api/v1/agents", tags=["approvals"])


def _service(request: Request) -> ApprovalService:
    return ApprovalService(request.app.state.database, resolver=request.app.state.resolver)


@router.get("/{agent_id}/approvals")
def list_approvals(
    agent_id: UUID,
    request: Request,
    session: _SESSION,
    task_id: UUID | None = None,
    offset: Annotated[int, Query(ge=0, le=1000000)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 100,
) -> dict:
    service = _service(request)
    approvals = service.list(agent_id, task_id=task_id, limit=limit + 1, offset=offset)
    return {
        "approvals": [service.view(agent_id, approval) for approval in approvals[:limit]],
        "has_more": len(approvals) > limit,
        "next_offset": offset + limit if len(approvals) > limit else None,
    }


@router.get("/{agent_id}/approvals/{approval_id}")
def get_approval(agent_id: UUID, approval_id: UUID, request: Request, session: _SESSION) -> dict:
    service = _service(request)
    return service.view(agent_id, service.get(agent_id, approval_id))


@router.post("/{agent_id}/approvals/{approval_id}/decision")
def decide(
    agent_id: UUID,
    approval_id: UUID,
    body: ApprovalDecisionInput,
    request: Request,
    session: _SESSION,
) -> dict:
    service = _service(request)
    return service.view(agent_id, service.decide(agent_id, approval_id, body))


@router.patch("/{agent_id}/approvals/{approval_id}")
def revoke(
    agent_id: UUID,
    approval_id: UUID,
    body: ApprovalRevokeInput,
    request: Request,
    session: _SESSION,
) -> dict:
    service = _service(request)
    return service.view(agent_id, service.revoke(agent_id, approval_id, body))
