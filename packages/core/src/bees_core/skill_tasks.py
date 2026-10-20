"""Reexecução humana de skill no coordenador limitado existente, sem rede."""

from uuid import UUID

from bees_core.resource_tools import MODEL_TASK_KIND
from bees_core.skill_context import SkillTaskInput, snapshot
from bees_core.tasks import TaskError, request_digest
from bees_core.tool_cycle import ToolTaskInput, ToolTaskService
from bees_core.tools import digest


def replay_skill_task(unit, agent_id, value, *, routine_binding=None):
    prior = unit.tasks.find_submission(value.client_request_id)
    if prior is not None and (
        prior.agent_id != UUID(str(agent_id))
        or prior.metadata.get("execution_kind") != MODEL_TASK_KIND
        or prior.metadata.get("skill_request_hash") != request_digest(value)
        or prior.metadata.get("routine_execution") != routine_binding
    ):
        raise TaskError("idempotency_conflict")
    return prior


class SkillTaskService(ToolTaskService):
    def create(self, agent_id, value):
        value = SkillTaskInput.model_validate(
            value.model_dump() if isinstance(value, SkillTaskInput) else value
        )
        with self.store.transaction(actor="user", source="skill_task_create") as unit:
            return self.create_skill_in_unit(unit, agent_id, value)

    def create_skill_in_unit(self, unit, agent_id, value, *, routine_binding=None):
        prior = replay_skill_task(unit, agent_id, value, routine_binding=routine_binding)
        if prior is not None:
            return prior
        binding = snapshot(unit, agent_id, value)
        definition = binding["definition"]
        command = ToolTaskInput(
            client_request_id=value.client_request_id,
            title=routine_binding["definition"]["name"] if routine_binding else definition["name"],
            objective="Execute o procedimento fixado e produza uma nova entrega para as "
            "entradas desta tarefa; confira os critérios de conclusão.",
            expected_result=definition["output"],
            max_calls=value.max_calls,
            max_active_seconds=value.max_active_seconds,
        )
        return self.create_bound_in_unit(
            unit,
            agent_id,
            command,
            metadata={
                "skill_execution": binding,
                "skill_execution_hash": digest(binding),
                "skill_request_hash": request_digest(value),
            }
            | (
                {
                    "routine_execution": routine_binding,
                    "routine_execution_hash": digest(routine_binding),
                }
                if routine_binding
                else {}
            ),
            routine_id=UUID(routine_binding["routine_id"]) if routine_binding else None,
        )
