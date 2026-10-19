"""Canal humano de decisões sobre ferramenta; nenhum endpoint prepara ou executa."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request

from bees_api.auth import require_session
from bees_core.resource_tools import ResourceDecision, ResourceRevoke, ResourceToolService
from bees_core.security.identity import Session

_SESSION = Annotated[Session, Depends(require_session)]
router = APIRouter(prefix="/api/v1/agents/{agent_id}/tool-approvals", tags=["tool-approvals"])


@router.get("")
def list_approvals(
    agent_id: UUID,
    request: Request,
    session: _SESSION,
    task_id: UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0, le=2**31 - 1)] = 0,
):
    service = ResourceToolService(request.app.state.database)
    items = service.list(agent_id, task_id=task_id, limit=limit + 1, offset=offset)
    return {
        "approvals": [service.view(agent_id, item.id) for item in items[:limit]],
        "has_more": len(items) > limit,
    }


@router.get("/{approval_id}")
def get_approval(agent_id: UUID, approval_id: UUID, request: Request, session: _SESSION):
    return ResourceToolService(request.app.state.database).view(agent_id, approval_id, detail=True)


@router.post("/{approval_id}/decision")
def decide(
    agent_id: UUID, approval_id: UUID, body: ResourceDecision, request: Request, session: _SESSION
):
    service = ResourceToolService(request.app.state.database)
    result = service.decide(agent_id, approval_id, body)
    return _receipt(service, agent_id, approval_id, body, result, "decide")


@router.patch("/{approval_id}")
def revoke(
    agent_id: UUID, approval_id: UUID, body: ResourceRevoke, request: Request, session: _SESSION
):
    service = ResourceToolService(request.app.state.database)
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
