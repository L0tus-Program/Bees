"""Pedidos humanos de computador; nenhuma rota provisiona ou concede acesso ao host."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request

from bees_api.auth import require_session
from bees_core.environments import (
    EnvironmentCancelInput,
    EnvironmentCreateInput,
    EnvironmentService,
)
from bees_core.security.identity import Session

_SESSION = Annotated[Session, Depends(require_session)]
_OFFSET = Annotated[int, Query(ge=0, le=1000000)]
_LIMIT = Annotated[int, Query(ge=1, le=100)]
router = APIRouter(prefix="/api/v1", tags=["environments"])


def _service(request: Request) -> EnvironmentService:
    # O serviço em container não presume acesso ao hipervisor da máquina hospedeira.
    return EnvironmentService(request.app.state.database)


@router.get("/environments/catalog")
def catalog(request: Request, session: _SESSION) -> dict:
    return _service(request).catalog()


@router.get("/environments/host")
def host(request: Request, session: _SESSION) -> dict:
    return _service(request).host_status()


@router.get("/agents/{agent_id}/environments")
def environments(
    agent_id: UUID, request: Request, session: _SESSION, offset: _OFFSET = 0, limit: _LIMIT = 100
) -> dict:
    service = _service(request)
    records = service.list(agent_id, limit=limit + 1, offset=offset)
    return {
        "environments": [service.view(record) for record in records[:limit]],
        "has_more": len(records) > limit,
        "next_offset": offset + limit if len(records) > limit else None,
    }


@router.post("/agents/{agent_id}/environments", status_code=201)
def create(
    agent_id: UUID, body: EnvironmentCreateInput, request: Request, session: _SESSION
) -> dict:
    service = _service(request)
    return service.view(service.request(agent_id, body))


@router.get("/agents/{agent_id}/environments/{environment_id}")
def detail(agent_id: UUID, environment_id: UUID, request: Request, session: _SESSION) -> dict:
    service = _service(request)
    return service.view(service.get(agent_id, environment_id))


@router.post("/agents/{agent_id}/environments/{environment_id}/cancel")
def cancel(
    agent_id: UUID,
    environment_id: UUID,
    body: EnvironmentCancelInput,
    request: Request,
    session: _SESSION,
) -> dict:
    service = _service(request)
    return service.view(service.cancel(agent_id, environment_id, body))
