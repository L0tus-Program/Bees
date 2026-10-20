"""Rotinas humanas; GETs consultam, mutações exigem sessão e proteção CSRF."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from bees_api.auth import require_session
from bees_api.worker_status import HEARTBEAT_KEY, worker_status
from bees_core.executor_transport.contracts import TransportError
from bees_core.providers.errors import ProviderError
from bees_core.routines import (
    CreateRoutine,
    ReviseRoutine,
    RoutineOccurrence,
    RoutineRevision,
    RoutineService,
    RoutineView,
    SetRoutineStatus,
)
from bees_core.security.identity import Session
from bees_core.storage.store import NotFoundError
from bees_core.tasks import TaskError
from bees_core.tool_runtime import available_runtime
from bees_core.tools import ToolError

_SESSION = Annotated[Session, Depends(require_session)]
_LIMIT = Annotated[int, Query(ge=1, le=100)]
_OFFSET = Annotated[int, Query(ge=0, le=2**31 - 1)]
router = APIRouter(prefix="/api/v1/agents/{agent_id}/routines", tags=["routines"])


def _mutation(operation, *args):
    try:
        return operation(*args)
    except ValueError:
        # Entradas podem conter dados privados; nunca ecoar ValidationError/DTO.
        raise HTTPException(
            422,
            detail={"code": "routine_input_invalid", "message": "Confira a skill e suas entradas."},
        ) from None


@router.get("", response_model=list[RoutineView])
def list_routines(
    agent_id: UUID, request: Request, session: _SESSION, limit: _LIMIT = 50, offset: _OFFSET = 0
):
    return RoutineService(request.app.state.database).list(agent_id, limit=limit, offset=offset)


@router.post("", response_model=RoutineView)
def create(agent_id: UUID, body: CreateRoutine, request: Request, session: _SESSION):
    return _mutation(RoutineService(request.app.state.database).create, agent_id, body)


@router.get("/runtime")
def runtime_status(agent_id: UUID, request: Request, session: _SESSION):
    store = request.app.state.store
    with store.transaction(write=False) as unit:
        if unit.agents.get(agent_id) is None:
            raise NotFoundError("Abelha não encontrada.")
    heartbeat = store.get_cache(HEARTBEAT_KEY)
    reason = None
    if (
        not worker_status(store)["available"]
        or not isinstance(heartbeat, dict)
        or type(heartbeat.get("routine_scheduler_version")) is not int
        or heartbeat["routine_scheduler_version"] != 1
    ):
        reason = "worker_unavailable"
    else:
        try:
            available_runtime(request.app.state.database, agent_id)
        except TransportError:
            reason = "resource_unavailable"
        except TaskError, ToolError, ProviderError:
            reason = "tool_unavailable"
    return {
        "available": reason is None,
        "reason": reason,
        "version": 1,
        "max_calls": 4,
        "max_active_seconds": 120,
    }


@router.get("/{routine_id}", response_model=RoutineView)
def get_routine(agent_id: UUID, routine_id: UUID, request: Request, session: _SESSION):
    return RoutineService(request.app.state.database).get(agent_id, routine_id)


@router.post("/{routine_id}/revisions", response_model=RoutineView)
def revise(
    agent_id: UUID, routine_id: UUID, body: ReviseRoutine, request: Request, session: _SESSION
):
    return _mutation(RoutineService(request.app.state.database).revise, agent_id, routine_id, body)


@router.put("/{routine_id}/status", response_model=RoutineView)
def set_status(
    agent_id: UUID, routine_id: UUID, body: SetRoutineStatus, request: Request, session: _SESSION
):
    return _mutation(
        RoutineService(request.app.state.database).set_status, agent_id, routine_id, body
    )


@router.get("/{routine_id}/occurrences", response_model=list[RoutineOccurrence])
def occurrences(
    agent_id: UUID,
    routine_id: UUID,
    request: Request,
    session: _SESSION,
    limit: _LIMIT = 50,
    offset: _OFFSET = 0,
):
    return RoutineService(request.app.state.database).occurrences(
        agent_id, routine_id, limit=limit, offset=offset
    )


@router.get("/{routine_id}/revisions", response_model=list[RoutineRevision])
def revisions(
    agent_id: UUID,
    routine_id: UUID,
    request: Request,
    session: _SESSION,
    limit: _LIMIT = 50,
    offset: _OFFSET = 0,
):
    return RoutineService(request.app.state.database).revisions(
        agent_id, routine_id, limit=limit, offset=offset
    )
