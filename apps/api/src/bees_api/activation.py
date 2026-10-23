"""Human reactivation is separate from global resume and task decisions."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request

from bees_api.auth import require_session
from bees_core.activation import (
    ActivationError,
    AgentActivationService,
    ReactivationInput,
    ReactivationReceipt,
)
from bees_core.security.identity import Session

router = APIRouter(prefix="/api/v1/agents", tags=["activation"])
_SESSION = Annotated[Session, Depends(require_session)]


@router.post("/{agent_id}/reactivate", response_model=ReactivationReceipt)
async def reactivate(agent_id: UUID, body: ReactivationInput, request: Request, session: _SESSION):
    try:
        return await AgentActivationService(
            request.app.state.database, request.app.state.providers
        ).reactivate(agent_id, body)
    except ActivationError as error:
        raise HTTPException(
            409, detail={"code": error.code, "message": "Confira a configuração e a parada global."}
        ) from None
