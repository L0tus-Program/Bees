"""Preparo gráfico de modelo local: estado do backend, download confirmado e remoção."""

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field

from bees_api.auth import require_session
from bees_core.providers.local_models import LocalModelManager, PullInput
from bees_core.security.identity import Session

_SESSION = Annotated[Session, Depends(require_session)]
router = APIRouter(prefix="/api/v1/local-models", tags=["local-models"])


class DeleteInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    endpoint: str | None = Field(default=None, max_length=2048)
    model: str = Field(min_length=1, max_length=200)
    confirm_delete: Literal[True]


def _manager(request: Request) -> LocalModelManager:
    return request.app.state.local_models


@router.get("")
async def status(request: Request, session: _SESSION, endpoint: str | None = None) -> dict:
    return await _manager(request).status(endpoint)


@router.post("/pulls", status_code=202)
async def start_pull(body: PullInput, request: Request, session: _SESSION) -> dict:
    return _manager(request).start(body).view()


@router.get("/pulls/{pull_id}")
def pull(pull_id: UUID, request: Request, session: _SESSION) -> dict:
    return _manager(request).get(pull_id).view()


@router.post("/pulls/{pull_id}/cancel")
async def cancel_pull(pull_id: UUID, request: Request, session: _SESSION) -> dict:
    return (await _manager(request).cancel(pull_id)).view()


@router.post("/delete")
async def delete_model(body: DeleteInput, request: Request, session: _SESSION) -> dict:
    await _manager(request).delete(body.endpoint, body.model)
    return {"deleted": body.model}
