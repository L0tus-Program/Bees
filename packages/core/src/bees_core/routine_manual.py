"""Intenção humana manual: tarefa e ocorrência atômicas, sem ativar a agenda."""

from uuid import UUID, uuid5

from pydantic import Field

from bees_core.executor_transport.contracts import TransportError
from bees_core.models import utc_now
from bees_core.providers.errors import ProviderError
from bees_core.routines import (
    RoutineOccurrence,
    RoutineRuntimeBinding,
    RoutineService,
    _stamp,
    _utc,
    command_hash,
)
from bees_core.skill_context import SkillTaskInput
from bees_core.skill_tasks import AgentSkillTaskService, SkillTaskService
from bees_core.skills import Command, Contract
from bees_core.storage.store import IntegrityError, NotFoundError, RevisionConflict, StateStore
from bees_core.tasks import TaskError
from bees_core.tool_runtime import available_runtime
from bees_core.tools import ToolError


class ManualRoutineInput(Command):
    expected_revision: int = Field(ge=1, strict=True)


class ManualRoutineReceipt(Contract):
    client_request_id: UUID
    request_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    agent_id: UUID
    routine_id: UUID
    routine_revision: int = Field(ge=1, strict=True)
    runtime_binding: RoutineRuntimeBinding | None
    occurrence: RoutineOccurrence


class ManualRoutineService:
    def __init__(self, database, *, clock=utc_now):
        self.store, self.clock = StateStore(database), clock

    @staticmethod
    def _replay(unit, agent_id, routine_id, value):
        connection, agent_id = RoutineService._connection(unit, agent_id)
        row = connection.execute(
            "SELECT agent_id,target,request_hash,response_json FROM routine_commands "
            "WHERE client_request_id=?",
            (str(value.client_request_id),),
        ).fetchone()
        if row is None:
            return None
        if row[:3] != (str(agent_id), "manual:" + str(routine_id), command_hash(value)):
            raise RevisionConflict("Identificador já usado para outra intenção de rotina.")
        receipt = ManualRoutineReceipt.model_validate_json(row[3])
        if (
            receipt.agent_id != agent_id
            or receipt.routine_id != routine_id
            or receipt.routine_revision != value.expected_revision
            or receipt.client_request_id != value.client_request_id
            or receipt.request_hash != row[2]
        ):
            raise IntegrityError("Recibo manual inválido.")
        return receipt

    def replay(self, agent_id, routine_id, value):
        with self.store.transaction(write=False) as unit:
            return self._replay(unit, agent_id, UUID(str(routine_id)), value)

    def receipt(self, agent_id, routine_id, request_id):
        with self.store.transaction(write=False) as unit:
            connection, agent_id = RoutineService._connection(unit, agent_id)
            row = connection.execute(
                "SELECT response_json FROM routine_commands WHERE client_request_id=? "
                "AND agent_id=? AND target=?",
                (str(request_id), str(agent_id), "manual:" + str(routine_id)),
            ).fetchone()
            if row is None:
                raise NotFoundError("Intenção manual não encontrada nesta rotina.")
            prior = ManualRoutineReceipt.model_validate_json(row[0])
            return self._replay(
                unit,
                agent_id,
                UUID(str(routine_id)),
                ManualRoutineInput(
                    client_request_id=request_id, expected_revision=prior.routine_revision
                ),
            )

    def execute(self, agent_id, routine_id, value):
        value = ManualRoutineInput.model_validate(value.model_dump())
        routine_id = UUID(str(routine_id))
        prior = self.replay(agent_id, routine_id, value)
        if prior is not None:
            return prior
        with self.store.transaction(write=False) as unit:
            connection, _ = RoutineService._connection(unit, agent_id)
            current = RoutineService._get(unit, connection, UUID(str(agent_id)), routine_id)
            agent_mode = (
                current.definition is not None and current.definition.execution_mode == "agent"
            )
        resource = plugin_id = None
        if not agent_mode:
            try:
                resource, plugin_id = available_runtime(self.store.database, agent_id)
            except TransportError, ProviderError, ToolError, TaskError:
                raise TaskError("unsupported_capability") from None
        with self.store.transaction(actor="user", source="routine_manual") as unit:
            connection, agent_id = RoutineService._connection(unit, agent_id)
            prior = self._replay(unit, agent_id, routine_id, value)
            if prior is not None:
                return prior
            routine = RoutineService._get(unit, connection, agent_id, routine_id)
            if routine.revision != value.expected_revision:
                raise RevisionConflict("A rotina mudou; revise antes de executar.")
            if routine.status == "cancelled" or routine.definition is None:
                raise RevisionConflict("A rotina exige uma definição humana não cancelada.")
            if unit.safety.state().status != "running":
                raise TaskError("global_stop")
            definition = routine.definition
            if (definition.execution_mode == "agent") != agent_mode:
                raise RevisionConflict("A rotina mudou; revise antes de executar.")
            pinned = None
            if not agent_mode:
                plugin = unit.plugins.get(plugin_id)
                if plugin is None:
                    raise TaskError("unsupported_capability")
                pinned = RoutineRuntimeBinding(
                    installation_id=resource.identity.installation_id,
                    resource_id=resource.identity.resource_id,
                    plugin_id=plugin_id,
                    manifest_hash=plugin.manifest_hash,
                )
                if definition.runtime_binding is not None and definition.runtime_binding != pinned:
                    raise RevisionConflict("Recurso ou versão mudou; revise a seleção humana.")
            RoutineService._validate_links(unit, connection, agent_id, definition, active=True)
            if connection.execute(
                "SELECT 1 FROM routine_occurrences WHERE routine_id=? AND "
                "status IN ('pending','running','outcome_unknown') LIMIT 1",
                (str(routine.id),),
            ).fetchone():
                raise RevisionConflict("Já existe ocorrência pendente, em curso ou desconhecida.")
            now = _utc(self.clock())
            if connection.execute(
                "SELECT 1 FROM routine_occurrences WHERE routine_id=? AND "
                "routine_revision=? AND scheduled_at=?",
                (str(routine.id), routine.revision, _stamp(now)),
            ).fetchone():
                raise RevisionConflict("Já existe ocorrência neste instante; não foi repetida.")
            occurrence = RoutineOccurrence(
                id=uuid5(value.client_request_id, "bees.routine.manual/v1"),
                routine_id=routine.id,
                routine_revision=routine.revision,
                scheduled_at=now,
                window_end=now,
                status="pending",
                reason="manual",
                created_at=now,
                updated_at=now,
            )
            connection.execute(
                "INSERT INTO routine_occurrences VALUES(?,?,?,?,?,'pending','manual',NULL,1,?,?)",
                (
                    str(occurrence.id),
                    str(routine.id),
                    routine.revision,
                    _stamp(now),
                    _stamp(now),
                    _stamp(now),
                    _stamp(now),
                ),
            )
            binding = {
                "routine_id": str(routine.id),
                "revision": routine.revision,
                "occurrence_id": str(occurrence.id),
                "scheduled_at": _stamp(now),
                "definition": definition.model_dump(mode="json"),
                "manual_request_id": str(value.client_request_id),
                "runtime_binding": pinned.model_dump(mode="json") if pinned else None,
            }
            service = (
                AgentSkillTaskService(self.store.database)
                if agent_mode
                else SkillTaskService(self.store.database, resource, plugin_id)
            )
            task = service.create_skill_in_unit(
                unit,
                agent_id,
                SkillTaskInput(
                    client_request_id=uuid5(occurrence.id, "bees.routine.task/v1"),
                    skill_id=definition.skill_id,
                    skill_version=definition.skill_version,
                    inputs=definition.inputs,
                ),
                routine_binding=binding,
            )
            from bees_core.routine_scheduler import RoutineScheduler

            RoutineScheduler._link_task(connection, occurrence, task, now)
            occurrence = occurrence.model_copy(
                update={
                    "status": "running",
                    "reason": "task_created",
                    "revision": 2,
                    "task_id": task.id,
                    "metadata": {"origin": "manual", "task_status": "queued"},
                }
            )
            result = ManualRoutineReceipt(
                client_request_id=value.client_request_id,
                request_hash=command_hash(value),
                agent_id=agent_id,
                routine_id=routine.id,
                routine_revision=routine.revision,
                runtime_binding=pinned,
                occurrence=occurrence,
            )
            connection.execute(
                "INSERT INTO routine_commands VALUES(?,?,?,?,?,?)",
                (
                    str(value.client_request_id),
                    str(agent_id),
                    "manual:" + str(routine.id),
                    result.request_hash,
                    result.model_dump_json(),
                    _stamp(now),
                ),
            )
            from bees_core.routine_context import validate_routine_task

            validate_routine_task(unit, task)
            return result
