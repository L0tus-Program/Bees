"""Comandos de tarefas textuais; nenhum método deste serviço faz rede."""

import hashlib
import json
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from bees_core.models import Conversation, Message, Run, Task, utc_now
from bees_core.models import TaskCommand as StoredCommand
from bees_core.providers.contracts import validate_capabilities
from bees_core.providers.errors import ProviderError
from bees_core.providers.service import PreparedChat, ProviderService
from bees_core.storage.database import Database
from bees_core.storage.store import NotFoundError, RevisionConflict, StateStore


class TaskError(RuntimeError):
    """Erro de domínio seguro para API/CLI, sem texto de usuário ou segredo."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class _Input(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)


class TaskInput(_Input):
    client_request_id: UUID
    title: str = Field(min_length=1, max_length=200)
    objective: str = Field(min_length=1, max_length=12000)
    expected_result: str = Field(default="", max_length=4000)
    max_calls: int = Field(default=3, ge=1, le=50, strict=True)
    max_active_seconds: int = Field(default=120, ge=1, le=1800, strict=True)

    @field_validator("title", "objective")
    @classmethod
    def nonempty(cls, value: str) -> str:
        value = value.strip()
        if not value or "\x00" in value:
            raise ValueError("Texto de tarefa vazio ou inválido.")
        return value


class TaskCommandInput(_Input):
    client_request_id: UUID
    expected_revision: int = Field(ge=1, strict=True)
    kind: Literal["pause", "resume", "cancel", "redirect"]
    instruction: str | None = Field(default=None, min_length=1, max_length=12000)
    acknowledge_unknown: bool = Field(default=False, strict=True)

    @model_validator(mode="after")
    def coherent(self):
        if self.kind == "redirect":
            if (
                self.instruction is None
                or not self.instruction.strip()
                or "\x00" in self.instruction
            ):
                raise ValueError("Redirecionamento exige instrução concreta.")
        elif self.instruction is not None:
            raise ValueError("Somente redirecionamento aceita instrução.")
        if self.acknowledge_unknown and self.kind != "resume":
            raise ValueError("Reconhecimento só é válido na retomada.")
        return self


# Compatibilidade explícita do contrato de transporte, distinta da entidade persistida.
TaskCommand = TaskCommandInput


class ModelCallSummary(_Input):
    id: UUID
    run_id: UUID
    ordinal: int
    status: str
    error_code: str | None = None
    output_message_id: UUID | None = None
    obsolete: bool = False
    acknowledged: bool = False
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None


class TaskCommandSummary(_Input):
    id: UUID
    kind: str
    created_at: datetime
    instruction: str | None = None


class TaskDetail(_Input):
    task: Task
    runs: list[Run]
    calls: list[ModelCallSummary]
    commands: list[TaskCommandSummary]
    messages: list[Message]
    has_more: bool = False


def request_digest(value: BaseModel) -> str:
    payload = json.dumps(
        value.model_dump(mode="json"), sort_keys=True, ensure_ascii=False, separators=(",", ":")
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class TaskService:
    def __init__(self, database: Database) -> None:
        self.store = StateStore(database)

    @staticmethod
    def _task(uow, agent_id, task_id) -> Task:
        task = uow.tasks.get(task_id)
        if task is None or task.agent_id != UUID(str(agent_id)):
            raise NotFoundError("Tarefa não encontrada.")
        return task

    def list(self, agent_id: UUID | str, *, limit=100, offset=0) -> list[Task]:
        with self.store.transaction(write=False) as uow:
            ProviderService._agent(uow, agent_id)
            return uow.tasks.list(
                agent_id=UUID(str(agent_id)), limit=limit, offset=offset, newest_first=True
            )

    def create(self, agent_id: UUID | str, value: TaskInput) -> Task:
        value = TaskInput.model_validate(value.model_dump(mode="python"))
        agent_id = UUID(str(agent_id))
        digest = request_digest(value)
        with self.store.transaction(source="task_create") as uow:
            existing = uow.tasks.find_submission(value.client_request_id)
            if existing is not None:
                if (
                    existing.agent_id != agent_id
                    or existing.metadata.get("submission_hash") != digest
                ):
                    raise TaskError("idempotency_conflict")
                return existing
            agent = ProviderService._agent(uow, agent_id)
            if agent.status != "active":
                raise TaskError("agent_inactive")
            config = ProviderService._validated_config(agent.provider_config)
            validate_capabilities(config)
            conversation = Conversation(
                agent_id=agent.id, title=value.title, metadata={"execution_kind": "text_task"}
            )
            task = Task(
                agent_id=agent.id,
                conversation_id=conversation.id,
                title=value.title,
                objective=value.objective,
                expected_result=value.expected_result,
                submission_key=value.client_request_id,
                max_calls=value.max_calls,
                max_active_seconds=value.max_active_seconds,
                metadata={"submission_hash": digest, "execution_kind": "text_task"},
            )
            uow.conversations.create(conversation)
            uow.tasks.create(task)
            uow.runs.create(
                Run(
                    task_id=task.id,
                    provider=config.kind,
                    model=config.model,
                    provider_config=config.model_dump(mode="json"),
                    checkpoint={"progress": "queued", "directive_revision": 0},
                    metadata={"agent_revision": agent.revision},
                )
            )
            return task

    def _ready_current(self, uow, run) -> bool:
        """Uma resposta pausada só conclui sem nova chamada se o contexto ainda vale."""
        if run.checkpoint.get("result_ready") is not True:
            return False
        output_id = run.checkpoint.get("result_message_id")
        calls = uow.model_calls.list(run_id=run.id, status="confirmed", limit=1000)
        call = next((entry for entry in calls if str(entry.output_message_id) == output_id), None)
        if call is None or call.metadata.get("obsolete") is True:
            return False
        prepared = PreparedChat.model_validate(call.snapshot["prepared"])
        records = ProviderService._records(uow, prepared.conversation_id)
        output = next((entry for entry in records if entry.id == call.output_message_id), None)
        if (
            output is None
            or ProviderService._normalized(output).model_dump(mode="json")
            != call.response["message"]
        ):
            return False
        previous_records = [entry for entry in records if entry.id != output.id]
        if ProviderService._history_digest(previous_records) != prepared.history_digest:
            return False
        completed_snapshot = prepared.model_copy(
            update={
                "conversation_revision": prepared.conversation_revision + 1,
                "history_digest": ProviderService._history_digest(records),
            }
        )
        try:
            ProviderService(self.store.database).validate_snapshot(uow, completed_snapshot)
        except ProviderError, NotFoundError:
            return False
        return True

    def command(self, agent_id: UUID | str, task_id: UUID | str, value: TaskCommandInput) -> Task:
        value = TaskCommandInput.model_validate(value.model_dump(mode="python"))
        with self.store.transaction(source="task_command") as uow:
            task = self._task(uow, agent_id, task_id)
            existing = uow.task_commands.find_request(task.id, value.client_request_id)
            digest = request_digest(value)
            if existing is not None:
                if existing.payload.get("request_hash") != digest:
                    raise TaskError("idempotency_conflict")
                return task
            if task.revision != value.expected_revision:
                raise RevisionConflict("Revisão da tarefa mudou.")
            if task.status in ("completed", "failed", "cancelled"):
                raise TaskError("terminal_task")
            runs = uow.runs.list(task_id=task.id, limit=1000)
            if not runs:
                raise TaskError("invalid_task_state")
            run = runs[-1]
            if value.kind == "resume" and task.status not in ("paused", "waiting_resource"):
                raise TaskError("invalid_task_state")
            calls = [
                call
                for existing_run in runs
                for call in uow.model_calls.list(run_id=existing_run.id, limit=1000)
            ]
            pending_unknown = any(
                call.status == "outcome_unknown" and call.unknown_acknowledged_at is None
                for call in calls
            )
            if pending_unknown and value.kind in ("resume", "redirect"):
                if value.kind != "resume" or not value.acknowledge_unknown:
                    raise TaskError("unknown_requires_ack")
            result_ready = self._ready_current(uow, run) and not pending_unknown
            if (
                value.kind == "resume"
                and not result_ready
                and (
                    task.calls_started >= task.max_calls
                    or task.active_milliseconds >= task.max_active_seconds * 1000
                )
            ):
                raise TaskError("task_limit_reached")
            stamp = utc_now()
            command = StoredCommand(
                task_id=task.id,
                client_request_id=value.client_request_id,
                kind=value.kind,
                expected_revision=value.expected_revision,
                payload={
                    "request_hash": digest,
                    "instruction": value.instruction,
                    "acknowledge_unknown": value.acknowledge_unknown,
                },
            )
            uow.task_commands.create(command)
            checkpoint = dict(run.checkpoint)
            updates = {"control_revision": task.control_revision + 1}
            run_updates = {}
            replace_run = False
            if value.kind == "cancel":
                updates |= {"desired_state": "cancelled", "status": "cancelled"}
                checkpoint["progress"] = "cancelled"
                run_updates |= {"status": "cancelled", "finished_at": stamp}
            elif value.kind == "pause":
                updates["desired_state"] = "paused"
                if task.status != "running":
                    updates["status"] = "paused"
                    run_updates["status"] = "paused"
                    checkpoint["progress"] = "paused"
            elif value.kind == "resume":
                agent = ProviderService._agent(uow, task.agent_id)
                if agent.status != "active":
                    raise TaskError("agent_inactive")
                config = ProviderService._validated_config(agent.provider_config)
                validate_capabilities(config)
                if pending_unknown:
                    uow.execution.acknowledge_unknown(task.id, command_id=command.id, now=stamp)
                updates |= {"desired_state": "running", "status": "queued"}
                run_updates |= {
                    "status": "queued",
                    "error": None,
                    "finished_at": None,
                    "provider": config.kind,
                    "model": config.model,
                    "provider_config": config.model_dump(mode="json"),
                    "metadata": run.metadata | {"agent_revision": agent.revision},
                }
                checkpoint |= {"progress": "queued", "attention_required": False}
                if result_ready:
                    updates["status"] = "completed"
                    run_updates |= {"status": "completed", "finished_at": stamp}
                    checkpoint["progress"] = "completed"
                else:
                    # A execução anterior mantém modelo/configuração/proveniência reais.
                    checkpoint.pop("result_message_id", None)
                    checkpoint["result_ready"] = False
                    uow.runs.create(
                        Run(
                            task_id=task.id,
                            provider=config.kind,
                            model=config.model,
                            provider_config=config.model_dump(mode="json"),
                            checkpoint=checkpoint,
                            metadata={"agent_revision": agent.revision},
                        )
                    )
                    replace_run = True
            else:
                checkpoint |= {
                    "latest_instruction": value.instruction,
                    "directive_revision": int(checkpoint.get("directive_revision", 0)) + 1,
                    "directive_command_id": str(command.id),
                    "result_ready": False,
                }
                if task.status not in ("running", "paused") and task.desired_state == "running":
                    updates["status"] = "queued"
                    run_updates["status"] = "queued"
                    checkpoint["progress"] = "queued"
            run_updates["checkpoint"] = checkpoint
            if not replace_run:
                uow.runs.update(run.model_copy(update=run_updates), expected_revision=run.revision)
            return uow.tasks.update(
                task.model_copy(update=updates), expected_revision=task.revision
            )

    def detail(self, agent_id: UUID | str, task_id: UUID | str) -> TaskDetail:
        with self.store.transaction(write=False) as uow:
            task = self._task(uow, agent_id, task_id)
            runs = uow.runs.list(task_id=task.id, limit=1000)
            calls = [
                call for run in runs for call in uow.model_calls.list(run_id=run.id, limit=1000)
            ]
            commands = uow.task_commands.list(task_id=task.id, limit=101)
            messages = (
                uow.messages.list(conversation_id=task.conversation_id, limit=201)
                if task.conversation_id is not None
                else []
            )
            return TaskDetail(
                task=task,
                runs=runs,
                calls=[
                    ModelCallSummary(
                        id=call.id,
                        run_id=call.run_id,
                        ordinal=call.ordinal,
                        status=call.status,
                        error_code=call.error_code,
                        output_message_id=call.output_message_id,
                        obsolete=call.metadata.get("obsolete") is True,
                        acknowledged=call.unknown_acknowledged_at is not None,
                        created_at=call.created_at,
                        updated_at=call.updated_at,
                        started_at=call.started_at,
                        finished_at=call.finished_at,
                    )
                    for call in calls
                ],
                commands=[
                    TaskCommandSummary(
                        id=command.id,
                        kind=command.kind,
                        created_at=command.created_at,
                        instruction=command.payload.get("instruction"),
                    )
                    for command in commands[:100]
                ],
                messages=messages[:200],
                has_more=len(commands) > 100 or len(messages) > 200,
            )
