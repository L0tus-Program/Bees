"""Conectores MCP por abelha: cadastro com leitura do catálogo, atualização e remoção."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field

from bees_api.auth import require_session
from bees_core.mcp import CreateServer, McpClient, McpError, McpService, view
from bees_core.security.identity import Session

_SESSION = Annotated[Session, Depends(require_session)]
router = APIRouter(prefix="/api/v1/agents/{agent_id}/mcp-servers", tags=["mcp"])


class RevisionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=1)


class EnabledBody(RevisionBody):
    enabled: bool


def _service(request: Request) -> McpService:
    return McpService(
        request.app.state.database,
        request.app.state.resolver,
        transport=getattr(request.app.state, "mcp_transport", None),
    )


@router.get("")
def list_servers(agent_id: UUID, request: Request, session: _SESSION) -> dict:
    return {"servers": [view(row) for row in _service(request).list(agent_id)]}


@router.post("", status_code=201)
async def create_server(
    agent_id: UUID, body: CreateServer, request: Request, session: _SESSION
) -> dict:
    service = _service(request)
    prior = service.find_request(body.client_request_id)
    if prior is not None:
        if prior["agent_id"] != str(agent_id) or prior["name"] != body.name:
            raise McpError("mcp_name_taken")
        return view(prior)
    token = body.token.get_secret_value() if body.token else None
    info, tools = await McpClient(body.url, token, transport=service.transport).list_tools()
    require_session(request)
    reference = request.app.state.vault.put(body.token) if body.token else None
    try:
        row = service.create(agent_id, body, secret_ref=reference, server=info, tools=tools)
    except BaseException:
        if reference:
            request.app.state.vault.delete(reference)
        raise
    return view(row)


@router.post("/{server_id}/refresh")
async def refresh_server(
    agent_id: UUID, server_id: UUID, body: RevisionBody, request: Request, session: _SESSION
) -> dict:
    return view(await _service(request).refresh(agent_id, server_id, body.expected_revision))


@router.post("/{server_id}/enabled")
def set_enabled(
    agent_id: UUID, server_id: UUID, body: EnabledBody, request: Request, session: _SESSION
) -> dict:
    return view(
        _service(request).set_enabled(agent_id, server_id, body.expected_revision, body.enabled)
    )


@router.post("/{server_id}/remove")
def remove_server(
    agent_id: UUID, server_id: UUID, body: RevisionBody, request: Request, session: _SESSION
) -> dict:
    row = _service(request).remove(agent_id, server_id, body.expected_revision)
    if row["secret_ref"]:
        try:
            request.app.state.vault.delete(row["secret_ref"])
        except Exception:
            pass  # Servidor removido não usa mais a credencial.
    return view(row)
