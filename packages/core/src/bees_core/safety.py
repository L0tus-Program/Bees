"""Parada global durável (BEES-022.2).

Parar impede novas gerações, ferramentas e efeitos de provisionamento em todos os
caminhos; não cancela no provedor o que já foi despachado. Cada parada aumenta a
geração: um fluxo iniciado antes dela não segue nem depois de uma retomada. Retomar
não despausa resultado desconhecido nem reaproveita aprovação consumida.
"""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from bees_core.models import utc_now
from bees_core.providers.errors import ProviderError
from bees_core.storage.database import Database
from bees_core.storage.store import SafetyState, StateStore


class SafetyCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    client_request_id: UUID
    kind: Literal["stop", "resume"]
    expected_revision: int = Field(ge=1)
    reason: str | None = Field(default=None, min_length=1, max_length=500)


def blocked(state: SafetyState, generation: int | None = None) -> bool:
    return state.status != "running" or (generation is not None and state.generation != generation)


def ensure_running(uow, generation: int | None = None) -> SafetyState:
    """Guarda das gerações: parada ou geração diferente recusam antes de qualquer efeito."""
    state = uow.safety.state()
    if blocked(state, generation):
        raise ProviderError("global_stop")
    return state


def view(state: SafetyState, in_flight: dict[str, int]) -> dict:
    return {
        "status": state.status,
        "generation": state.generation,
        "revision": state.revision,
        "changed_at": state.changed_at.isoformat(),
        "reason": state.reason,
        "in_flight": in_flight,
    }


class SafetyService:
    def __init__(self, database: Database) -> None:
        self.store = StateStore(database)

    def current(self, *, include_snapshot=False) -> dict:
        from bees_core.activity import observe

        with self.store.transaction(write=False) as uow:
            snapshot = observe(uow.tasks._context.connection)
            result = view(uow.safety.state(), uow.safety.in_flight())
            if include_snapshot:
                result["snapshot"] = snapshot.model_dump(mode="json")
            return result

    def command(self, value: SafetyCommand, *, actor: str = "user", include_snapshot=False) -> dict:
        from bees_core.activity import observe

        value = SafetyCommand.model_validate(value.model_dump())
        with self.store.transaction(actor=actor, source="safety") as uow:
            state, _ = uow.safety.command(
                kind=value.kind,
                client_request_id=value.client_request_id,
                expected_revision=value.expected_revision,
                reason=value.reason,
                now=utc_now(),
            )
            connection = uow.safety._context.connection
            if (
                state.status != "running"
                and connection.execute(
                    "SELECT 1 FROM sqlite_schema WHERE type='table' AND name='executor_commands'"
                ).fetchone()
            ):
                # Comandos ainda não entregues a executores não saem mais.
                connection.execute(
                    "UPDATE executor_commands SET status='expired',error_code='global_stop',"
                    "updated_at=?,finished_at=? WHERE status='queued'",
                    (state.changed_at.isoformat(), state.changed_at.isoformat()),
                )
            result = view(state, uow.safety.in_flight())
            if include_snapshot:
                result["snapshot"] = observe(uow.tasks._context.connection).model_dump(mode="json")
            return result
