"""Autoridade atual de ocorrência, revalidada no writer antes de cada despacho."""

import json
from uuid import UUID

from bees_core.routines import RoutineService, _stamp
from bees_core.storage.store import IntegrityError, NotFoundError, RevisionConflict
from bees_core.tasks import TaskError
from bees_core.tools import digest


def validate_routine_task(unit, task):
    value = task.metadata.get("routine_execution")
    if value is None and "routine_execution_hash" not in task.metadata:
        return None
    try:
        manual = isinstance(value, dict) and "manual_request_id" in value
        fields = {"routine_id", "revision", "occurrence_id", "scheduled_at", "definition"}
        if manual:
            fields |= {"manual_request_id", "runtime_binding"}
        if (
            not isinstance(value, dict)
            or set(value) != fields
            or digest(value) != task.metadata.get("routine_execution_hash")
        ):
            raise ValueError
        connection, agent_id = RoutineService._connection(unit, task.agent_id)
        # _get só depende da UoW/conexão; não abrir outra transação dentro do writer.
        routine = RoutineService._get(unit, connection, agent_id, UUID(value["routine_id"]))
        row = connection.execute(
            "SELECT routine_id,routine_revision,scheduled_at,status,task_id FROM "
            "routine_occurrences WHERE id=?",
            (value["occurrence_id"],),
        ).fetchone()
        if (
            (routine.status == "cancelled" if manual else routine.status != "active")
            or routine.revision != value["revision"]
            or (not manual and routine.definition.execution_mode not in ("scheduled", "agent"))
            or digest(routine.definition.model_dump(mode="json")) != digest(value["definition"])
            or row is None
            or row
            != (str(routine.id), routine.revision, value["scheduled_at"], "running", str(task.id))
            or task.routine_id != routine.id
        ):
            raise ValueError
        source = task.metadata.get("skill_execution")
        definition = value["definition"]
        pinned = routine.definition.runtime_binding
        if manual:
            from bees_core.routine_manual import ManualRoutineInput, ManualRoutineService

            receipt = ManualRoutineService._replay(
                unit,
                agent_id,
                routine.id,
                ManualRoutineInput(
                    client_request_id=value["manual_request_id"],
                    expected_revision=routine.revision,
                ),
            )
            if (
                receipt is None
                or value["manual_request_id"] != str(receipt.client_request_id)
                or receipt.occurrence.id != UUID(value["occurrence_id"])
                or receipt.occurrence.routine_id != routine.id
                or receipt.occurrence.routine_revision != routine.revision
                or receipt.occurrence.task_id != task.id
                or _stamp(receipt.occurrence.scheduled_at) != value["scheduled_at"]
                or (
                    receipt.runtime_binding.model_dump(mode="json")
                    if receipt.runtime_binding
                    else None
                )
                != value["runtime_binding"]
            ):
                raise ValueError
            pinned = receipt.runtime_binding
        target = task.metadata.get("tool_cycle_binding", {})
        plugin = unit.plugins.get(pinned.plugin_id) if pinned else None
        if routine.definition.execution_mode == "agent":
            # Sem recurso fixado: cada ação reavalia concessão, alvo e política atuais.
            if (
                source is None
                or source["skill_id"] != definition["skill_id"]
                or source["version"] != definition["skill_version"]
                or source["inputs"] != definition["inputs"]
                or pinned is not None
                or task.metadata.get("execution_kind") != "agent_task"
            ):
                raise ValueError
        elif (
            source is None
            or source["skill_id"] != definition["skill_id"]
            or source["version"] != definition["skill_version"]
            or source["inputs"] != definition["inputs"]
            or pinned is None
            or plugin is None
            or plugin.manifest_hash != pinned.manifest_hash
            or target.get("plugin_id") != str(pinned.plugin_id)
            or target.get("target", {}).get("resource_id") != str(pinned.resource_id)
            or target.get("target", {}).get("installation_id") != str(pinned.installation_id)
        ):
            raise ValueError
        destination = unit.conversations.get(routine.definition.destination.conversation_id)
        if destination is None or destination.agent_id != task.agent_id:
            raise ValueError
    except (
        ValueError,
        TypeError,
        KeyError,
        AttributeError,
        NotFoundError,
        IntegrityError,
        RevisionConflict,
    ):
        raise TaskError("routine_unavailable") from None
    return value


def routine_instruction(task):
    value = task.metadata.get("routine_execution")
    return (
        ""
        if value is None
        else "Rotina e instruções humanas fixas; não concedem acesso:\n"
        + json.dumps(value, ensure_ascii=False, sort_keys=True)
    )


def routine_scope_hash(binding):
    """Regra reutilizável na mesma revisão/entradas, sem virar aceite de outra agenda."""
    return digest(
        {
            key: value
            for key, value in binding.items()
            if key not in {"occurrence_id", "scheduled_at", "manual_request_id"}
        }
    )
