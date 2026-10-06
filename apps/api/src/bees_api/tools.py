"""Gestão humana de contratos locais; chamadas do modelo não concedem ferramentas."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request

from bees_api.auth import require_session
from bees_core.security.identity import Session
from bees_core.tools import PluginInstallInput, PluginUpdate, ToolGrantInput, ToolService

_SESSION = Annotated[Session, Depends(require_session)]
_OFFSET = Annotated[int, Query(ge=0, le=1000000)]
_LIMIT = Annotated[int, Query(ge=1, le=100)]
router = APIRouter(prefix="/api/v1", tags=["tools"])


def _service(request: Request) -> ToolService:
    return ToolService(request.app.state.database)


def _plugin_view(plugin) -> dict:
    return {
        "id": str(plugin.id),
        "manifest": plugin.manifest,
        "manifest_hash": plugin.manifest_hash,
        "enabled": plugin.enabled,
        "revision": plugin.revision,
        "created_at": plugin.created_at.isoformat(),
        "updated_at": plugin.updated_at.isoformat(),
    }


def _grant_view(grant) -> dict:
    return {
        "id": str(grant.id),
        "agent_id": str(grant.agent_id),
        "plugin_id": str(grant.plugin_id),
        "tool_name": grant.tool_name,
        "enabled": grant.enabled,
        "revision": grant.revision,
        "created_at": grant.created_at.isoformat(),
        "updated_at": grant.updated_at.isoformat(),
    }


def _page(items: list, field: str, offset: int, limit: int) -> dict:
    return {
        field: items[:limit],
        "has_more": len(items) > limit,
        "next_offset": offset + limit if len(items) > limit else None,
    }


@router.get("/plugins/catalog")
def catalog(request: Request, session: _SESSION) -> dict:
    return {"plugins": _service(request).catalog()}


@router.get("/plugins")
def plugins(request: Request, session: _SESSION, offset: _OFFSET = 0, limit: _LIMIT = 100) -> dict:
    records = _service(request).list_plugins(limit=limit + 1, offset=offset)
    return _page([_plugin_view(record) for record in records], "plugins", offset, limit)


@router.post("/plugins", status_code=201)
def install(body: PluginInstallInput, request: Request, session: _SESSION) -> dict:
    return _plugin_view(_service(request).install(body))


@router.patch("/plugins/{plugin_id}")
def update(plugin_id: UUID, body: PluginUpdate, request: Request, session: _SESSION) -> dict:
    return _plugin_view(_service(request).update_plugin(plugin_id, body))


@router.get("/agents/{agent_id}/tools")
def tools(
    agent_id: UUID, request: Request, session: _SESSION, offset: _OFFSET = 0, limit: _LIMIT = 100
) -> dict:
    records = _service(request).list_tools(agent_id, limit=limit + 1, offset=offset)
    return _page(records, "tools", offset, limit)


@router.put("/agents/{agent_id}/tools/{plugin_id}/{tool_name}")
def grant(
    agent_id: UUID,
    plugin_id: UUID,
    tool_name: str,
    body: ToolGrantInput,
    request: Request,
    session: _SESSION,
) -> dict:
    return _grant_view(_service(request).set_grant(agent_id, plugin_id, tool_name, body))
