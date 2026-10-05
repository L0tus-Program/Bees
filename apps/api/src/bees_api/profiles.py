"""Perfil e memórias explícitas; nenhum desses recursos autoriza ferramentas."""

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import Field, ValidationError

from bees_api.auth import require_session
from bees_api.onboarding import Input, _summary
from bees_core.memory import MemoryDetails, MemoryInput, MemoryService, MemoryUpdate, memory_details
from bees_core.models import Memory
from bees_core.profiles import AgentProfile
from bees_core.security.identity import Session
from bees_core.storage.store import NotFoundError

_SESSION = Annotated[Session, Depends(require_session)]
router = APIRouter(prefix="/api/v1/agents", tags=["profiles", "memories"])


class ProfileInput(AgentProfile):
    expected_revision: int = Field(ge=1, strict=True)


class MemoryCreateInput(MemoryDetails):
    scope: Literal["user", "agent", "task"]
    task_id: UUID | None = None
    content: str = Field(min_length=1, max_length=8192)


class MemoryUpdateInput(MemoryUpdate):
    expected_revision: int = Field(ge=1, strict=True)


class DeleteInput(Input):
    expected_revision: int = Field(ge=1, strict=True)


def _memory_response(memory: Memory) -> dict:
    result = {
        "id": str(memory.id),
        "revision": memory.revision,
        "scope": memory.scope,
        "agent_id": str(memory.agent_id) if memory.agent_id else None,
        "task_id": str(memory.task_id) if memory.task_id else None,
        "content": memory.content,
        "created_at": memory.created_at.isoformat(),
        "updated_at": memory.updated_at.isoformat(),
    }
    return result | memory_details(memory).model_dump(mode="json")


def _memory_scope(request: Request, agent_id: UUID, memory_id: UUID | None = None) -> Memory | None:
    with request.app.state.store.transaction(write=False) as unit:
        if unit.agents.get(agent_id) is None:
            raise NotFoundError("Abelha não encontrada.")
        if memory_id is None:
            return None
        memory = unit.memories.get(memory_id)
        if memory is None or (memory.scope != "user" and memory.agent_id != agent_id):
            raise NotFoundError("Memória não encontrada para esta abelha.")
        return memory


def _validated_memory(value: dict) -> MemoryInput:
    try:
        return MemoryInput.model_validate(value)
    except ValidationError:
        # Erros de domínio internos também podem carregar conteúdo em input.
        raise HTTPException(
            422,
            detail={
                "code": "validation_failed",
                "message": "Confira o escopo e os dados da memória.",
            },
        ) from None


@router.post("/{agent_id}/profile")
def profile(agent_id: UUID, body: ProfileInput, request: Request, session: _SESSION) -> dict:
    value = AgentProfile.model_validate(body.model_dump(exclude={"expected_revision"}))
    updated = request.app.state.providers.update_profile(
        agent_id, value, expected_revision=body.expected_revision
    )
    with request.app.state.store.transaction(write=False) as unit:
        return _summary(unit, updated)


@router.get("/{agent_id}/memories")
def memories(agent_id: UUID, request: Request, session: _SESSION) -> dict:
    _memory_scope(request, agent_id)
    records = MemoryService(request.app.state.database).list(agent_id=agent_id, limit=1000)
    with request.app.state.store.transaction(write=False) as unit:
        tasks = unit.tasks.list(agent_id=agent_id, limit=1000)
    return {
        "memories": [_memory_response(memory) for memory in records],
        "tasks": [{"id": str(task.id), "title": task.title} for task in tasks],
        "has_more": len(records) == 1000 or len(tasks) == 1000,
    }


@router.post("/{agent_id}/memories", status_code=201)
def create_memory(
    agent_id: UUID, body: MemoryCreateInput, request: Request, session: _SESSION
) -> dict:
    _memory_scope(request, agent_id)
    value = _validated_memory(
        body.model_dump() | {"agent_id": None if body.scope == "user" else agent_id}
    )
    created = MemoryService(request.app.state.database).create(value)
    return _memory_response(created)


@router.post("/{agent_id}/memories/{memory_id}/update")
def update_memory(
    agent_id: UUID,
    memory_id: UUID,
    body: MemoryUpdateInput,
    request: Request,
    session: _SESSION,
) -> dict:
    _memory_scope(request, agent_id, memory_id)
    try:
        value = MemoryUpdate.model_validate(
            body.model_dump(exclude={"expected_revision"}, exclude_unset=True)
        )
        changed = MemoryService(request.app.state.database).update(
            memory_id, value, expected_revision=body.expected_revision
        )
    except ValidationError:
        raise HTTPException(
            422,
            detail={
                "code": "validation_failed",
                "message": "Confira os dados e a fonte da memória.",
            },
        ) from None
    return _memory_response(changed)


@router.post("/{agent_id}/memories/{memory_id}/delete", status_code=204)
def delete_memory(
    agent_id: UUID,
    memory_id: UUID,
    body: DeleteInput,
    request: Request,
    session: _SESSION,
) -> Response:
    _memory_scope(request, agent_id, memory_id)
    MemoryService(request.app.state.database).delete(
        memory_id, expected_revision=body.expected_revision
    )
    return Response(status_code=204)
