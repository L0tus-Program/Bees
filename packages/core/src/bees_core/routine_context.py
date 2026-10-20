"""Autoridade atual de ocorrência, revalidada no writer antes de cada despacho."""

import json
from uuid import UUID

from bees_core.routines import RoutineService
from bees_core.storage.store import IntegrityError, NotFoundError
from bees_core.tasks import TaskError
from bees_core.tools import digest


def validate_routine_task(unit, task):
    value = task.metadata.get("routine_execution")
    if value is None and "routine_execution_hash" not in task.metadata:
        return None
    try:
        if (
            not isinstance(value, dict)
            or set(value)
            != {"routine_id", "revision", "occurrence_id", "scheduled_at", "definition"}
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
            routine.status != "active"
            or routine.revision != value["revision"]
            or routine.definition.execution_mode != "scheduled"
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
        target = task.metadata.get("tool_cycle_binding", {})
        plugin = unit.plugins.get(pinned.plugin_id) if pinned else None
        if (
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
    except ValueError, TypeError, KeyError, AttributeError, NotFoundError, IntegrityError:
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
            if key not in {"occurrence_id", "scheduled_at"}
        }
    )
