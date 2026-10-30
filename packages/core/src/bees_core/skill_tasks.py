"""Reexecução humana de skill no coordenador limitado existente, sem rede."""

from uuid import UUID

from pydantic import Field

from bees_core.agent_tools.cycle import AgentTaskInput, AgentTaskService
from bees_core.resource_tools import MODEL_TASK_KIND
from bees_core.skill_context import SkillTaskInput, snapshot
from bees_core.tasks import TaskError, request_digest
from bees_core.tool_cycle import ToolTaskInput, ToolTaskService
from bees_core.tools import digest


def replay_skill_task(unit, agent_id, value, *, routine_binding=None):
    prior = unit.tasks.find_submission(value.client_request_id)
    if prior is not None and (
        prior.agent_id != UUID(str(agent_id))
        or prior.metadata.get("execution_kind") not in (MODEL_TASK_KIND, "agent_task")
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
        from bees_core.skill_context import agent_supported
        from bees_core.skills import SkillDefinition

        if agent_supported(SkillDefinition.model_validate(definition)):
            # Skills com ferramentas do registro rodam no ciclo do agente.
            raise TaskError("unsupported_capability")
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


class AgentSkillTaskInput(SkillTaskInput):
    max_calls: int = Field(default=20, ge=1, le=50, strict=True)
    max_active_seconds: int = Field(default=900, ge=30, le=1800, strict=True)


def is_agent_skill(unit, agent_id, skill_id, version) -> bool:
    from bees_core.skill_context import agent_supported
    from bees_core.skills import SkillsRepository

    revision = SkillsRepository(unit.agents._context.connection, UUID(str(agent_id))).revision(
        skill_id, version
    )
    return agent_supported(revision.definition)


class AgentSkillTaskService(AgentTaskService):
    """Skill com ferramentas do registro executada pelo ciclo do agente."""

    def create(self, agent_id, value):
        value = AgentSkillTaskInput.model_validate(
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
        command = AgentTaskInput(
            client_request_id=value.client_request_id,
            title=routine_binding["definition"]["name"] if routine_binding else definition["name"],
            objective="Execute o procedimento fixado e produza uma nova entrega para as "
            "entradas desta tarefa; confira os critérios de conclusão.",
            expected_result=definition["output"],
            max_calls=max(value.max_calls, 1),
            max_active_seconds=max(value.max_active_seconds, 30),
        )
        metadata = {
            "skill_execution": binding,
            "skill_execution_hash": digest(binding),
            "skill_request_hash": request_digest(value),
        }
        if routine_binding:
            destination = routine_binding["definition"]["destination"]["conversation_id"]
            metadata |= {
                "routine_execution": routine_binding,
                "routine_execution_hash": digest(routine_binding),
                # Resultado da rotina aparece na conversa de destino.
                "agent_chat": {"conversation_id": destination},
            }
        return self.create_bound_in_unit(
            unit,
            agent_id,
            command,
            metadata=metadata,
            routine_id=UUID(routine_binding["routine_id"]) if routine_binding else None,
        )
