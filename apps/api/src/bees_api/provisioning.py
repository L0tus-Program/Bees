"""Revisão humana de planos fechados; não despacha comandos no hospedeiro."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request

from bees_api.auth import require_session
from bees_core.provisioning import (
    DEFAULT_PROVISIONING_CATALOG,
    PlanCommandInput,
    PreparePlanInput,
    ProvisioningService,
)
from bees_core.security.identity import Session

_SESSION = Annotated[Session, Depends(require_session)]
router = APIRouter(
    prefix="/api/v1/agents/{agent_id}/environments/{environment_id}/provisioning",
    tags=["provisioning"],
)


def _service(request: Request) -> ProvisioningService:
    return ProvisioningService(
        request.app.state.database,
        catalog=getattr(request.app.state, "provisioning_catalog", DEFAULT_PROVISIONING_CATALOG),
    )


@router.get("")
def overview(agent_id: UUID, environment_id: UUID, request: Request, session: _SESSION) -> dict:
    service = _service(request)
    plan = service.current_plan(agent_id, environment_id)
    host = service.active_host_summary()
    return {
        "plan": plan,
        "host": host,
        # Disponibilidade de planejamento não significa hipervisor/imagem instalada.
        "preparation_available": host is not None and service.catalog is not None,
    }


@router.post("/prepare", status_code=201)
def prepare(
    agent_id: UUID,
    environment_id: UUID,
    body: PreparePlanInput,
    request: Request,
    session: _SESSION,
) -> dict:
    return _service(request).prepare_plan(agent_id, environment_id, body)


@router.post("/{plan_id}/authorize")
def authorize(
    agent_id: UUID,
    environment_id: UUID,
    plan_id: UUID,
    body: PlanCommandInput,
    request: Request,
    session: _SESSION,
) -> dict:
    return _service(request).authorize_once(
        plan_id, body, agent_id=agent_id, environment_id=environment_id
    )


@router.post("/{plan_id}/revoke")
def revoke(
    agent_id: UUID,
    environment_id: UUID,
    plan_id: UUID,
    body: PlanCommandInput,
    request: Request,
    session: _SESSION,
) -> dict:
    return _service(request).revoke_authorization(
        plan_id, body, agent_id=agent_id, environment_id=environment_id
    )
