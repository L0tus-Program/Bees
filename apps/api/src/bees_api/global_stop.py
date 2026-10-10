"""Consulta e comandos humanos da parada global; sucesso só após a confirmação canônica."""

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from pydantic import Field

from bees_api.auth import require_session
from bees_api.onboarding import Input
from bees_core.safety import SafetyCommand, SafetyService
from bees_core.security.identity import Session

_SESSION = Annotated[Session, Depends(require_session)]
router = APIRouter(prefix="/api/v1/safety", tags=["safety"])


class CommandInput(Input):
    client_request_id: UUID
    kind: Literal["stop", "resume"]
    expected_revision: int = Field(ge=1, strict=True)
    reason: str | None = Field(default=None, min_length=1, max_length=500)


def _service(request: Request) -> SafetyService:
    return SafetyService(request.app.state.database)


@router.get("")
def get_safety(request: Request, session: _SESSION) -> dict:
    return _service(request).current()


@router.post("/commands")
def post_command(body: CommandInput, request: Request, session: _SESSION) -> dict:
    # Replay do mesmo UUID devolve o estado atual sem reaplicar; outro conteúdo é conflito.
    return _service(request).command(SafetyCommand.model_validate(body.model_dump()))
