"""Pareamento humano e canal do helper, limitado a diagnóstico de virtualização."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from bees_api.auth import require_session
from bees_core.security.hosts import (
    HostConfirmInput,
    HostPairInput,
    HostReportInput,
    HostRevokeInput,
    HostService,
)
from bees_core.security.identity import Session

_SESSION = Annotated[Session, Depends(require_session)]
_OFFSET = Annotated[int, Query(ge=0, le=1000000)]
_LIMIT = Annotated[int, Query(ge=1, le=100)]
router = APIRouter(prefix="/api/v1", tags=["host-links"])


def _service(request: Request) -> HostService:
    return HostService(request.app.state.database)


def _credential(request: Request) -> str:
    values = request.headers.getlist("authorization")
    if len(values) != 1 or not values[0].startswith("Bearer bh_"):
        raise HTTPException(
            401, detail={"code": "host_credentials_invalid", "message": "Vínculo inválido."}
        )
    # Cookies de usuário nunca substituem a credencial própria do helper.
    return values[0][7:]


@router.get("/environments/hosts")
def hosts(request: Request, session: _SESSION, offset: _OFFSET = 0, limit: _LIMIT = 100) -> dict:
    records = _service(request).list(limit=limit + 1, offset=offset)
    return {
        "hosts": records[:limit],
        "has_more": len(records) > limit,
        "next_offset": offset + limit if len(records) > limit else None,
    }


@router.post("/environments/hosts/{host_id}/confirm")
def confirm(host_id: UUID, body: HostConfirmInput, request: Request, session: _SESSION) -> dict:
    return _service(request).confirm(host_id, body)


@router.post("/environments/hosts/{host_id}/revoke")
def revoke(host_id: UUID, body: HostRevokeInput, request: Request, session: _SESSION) -> dict:
    return _service(request).revoke(host_id, body)


@router.post("/host-link/runtime/exchange")
def exchange(body: HostPairInput, request: Request) -> dict:
    # O convite de alta entropia autentica esta troca; não existe auto-claim anônimo.
    return _service(request).pair(body)


@router.get("/host-link/runtime/session")
def runtime_session(request: Request) -> dict:
    return _service(request).session(_credential(request))


@router.post("/host-link/runtime/report")
def report(body: HostReportInput, request: Request) -> dict:
    return _service(request).report(_credential(request), body)
