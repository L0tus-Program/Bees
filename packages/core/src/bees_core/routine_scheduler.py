"""Agenda limitada no worker: transações curtas vinculam ocorrência e fila comum.

Não há efeito externo entre aquisição da ocorrência e criação da tarefa. O writer
SQLite serializa essa operação; o claim/lease/fence do executor passa a coordenar
todo despacho após o commit. Consultas HTTP nunca chamam este serviço.
"""

import asyncio
from datetime import datetime
from uuid import UUID, uuid5

from bees_core.executor_transport.contracts import TransportError
from bees_core.models import Message, utc_now
from bees_core.providers.errors import ProviderError
from bees_core.routine_context import validate_routine_task
from bees_core.routines import RoutineService, _stamp, _utc
from bees_core.skill_context import SkillTaskInput
from bees_core.skill_tasks import SkillTaskService
from bees_core.storage.store import NotFoundError, RevisionConflict, StateStore
from bees_core.tasks import TaskError
from bees_core.tool_runtime import available_runtime
from bees_core.tools import ToolError, digest


class RoutineScheduler:
    def __init__(self, database, *, clock=utc_now, batch_size=32):
        if type(batch_size) is not int or not 1 <= batch_size <= 100:
            raise ValueError("Lote inválido.")
        self.store = StateStore(database)
        self.routines = RoutineService(database, clock=clock)
        self.clock, self.batch_size = clock, batch_size

    @staticmethod
    def _link_task(connection, occurrence, task, now):
        connection.execute(
            "UPDATE routine_occurrences SET status='running',reason='task_created',task_id=?,"
            "revision=revision+1,updated_at=max(updated_at,?) WHERE id=? AND status='pending' "
            "AND revision=?",
            (str(task.id), _stamp(now), str(occurrence.id), occurrence.revision),
        )
        if connection.changes() != 1:
            raise RevisionConflict("Ocorrência mudou antes de vincular a tarefa.")

    @staticmethod
    def _notice(unit, occurrence, definition, status, reason):
        if definition.notification == "none" or (
            definition.notification == "failures" and status == "completed"
        ):
            return
        destination = unit.conversations.get(definition.destination.conversation_id)
        routine = unit.routines.get(occurrence.routine_id)
        if destination is None or routine is None or destination.agent_id != routine.agent_id:
            raise TaskError("routine_unavailable")
        message_id = uuid5(occurrence.id, "bees.routine.notice/v1")
        prior = unit.messages.get(message_id)
        if prior is not None:
            if prior.conversation_id != destination.id:
                raise TaskError("routine_unavailable")
            return
        unit.messages.create(
            Message(
                id=message_id,
                conversation_id=destination.id,
                role="assistant",
                source="routine_notification",
                content=(
                    "Uma ocorrência de rotina foi concluída. "
                    "Confira a tarefa no histórico de rotinas."
                )
                if status == "completed"
                else (
                    "Uma ocorrência de rotina exige revisão. "
                    "Confira o motivo no histórico de rotinas."
                ),
                metadata={
                    "origin": "routine_notification",
                    "routine_id": str(routine.id),
                    "occurrence_id": str(occurrence.id),
                    "task_id": str(occurrence.task_id) if occurrence.task_id else None,
                    "status": status,
                    "reason": reason,
                },
            )
        )

    def _skip(self, unit, occurrence, definition, reason, now):
        connection = unit.agents._context.connection
        connection.execute(
            "UPDATE routine_occurrences SET status='skipped',reason=?,revision=revision+1,"
            "updated_at=max(updated_at,?) WHERE id=? AND status='pending' AND revision=?",
            (reason, _stamp(now), str(occurrence.id), occurrence.revision),
        )
        if connection.changes() != 1:
            raise RevisionConflict("Ocorrência mudou antes da recusa.")
        self._notice(unit, occurrence, definition, "skipped", reason)

    def dispatch(self, occurrence_id, *, now=None):
        now = _utc(now or self.clock())
        # Abrir somente o recurso já preparado; nenhuma instalação/grant é criada.
        with self.store.transaction(write=False) as unit:
            connection = unit.agents._context.connection
            row = connection.execute(
                "SELECT o.*,r.agent_id FROM routine_occurrences o JOIN routines r ON "
                "r.id=o.routine_id WHERE o.id=?",
                (str(occurrence_id),),
            ).fetchone()
            if row is None:
                raise NotFoundError("Ocorrência não encontrada.")
            if row[5] != "pending":
                return False
            agent_id = UUID(row[-1])
        capability_error = None
        try:
            resource, plugin_id = available_runtime(self.store.database, agent_id)
            tasks = SkillTaskService(self.store.database, resource, plugin_id)
        except TransportError:
            capability_error = "resource_unavailable"
        except TaskError, ToolError, ProviderError:
            capability_error = "tool_unavailable"
        with self.store.transaction(actor="worker", source="routine_dispatch") as unit:
            connection = unit.agents._context.connection
            row = connection.execute(
                "SELECT * FROM routine_occurrences WHERE id=?", (str(occurrence_id),)
            ).fetchone()
            if row is None or row[5] != "pending":
                return False
            occurrence = self.routines._occurrence(row)
            routine = self.routines._get(unit, connection, agent_id, occurrence.routine_id)
            definition = routine.definition
            if definition is None:
                raise TaskError("routine_unavailable")
            if routine.status != "active" or routine.revision != occurrence.routine_revision:
                self._skip(unit, occurrence, definition, "routine_changed", now)
                return True
            if definition.execution_mode != "scheduled":
                self._skip(unit, occurrence, definition, "execution_not_enabled", now)
                return True
            if unit.safety.state().status != "running":
                return False
            if (now - occurrence.scheduled_at).total_seconds() > definition.grace_seconds:
                self._skip(unit, occurrence, definition, "misfire", now)
                return True
            if capability_error:
                self._skip(unit, occurrence, definition, capability_error, now)
                return True
            pinned = definition.runtime_binding
            plugin = unit.plugins.get(plugin_id)
            if (
                pinned is None
                or str(pinned.resource_id) != resource.identity.resource_id
                or str(pinned.installation_id) != resource.identity.installation_id
                or pinned.plugin_id != plugin_id
                or pinned.manifest_hash != plugin.manifest_hash
            ):
                self._skip(unit, occurrence, definition, "runtime_changed", now)
                return True
            binding = {
                "routine_id": str(routine.id),
                "revision": routine.revision,
                "occurrence_id": str(occurrence.id),
                "scheduled_at": row[3],
                "definition": definition.model_dump(mode="json"),
            }
            command = SkillTaskInput(
                client_request_id=uuid5(occurrence.id, "bees.routine.task/v1"),
                skill_id=definition.skill_id,
                skill_version=definition.skill_version,
                inputs=definition.inputs,
            )
            try:
                self.routines._validate_links(unit, connection, agent_id, definition, active=True)
                task = tasks.create_skill_in_unit(unit, agent_id, command, routine_binding=binding)
            except RevisionConflict:
                self._skip(unit, occurrence, definition, "skill_unavailable", now)
                return True
            except TaskError as error:
                if error.code not in {
                    "skill_unavailable",
                    "skill_inputs_invalid",
                    "unsupported_capability",
                    "agent_inactive",
                }:
                    raise
                self._skip(unit, occurrence, definition, error.code, now)
                return True
            self._link_task(connection, occurrence, task, now)
            validate_routine_task(unit, task)
            return True

    def synchronize(self, *, now=None):
        now = _utc(now or self.clock())
        with self.store.transaction(actor="worker", source="routine_reconcile") as unit:
            connection = unit.agents._context.connection
            rows = list(
                connection.execute(
                    "SELECT o.* FROM routine_occurrences o JOIN tasks t ON t.id=o.task_id "
                    "WHERE o.status='running' AND (t.status IN "
                    "('completed','failed','cancelled') OR "
                    "EXISTS(SELECT 1 FROM runs r JOIN model_calls c ON c.run_id=r.id WHERE "
                    "r.task_id=t.id AND c.status='outcome_unknown') OR "
                    "EXISTS(SELECT 1 FROM runs r JOIN actions a ON a.run_id=r.id WHERE "
                    "r.task_id=t.id AND a.status='outcome_unknown')) "
                    "ORDER BY o.updated_at,o.id LIMIT ?",
                    (self.batch_size,),
                )
            )
            for row in rows:
                occurrence = self.routines._occurrence(row)
                task = unit.tasks.get(occurrence.task_id)
                routine = unit.routines.get(occurrence.routine_id)
                binding = task.metadata.get("routine_execution", {})
                if (
                    task.agent_id != routine.agent_id
                    or task.routine_id != routine.id
                    or digest(binding) != task.metadata.get("routine_execution_hash")
                    or binding.get("routine_id") != str(routine.id)
                    or binding.get("revision") != occurrence.routine_revision
                    or binding.get("scheduled_at") != row[3]
                    or binding.get("occurrence_id") != str(occurrence.id)
                ):
                    raise TaskError("routine_unavailable")
                unknown = connection.execute(
                    "SELECT EXISTS(SELECT 1 FROM runs r JOIN model_calls c ON c.run_id=r.id "
                    "WHERE r.task_id=? AND c.status='outcome_unknown') OR "
                    "EXISTS(SELECT 1 FROM runs r JOIN actions a ON a.run_id=r.id WHERE "
                    "r.task_id=? AND a.status='outcome_unknown')",
                    (str(task.id), str(task.id)),
                ).get
                status = (
                    "outcome_unknown"
                    if unknown
                    else "completed"
                    if task.status == "completed"
                    else "failed"
                )
                reason = "outcome_unknown" if unknown else "task_" + task.status
                definition = self.routines._definition(
                    connection, routine.id, occurrence.routine_revision
                )
                if digest(definition.model_dump(mode="json")) != digest(binding.get("definition")):
                    raise TaskError("routine_unavailable")
                connection.execute(
                    "UPDATE routine_occurrences SET "
                    "status=?,reason=?,revision=revision+1,updated_at=max(updated_at,?) WHERE"
                    " id=? AND status='running' AND revision=?",
                    (status, reason, _stamp(now), str(occurrence.id), occurrence.revision),
                )
                self._notice(unit, occurrence, definition, status, reason)
            return len(rows)

    def tick(self, *, now: datetime | None = None):
        now = _utc(now or self.clock())
        work = self.synchronize(now=now)
        with self.store.transaction(write=False) as unit:
            if unit.safety.state().status != "running":
                return work
            connection = unit.agents._context.connection
            due = list(
                connection.execute(
                    "SELECT r.agent_id,r.id,r.revision FROM routines r JOIN routine_cursors c"
                    " ON c.routine_id=r.id AND c.routine_revision=r.revision "
                    "JOIN routine_definitions d ON d.routine_id=r.id AND d.revision=r.revision "
                    "WHERE r.status='active' AND "
                    "json_extract(d.definition_json,'$.execution_mode')='scheduled' AND "
                    "c.next_run_at<=? "
                    "ORDER BY c.next_run_at,r.id LIMIT ?",
                    (_stamp(now), self.batch_size),
                )
            )
        for agent_id, routine_id, revision in due:
            try:
                work += len(
                    self.routines.evaluate(
                        agent_id, routine_id, expected_revision=revision, now=now
                    )
                )
            except RevisionConflict:
                pass
        # Materializar e notificar são commits separados. A varredura recupera um
        # crash entre eles sem repetir o aviso nem expandir janelas perdidas.
        with self.store.transaction(actor="worker", source="routine_misfire_notice") as unit:
            connection = unit.agents._context.connection
            skipped = list(
                connection.execute(
                    "SELECT o.* FROM routine_occurrences o JOIN routine_definitions d "
                    "ON d.routine_id=o.routine_id AND d.revision=o.routine_revision "
                    "WHERE o.status='skipped' AND "
                    "json_extract(d.definition_json,'$.execution_mode')='scheduled' AND "
                    "json_extract(d.definition_json,'$.notification')<>'none' AND NOT EXISTS "
                    "(SELECT 1 FROM messages m WHERE m.source='routine_notification' "
                    "AND json_extract(m.metadata_json,'$.occurrence_id')=o.id) "
                    "ORDER BY o.scheduled_at,o.id LIMIT ?",
                    (self.batch_size,),
                )
            )
            for row in skipped:
                occurrence = self.routines._occurrence(row)
                definition = self.routines._definition(
                    connection, occurrence.routine_id, occurrence.routine_revision
                )
                self._notice(unit, occurrence, definition, "skipped", occurrence.reason)
        with self.store.transaction(write=False) as unit:
            rows = list(
                unit.agents._context.connection.execute(
                    "SELECT o.id FROM routine_occurrences o JOIN routines r ON r.id=o.routine_id "
                    "JOIN routine_definitions d ON d.routine_id=o.routine_id AND "
                    "d.revision=o.routine_revision "
                    "WHERE o.status='pending' AND "
                    "json_extract(d.definition_json,'$.execution_mode')='scheduled' ORDER BY "
                    "o.scheduled_at,o.id LIMIT ?",
                    (self.batch_size,),
                )
            )
        for (occurrence_id,) in rows:
            work += bool(self.dispatch(occurrence_id, now=now))
        return work

    async def run_forever(self):
        while True:
            self.tick()
            await asyncio.sleep(3)
