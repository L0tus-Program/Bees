"""Edição humana autenticada de políticas; nenhum endpoint aceita autoridade do modelo."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request

from bees_api.auth import require_session
from bees_core.models import Policy
from bees_core.policies import PolicyInput, PolicyService, PolicyUpdate
from bees_core.security.identity import Session

_SESSION = Annotated[Session, Depends(require_session)]
router = APIRouter(prefix="/api/v1/agents", tags=["policies"])


class CreateInput(PolicyInput):
    client_request_id: UUID


def _service(request: Request) -> PolicyService:
    return PolicyService(request.app.state.database)


def _view(policy: Policy) -> dict:
    # Não exportar metadata interna, request hashes, source/actor fornecidos pelo cliente.
    return {
        "id": str(policy.id),
        "agent_id": str(policy.agent_id) if policy.agent_id else None,
        "name": policy.name,
        "effect": policy.effect,
        "scope": policy.scope,
        "status": policy.status,
        "revision": policy.revision,
        "reason": policy.reason,
        "created_at": policy.created_at.isoformat(),
        "updated_at": policy.updated_at.isoformat(),
    }


@router.get("/{agent_id}/policies")
def list_policies(
    agent_id: UUID,
    request: Request,
    session: _SESSION,
    offset: Annotated[int, Query(ge=0, le=1000000)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 100,
) -> dict:
    policies = _service(request).list(agent_id, limit=limit + 1, offset=offset)
    return {
        "policies": [_view(policy) for policy in policies[:limit]],
        "has_more": len(policies) > limit,
        "next_offset": offset + limit if len(policies) > limit else None,
    }


@router.post("/{agent_id}/policies", status_code=201)
def create_policy(agent_id: UUID, body: CreateInput, request: Request, session: _SESSION) -> dict:
    value = PolicyInput.model_validate(body.model_dump(exclude={"client_request_id"}))
    return _view(
        _service(request).create(agent_id, value, client_request_id=body.client_request_id)
    )


@router.patch("/{agent_id}/policies/{policy_id}")
def update_policy(
    agent_id: UUID, policy_id: UUID, body: PolicyUpdate, request: Request, session: _SESSION
) -> dict:
    return _view(_service(request).update(agent_id, policy_id, body))
