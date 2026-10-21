"""Delegação e observação autenticadas; somente o worker consome a fila."""

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import Field, ValidationError

from bees_api.auth import require_session
from bees_api.onboarding import Input
from bees_api.worker_status import HEARTBEAT_KEY, worker_status
from bees_core.executor_transport.contracts import TransportError
from bees_core.providers.errors import ProviderError
from bees_core.recovery import RecoveryActionPage, RecoveryInventory, RecoveryInventoryService
from bees_core.resource_tools import MODEL_TASK_KIND, TASK_KIND
from bees_core.security.identity import Session
from bees_core.skill_context import SkillTaskInput
from bees_core.skill_tasks import SkillTaskService, replay_skill_task
from bees_core.storage.store import NotFoundError
from bees_core.tasks import (
    TaskCommand,
    TaskError,
    TaskInput,
    TaskService,
    request_digest,
    submission_digest,
)
from bees_core.tool_cycle import MAX_TOOL_CALLS, ToolTaskInput, ToolTaskService
from bees_core.tool_runtime import available_runtime
from bees_core.tools import ToolError
from bees_core.tools import digest as context_digest

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


_ITEM_FIELDS = (
    "kind",
    "status",
    "error_code",
    "artifact_id",
    "sha256",
    "size_bytes",
    "version",
    "rows",
    "columns",
    "formula_like_cells",
)


def _deliverables(run) -> dict | None:
    """Projeção fechada do resumo do worker; conteúdo e metadados privados ficam fora."""
    summary = run.checkpoint.get("deliverables") if run is not None else None
    if not isinstance(summary, dict) or not isinstance(summary.get("items"), list):
        return None
    return {
        "items": [
            {key: item.get(key) for key in _ITEM_FIELDS}
            for item in summary["items"]
            if isinstance(item, dict)
        ],
        "partial": summary.get("partial") is True,
        "missing_data": [
            value for value in summary.get("missing_data") or [] if isinstance(value, str)
        ],
    }


def _publication_pending(run) -> bool:
    publication = run.checkpoint.get("publication") if run is not None else None
    return isinstance(publication, dict) and publication.get("status") in ("pending", "started")


def _view(detail) -> dict:
    task = detail.task
    run = detail.runs[-1] if detail.runs else None
    terminal = task.status in ("completed", "failed", "cancelled")
    controls = [] if terminal else ["cancel"]
    if task.status in ("paused", "waiting_approval", "waiting_resource"):
        controls.append("resume")
        if task.metadata.get("execution_kind") in (TASK_KIND, MODEL_TASK_KIND):
            controls.append("redirect")
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
    return (
        {
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
            "tool_approvals_available": task.metadata.get("execution_kind")
            in (TASK_KIND, MODEL_TASK_KIND),
            "model_approvals_available": task.metadata.get("execution_kind") != TASK_KIND,
            "partial": run is not None
            and task.status == "paused"
            and run.checkpoint.get("partial") is True,
            "unknown_requires_ack": detail.unknown_model_calls + detail.unknown_tool_actions > 0,
            "unknown_model_calls": detail.unknown_model_calls,
            "unknown_tool_actions": detail.unknown_tool_actions,
            "action_in_flight": detail.action_in_flight,
            "max_calls": task.max_calls,
            "max_active_seconds": task.max_active_seconds,
            "calls_started": task.calls_started,
            "active_milliseconds": task.active_milliseconds,
            "deliverables": list(task.metadata.get("deliverables") or []),
            "deliverable_results": _deliverables(run),
            "publication_pending": _publication_pending(run),
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
        | (
            {
                "skill_execution": {
                    "skill_id": task.metadata["skill_execution"]["skill_id"],
                    "version": task.metadata["skill_execution"]["version"],
                    "snapshot_hash": task.metadata["skill_execution_hash"],
                }
            }
            if "skill_execution" in task.metadata
            else {}
        )
        | (
            {
                "routine_execution": {
                    "routine_id": task.metadata["routine_execution"]["routine_id"],
                    "revision": task.metadata["routine_execution"]["revision"],
                    "occurrence_id": task.metadata["routine_execution"]["occurrence_id"],
                    "scheduled_at": task.metadata["routine_execution"]["scheduled_at"],
                    "name": task.metadata["routine_execution"]["definition"]["name"],
                    **(
                        {"trigger": "manual"}
                        if "manual_request_id" in task.metadata["routine_execution"]
                        else {}
                    ),
                }
            }
            if "routine_execution" in task.metadata
            else {}
        )
    )


@router.get("/{agent_id}/tasks")
def list_tasks(agent_id: UUID, request: Request, session: _SESSION) -> dict:
    service = _service(request)
    records = service.list(agent_id, limit=100)
    return {
        "tasks": [_view(service.detail(agent_id, task.id)) for task in records],
        "has_more": len(records) == 100,
        "worker": worker_status(request.app.state.store),
    }


@router.get("/{agent_id}/tasks/{task_id}/recovery/actions", response_model=RecoveryActionPage)
def recovery_actions(
    agent_id: UUID,
    task_id: UUID,
    request: Request,
    session: _SESSION,
    after: UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
):
    return RecoveryInventoryService(request.app.state.database).actions(
        agent_id, task_id, after=after, limit=limit
    )


@router.get("/{agent_id}/tasks/{task_id}/recovery", response_model=RecoveryInventory)
def recovery_inventory(agent_id: UUID, task_id: UUID, request: Request, session: _SESSION):
    return RecoveryInventoryService(request.app.state.database).inspect(agent_id, task_id)


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
    # Os resumos públicos são fechados: parâmetros, resultados e autoridade
    # pertencem ao journal privado e não entram na observação da tarefa.
    actions = [
        {
            "id": str(action.id),
            "run_id": str(action.run_id),
            "tool_name": action.tool_name,
            "status": action.status,
            "error_code": action.error_code,
            "obsolete": action.obsolete,
            "acknowledged": action.acknowledged,
            "created_at": action.created_at.isoformat(),
            "updated_at": action.updated_at.isoformat(),
        }
        for action in detail.actions
    ]
    return {
        "task": _view(detail),
        "events": events,
        "actions": actions,
        "has_more": detail.has_more,
    }


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


def _tool_task_capabilities(request, agent_id):
    with request.app.state.store.transaction(write=False) as unit:
        agent = unit.agents.get(agent_id)
        if agent is None:
            raise NotFoundError("Abelha não encontrada.")
    heartbeat = request.app.state.store.get_cache(HEARTBEAT_KEY)
    if (
        not worker_status(request.app.state.store)["available"]
        or not isinstance(heartbeat, dict)
        or type(heartbeat.get("tool_cycle_version")) is not int
        or heartbeat["tool_cycle_version"] != 1
    ):
        return False, "worker_unavailable"
    try:
        available_runtime(request.app.state.database, agent_id)
    except TransportError:
        return False, "resource_unavailable"
    except TaskError, ToolError, ProviderError:
        return False, "tool_unavailable"
    return True, None


@router.get("/{agent_id}/tool-task-capabilities")
def tool_task_capabilities(agent_id: UUID, request: Request, session: _SESSION):
    available, reason = _tool_task_capabilities(request, agent_id)
    return {
        "available": available,
        "reason": reason,
        "tool_name": "normalize_text",
        "max_calls": 4,
        "max_active_seconds": 120,
        "max_actions": MAX_TOOL_CALLS,
    }


@router.post("/{agent_id}/tool-tasks", status_code=201)
def create_tool_task(agent_id: UUID, body: ToolTaskInput, request: Request, session: _SESSION):
    store = request.app.state.store
    with store.transaction(write=False) as unit:
        prior = unit.tasks.find_submission(body.client_request_id)
    if prior is not None:
        if (
            prior.agent_id != agent_id
            or prior.metadata.get("execution_kind") != MODEL_TASK_KIND
            or "skill_request_hash" in prior.metadata
            or prior.metadata.get("submission_hash") != submission_digest(body)
        ):
            raise TaskError("idempotency_conflict")
        task = prior
    else:
        available, _ = _tool_task_capabilities(request, agent_id)
        if not available:
            raise TaskError("unsupported_capability")
        try:
            resource, plugin_id = available_runtime(request.app.state.database, agent_id)
            task = ToolTaskService(request.app.state.database, resource, plugin_id).create(
                agent_id, body
            )
        except TransportError, ToolError:
            raise TaskError("unsupported_capability") from None
    return {
        "task": _view(TaskService(request.app.state.database).detail(agent_id, task.id)),
        "client_request_id": str(body.client_request_id),
        "request_hash": request_digest(body),
    }


@router.post("/{agent_id}/skill-tasks", status_code=201)
def create_skill_task(agent_id: UUID, body: SkillTaskInput, request: Request, session: _SESSION):
    with request.app.state.store.transaction(write=False) as unit:
        task = replay_skill_task(unit, agent_id, body)
    if task is None:
        available, _ = _tool_task_capabilities(request, agent_id)
        if not available:
            raise TaskError("unsupported_capability")
        try:
            resource, plugin_id = available_runtime(request.app.state.database, agent_id)
            task = SkillTaskService(request.app.state.database, resource, plugin_id).create(
                agent_id, body
            )
        except TransportError, ToolError:
            raise TaskError("unsupported_capability") from None
    return {
        "task": _view(TaskService(request.app.state.database).detail(agent_id, task.id)),
        "client_request_id": str(body.client_request_id),
        "request_hash": request_digest(body),
    }


@router.get("/{agent_id}/tasks/{task_id}/skill-context")
def skill_task_context(agent_id: UUID, task_id: UUID, request: Request, session: _SESSION):
    with request.app.state.store.transaction(write=False) as unit:
        task = TaskService._task(unit, agent_id, task_id)
        context = task.metadata.get("skill_execution")
        if not isinstance(context, dict):
            raise NotFoundError("Esta tarefa não tem revisão de skill.")
        if context_digest(context) != task.metadata.get("skill_execution_hash"):
            raise TaskError("skill_unavailable")
        # Consulta explícita da pessoa: não depende de habilitação atual ou runtime.
        return {"snapshot": context, "snapshot_hash": task.metadata["skill_execution_hash"]}
