"""Gestão humana de cadastros de provisionadores; sem emissão ou execução."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request

from bees_api.auth import require_session
from bees_core.provisioning import ProvisionerRevokeInput, ProvisioningService
from bees_core.security.identity import Session

_SESSION = Annotated[Session, Depends(require_session)]
_OFFSET = Annotated[int, Query(ge=0, le=1000000)]
_LIMIT = Annotated[int, Query(ge=1, le=100)]
router = APIRouter(
    prefix="/api/v1/environments/hosts/{host_id}/provisioners", tags=["provisioners"]
)


@router.get("")
def list_provisioners(
    host_id: UUID,
    request: Request,
    session: _SESSION,
    offset: _OFFSET = 0,
    limit: _LIMIT = 100,
) -> dict:
    records = ProvisioningService(request.app.state.database).list_provisioners(
        host_id, offset=offset, limit=limit + 1
    )
    has_more = len(records) > limit
    return {
        "provisioners": records[:limit],
        "has_more": has_more,
        "next_offset": offset + limit if has_more else None,
    }


@router.post("/{provisioner_id}/revoke")
def revoke_provisioner(
    host_id: UUID,
    provisioner_id: UUID,
    body: ProvisionerRevokeInput,
    request: Request,
    session: _SESSION,
) -> dict:
    return ProvisioningService(request.app.state.database).revoke_provisioner(
        provisioner_id, body, host_id=host_id
    )
