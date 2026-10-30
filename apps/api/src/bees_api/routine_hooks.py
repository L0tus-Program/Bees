"""Gatilhos por webhook: gerência autenticada por rotina e disparo público por segredo."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from bees_api.auth import require_session
from bees_core.routine_hooks import CreateHook, HookError, RoutineHookService, view
from bees_core.security.identity import Session

_SESSION = Annotated[Session, Depends(require_session)]
router = APIRouter(prefix="/api/v1/agents/{agent_id}/routines/{routine_id}/hook", tags=["hooks"])
public = APIRouter(prefix="/api/v1/hooks", tags=["hooks"])


class RevisionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=1)


def _url(request: Request, token: str) -> str:
    settings = request.app.state.settings
    base = (settings.public_url or str(request.base_url)).rstrip("/")
    return f"{base}/api/v1/hooks/{token}"


@router.get("")
def get_hook(agent_id: UUID, routine_id: UUID, request: Request, session: _SESSION) -> dict:
    return {
        "hook": view(RoutineHookService(request.app.state.database).active(agent_id, routine_id))
    }


@router.post("", status_code=201)
def create_hook(
    agent_id: UUID, routine_id: UUID, body: CreateHook, request: Request, session: _SESSION
) -> dict:
    row, token = RoutineHookService(request.app.state.database).create(agent_id, routine_id, body)
    # A URL contém o segredo e só é mostrada agora.
    return {"hook": view(row), "url": _url(request, token)}


@router.post("/revoke")
def revoke_hook(
    agent_id: UUID, routine_id: UUID, body: RevisionBody, request: Request, session: _SESSION
) -> dict:
    row = RoutineHookService(request.app.state.database).revoke(
        agent_id, routine_id, body.expected_revision
    )
    return {"hook": view(row)}


@public.post("/{token}", status_code=202)
def trigger(token: str, request: Request):
    try:
        return RoutineHookService(request.app.state.database).trigger(
            token, request.headers.get("idempotency-key")
        )
    except HookError as error:
        status = {"hook_not_found": 404, "hook_rate_limited": 429}.get(error.code, 409)
        headers = {"Cache-Control": "no-store"}
        if status == 429:
            headers["Retry-After"] = "60"
        return JSONResponse(
            {"error": {"code": error.code, "message": "Gatilho não aceito."}},
            status_code=status,
            headers=headers,
        )
