"""Explicit reactivation under global stop; never resumes or authorizes work."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from bees_core.providers.service import ProviderService
from bees_core.storage.database import Database
from bees_core.storage.store import NotFoundError, RevisionConflict, StateStore


class ActivationError(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class ReactivationInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, hide_input_in_errors=True)

    expected_revision: int = Field(ge=1)
    expected_safety_revision: int = Field(ge=1)
    consent: Literal["reactivate_paused_agent"]


class ReactivationReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    agent_id: UUID
    revision: int = Field(ge=1, strict=True)
    status: Literal["active"] = "active"
    safety_revision: int = Field(ge=1, strict=True)
    global_status: Literal["stopped"] = "stopped"
    provider_checked: Literal[True] = True


class AgentActivationService:
    def __init__(self, database: Database, providers: ProviderService | None = None):
        self.store = StateStore(database)
        self.providers = ProviderService(database) if providers is None else providers

    @staticmethod
    def _checked(unit, agent_id: UUID, value: ReactivationInput):
        agent = unit.agents.get(agent_id)
        if agent is None:
            raise NotFoundError("Agent not found")
        if agent.revision != value.expected_revision:
            raise RevisionConflict("Agent changed")
        if agent.status != "paused":
            raise ActivationError("activation_not_paused")
        if not agent.provider_config:
            raise ActivationError("activation_configuration_required")
        safety = unit.safety.state()
        if safety.revision != value.expected_safety_revision:
            raise RevisionConflict("Global stop changed")
        if safety.status != "stopped":
            raise ActivationError("activation_stop_required")
        return agent, safety

    async def reactivate(
        self, agent_id: UUID | str, value: ReactivationInput
    ) -> ReactivationReceipt:
        agent_id = UUID(str(agent_id))
        value = ReactivationInput.model_validate(value.model_dump())
        with self.store.transaction(write=False) as unit:
            self._checked(unit, agent_id, value)
        # diagnose checks availability only, never generates or changes configuration.
        await self.providers.diagnose(agent_id)
        with self.store.transaction(source="agent_reactivation") as unit:
            agent, safety = self._checked(unit, agent_id, value)
            updated = unit.agents.update(
                agent.model_copy(update={"status": "active"}), agent.revision
            )
            return ReactivationReceipt(
                agent_id=updated.id, revision=updated.revision, safety_revision=safety.revision
            )
