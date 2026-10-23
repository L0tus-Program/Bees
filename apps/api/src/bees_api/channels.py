"""Canais externos por abelha: criar com token no cofre, parear, pausar e revogar."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request

from bees_api.auth import require_session
from bees_core.channels.adapters import ChannelError, adapters
from bees_core.channels.service import (
    ChannelService,
    ChannelServiceError,
    CreateChannel,
    RevisionInput,
    UpdateChannel,
    view,
)
from bees_core.security.identity import Session

_SESSION = Annotated[Session, Depends(require_session)]
router = APIRouter(prefix="/api/v1/agents/{agent_id}/channels", tags=["channels"])


def _service(request: Request) -> ChannelService:
    return ChannelService(request.app.state.database)


def _adapters(request: Request):
    return getattr(request.app.state, "channel_adapters", None) or adapters()


def _pairing(row: dict, code: str | None) -> dict:
    link = None
    if code and row["kind"] == "telegram" and row["metadata"].get("bot_username"):
        link = f"https://t.me/{row['metadata']['bot_username']}?start={code}"
    return {"channel": view(row), "pairing_code": code, "pairing_link": link}


@router.get("")
def list_channels(agent_id: UUID, request: Request, session: _SESSION) -> dict:
    return {"channels": [view(row) for row in _service(request).list(agent_id)]}


@router.post("", status_code=201)
async def create_channel(
    agent_id: UUID, body: CreateChannel, request: Request, session: _SESSION
) -> dict:
    service = _service(request)
    prior = service.find_request(body.client_request_id)
    if prior is not None:
        if prior["agent_id"] != str(agent_id) or prior["kind"] != body.kind:
            raise ChannelServiceError("channel_request_conflict")
        # O código só é mostrado uma vez; gere outro pela interface.
        return _pairing(prior, None) | {"replayed": True}
    if body.kind == "discord" and not body.target:
        raise ChannelServiceError("channel_target_invalid")
    identity = await _adapters(request)[body.kind].identity(
        body.token.get_secret_value(), body.target
    )
    require_session(request)
    reference = request.app.state.vault.put(body.token)
    try:
        row, code = service.create(agent_id, body, secret_ref=reference, identity=identity)
    except BaseException:
        request.app.state.vault.delete(reference)
        raise
    return _pairing(row, code) | {"replayed": False}


@router.post("/{channel_id}/update")
def update_channel(
    agent_id: UUID, channel_id: UUID, body: UpdateChannel, request: Request, session: _SESSION
) -> dict:
    return view(_service(request).update(agent_id, channel_id, body))


@router.post("/{channel_id}/code")
def new_code(
    agent_id: UUID, channel_id: UUID, body: RevisionInput, request: Request, session: _SESSION
) -> dict:
    row, code = _service(request).new_code(agent_id, channel_id, body)
    return _pairing(row, code)


@router.post("/{channel_id}/revoke")
def revoke_channel(
    agent_id: UUID, channel_id: UUID, body: RevisionInput, request: Request, session: _SESSION
) -> dict:
    row = _service(request).revoke(agent_id, channel_id, body)
    try:
        request.app.state.vault.delete(row["secret_ref"])
    except Exception:
        pass  # Vínculo revogado não usa mais o token; limpeza do cofre é melhor esforço.
    return view(row)


__all__ = ["router", "ChannelError", "ChannelServiceError"]
