"""Delegação e observação autenticadas; somente o worker consome a fila."""

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import Field, ValidationError

from bees_api.auth import require_session
from bees_api.onboarding import Input
from bees_api.worker_status import worker_status
from bees_core.security.identity import Session
from bees_core.storage.store import NotFoundError
from bees_core.tasks import TaskCommand, TaskInput, TaskService

_SESSION = Annotated[Session, Depends(require_session)]
router = APIRouter(prefix="/api/v1/agents", tags=["tasks"])


class CreateTaskInput(TaskInput):
    conversation_id: UUID | None = None


class ControlInput(Input):
    client_request_id: UUID
    expected_revision: int = Field(ge=1, strict=True)
    action: Literal["pause", "cancel", "resume", "redirect"]
    instruction: str | None = Field(default=None, min_length=1, max_length=12000)
    acknowledge_unknown: bool = Field(default=False, strict=True)


def _service(request: Request) -> TaskService:
    return TaskService(request.app.state.database)


def _view(detail) -> dict:
    task = detail.task
    run = detail.runs[-1] if detail.runs else None
    terminal = task.status in ("completed", "failed", "cancelled")
    controls = [] if terminal else ["cancel"]
    if task.status in ("paused", "waiting_approval", "waiting_resource"):
        controls.append("resume")
    elif not terminal:
        controls.extend(["pause", "redirect"])
    result = None
    if run is not None and task.status == "completed":
        result_id = run.checkpoint.get("result_message_id")
        messages = [
            message
            for message in detail.messages
            if message.role == "assistant" and str(message.id) == result_id
        ]
        if messages:
            message = messages[-1]
            result = {"content": message.content, "created_at": message.created_at.isoformat()}
    return {
        "id": str(task.id),
        "agent_id": str(task.agent_id),
        "conversation_id": str(task.conversation_id) if task.conversation_id else None,
        "title": task.title,
        "objective": task.objective,
        "expected_result": task.expected_result,
        "status": task.status,
        "revision": task.revision,
        "created_at": task.created_at.isoformat(),
        "updated_at": task.updated_at.isoformat(),
        "active_run_id": str(run.id) if run else None,
        "control_requested": (
            "pause"
            if task.desired_state == "paused" and task.status == "running"
            else "cancel"
            if task.desired_state == "cancelled" and task.status == "running"
            else None
        ),
        "available_controls": controls,
        "max_calls": task.max_calls,
        "max_active_seconds": task.max_active_seconds,
        "calls_started": task.calls_started,
        "active_milliseconds": task.active_milliseconds,
        "latest_run": (
            {
                "id": str(run.id),
                "status": run.status,
                "provider": run.provider,
                "model": run.model,
                "started_at": run.started_at.isoformat() if run.started_at else None,
                "finished_at": run.finished_at.isoformat() if run.finished_at else None,
                "error_code": run.error,
                "result": result,
            }
            if run
            else None
        ),
        "progress": {
            "code": run.checkpoint.get("progress", task.status) if run else task.status,
            "created_at": run.updated_at.isoformat() if run else task.updated_at.isoformat(),
        },
    }


@router.get("/{agent_id}/tasks")
def list_tasks(agent_id: UUID, request: Request, session: _SESSION) -> dict:
    service = _service(request)
    records = service.list(agent_id, limit=100)
    return {
        "tasks": [_view(service.detail(agent_id, task.id)) for task in records],
        "has_more": len(records) == 100,
        "worker": worker_status(request.app.state.store),
    }


@router.get("/{agent_id}/tasks/{task_id}")
def detail_task(agent_id: UUID, task_id: UUID, request: Request, session: _SESSION) -> dict:
    detail = _service(request).detail(agent_id, task_id)
    events = [
        {
            "id": str(command.id),
            "code": command.kind,
            "created_at": command.created_at.isoformat(),
            "run_id": None,
            "content": command.instruction or None,
        }
        for command in detail.commands
    ]
    events.extend(
        {
            "id": str(call.id),
            "code": call.status,
            "created_at": call.updated_at.isoformat(),
            "run_id": str(call.run_id),
        }
        for call in detail.calls
    )
    events.sort(key=lambda event: (event["created_at"], event["id"]))
    return {"task": _view(detail), "events": events, "has_more": detail.has_more}


@router.post("/{agent_id}/tasks", status_code=201)
def create_task(agent_id: UUID, body: CreateTaskInput, request: Request, session: _SESSION) -> dict:
    # A conversa indicada é origem; o worker sempre usa outra conversa exclusiva.
    if body.conversation_id is not None:
        with request.app.state.store.transaction(write=False) as unit:
            conversation = unit.conversations.get(body.conversation_id)
            if conversation is None or conversation.agent_id != agent_id:
                raise NotFoundError("Conversa não encontrada para esta abelha.")
    service = _service(request)
    task = service.create(
        agent_id, TaskInput.model_validate(body.model_dump(exclude={"conversation_id"}))
    )
    return _view(service.detail(agent_id, task.id))


@router.post("/{agent_id}/tasks/{task_id}/control")
def control_task(
    agent_id: UUID, task_id: UUID, body: ControlInput, request: Request, session: _SESSION
) -> dict:
    service = _service(request)
    value = body.model_dump(exclude={"action"}) | {"kind": body.action}
    try:
        command = TaskCommand.model_validate(value)
    except ValidationError:
        raise HTTPException(
            422, detail={"code": "validation_failed", "message": "Confira o comando informado."}
        ) from None
    task = service.command(agent_id, task_id, command)
    return _view(service.detail(agent_id, task.id))
