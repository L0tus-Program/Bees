"""Consumo canônico das gerações e limite de tokens por abelha (BEES-022.1).

A reserva é gravada no mesmo commit da autorização imediatamente antes da rede; a
liquidação usa o uso informado pelo provedor. Ausência de uso nunca vira zero: o
registro conta a reserva. O limite local bloqueia a próxima geração antes da rede; não
corta uma resposta em andamento nem garante teto na fatura do fornecedor.
"""

import json
import math
from datetime import datetime, timedelta
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

from bees_core.models import BudgetLimit, UsageEntry, utc_now
from bees_core.providers.contracts import ChatRequest, Usage
from bees_core.providers.errors import ProviderError
from bees_core.storage.database import Database
from bees_core.storage.store import NotFoundError, RevisionConflict, StateStore

# Estimativa conservadora e declarada: textos comuns ficam perto de 4 caracteres por
# token; dividir por 3 superestima a entrada. Não é contagem do fornecedor.
ESTIMATE_METHOD = "chars_div_3_v1"
DEFAULT_OUTPUT_ALLOWANCE = 4096


class LimitInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    token_limit: int = Field(ge=1, le=1_000_000_000)
    window_seconds: int = Field(default=86400, ge=3600, le=2_592_000)
    output_allowance: int = Field(default=DEFAULT_OUTPUT_ALLOWANCE, ge=1, le=1_000_000)
    status: str = Field(default="active", pattern="^(active|disabled)$")


def estimate_input(request: ChatRequest) -> int:
    payload = json.dumps(request.model_dump(mode="json"), ensure_ascii=False, separators=(",", ":"))
    return math.ceil(len(payload) / 3)


def releases(error: BaseException) -> bool:
    """Somente recusa HTTP explícita do provedor prova que nenhuma geração ocorreu."""
    return isinstance(error, ProviderError) and (
        error.code == "redirect_refused"
        or (error.upstream_status is not None and 400 <= error.upstream_status < 500)
    )


def window_total(uow, agent_id: UUID, *, window_seconds: int, now: datetime) -> int:
    entries = uow.usage_entries.since(agent_id, now - timedelta(seconds=window_seconds))
    return sum(entry.counted_tokens() for entry in entries)


def reserve(
    uow,
    prepared,
    *,
    source: str,
    model_call_id: UUID | None = None,
    now: datetime | None = None,
) -> UsageEntry:
    """Dentro da transação de autorização; o commit precede a rede."""
    now = now or utc_now()
    limit = uow.budget_limits.for_agent(prepared.agent_id)
    allowance = limit.output_allowance if limit is not None else DEFAULT_OUTPUT_ALLOWANCE
    reserved = estimate_input(prepared.request) + allowance
    if limit is not None and limit.status == "active":
        used = window_total(uow, prepared.agent_id, window_seconds=limit.window_seconds, now=now)
        if used + reserved > limit.token_limit:
            raise ProviderError("budget_exhausted")
    return uow.usage_entries.create(
        UsageEntry(
            id=uuid4(),
            agent_id=prepared.agent_id,
            source=source,
            conversation_id=prepared.conversation_id,
            model_call_id=model_call_id,
            provider_kind=prepared.config.kind,
            model=prepared.config.model,
            reserved_tokens=reserved,
            estimate_method=ESTIMATE_METHOD,
            created_at=now,
            updated_at=now,
        )
    )


def settle(
    uow,
    entry_id: UUID,
    *,
    usage: Usage | None = None,
    released: bool = False,
    now: datetime | None = None,
) -> UsageEntry | None:
    """Liquidação final e idempotente: uma segunda liquidação não altera a primeira."""
    entry = uow.usage_entries.get(entry_id)
    if entry is None or entry.status != "reserved":
        return entry
    update: dict = {"settled_at": now or utc_now()}
    if usage is not None:
        update |= {
            "status": "confirmed",
            "usage_kind": usage.kind,
            "input_tokens": usage.input_tokens,
            "output_tokens": usage.output_tokens,
            "total_tokens": usage.total_tokens,
        }
    else:
        update["status"] = "released" if released else "unknown"
    return uow.usage_entries.update(entry.model_copy(update=update), entry.revision)


class BudgetService:
    def __init__(self, database: Database) -> None:
        self.store = StateStore(database)

    @staticmethod
    def _agent(uow, agent_id: UUID | str) -> UUID:
        agent = uow.agents.get(agent_id)
        if agent is None:
            raise NotFoundError("Agente não encontrado.")
        return agent.id

    def summary(self, agent_id: UUID | str, *, now: datetime | None = None) -> dict:
        now = now or utc_now()
        with self.store.transaction(write=False) as uow:
            agent = self._agent(uow, agent_id)
            limit = uow.budget_limits.for_agent(agent)
            window = limit.window_seconds if limit is not None else 86400
            entries = uow.usage_entries.since(agent, now - timedelta(seconds=window))
        counted = sum(entry.counted_tokens() for entry in entries)
        reported = sum(
            entry.counted_tokens()
            for entry in entries
            if entry.status == "confirmed" and entry.usage_kind == "reported"
        )
        return {
            "limit": limit,
            "window_seconds": window,
            "counted_tokens": counted,
            "reported_tokens": reported,
            "open_reservations": sum(1 for entry in entries if entry.status == "reserved"),
            "unknown_entries": sum(
                1
                for entry in entries
                if entry.status == "unknown"
                or (entry.status == "confirmed" and entry.usage_kind == "unknown")
            ),
            "entries": len(entries),
            "remaining_tokens": (
                max(limit.token_limit - counted, 0)
                if limit is not None and limit.status == "active"
                else None
            ),
        }

    def set_limit(
        self,
        agent_id: UUID | str,
        value: LimitInput,
        *,
        expected_revision: int | None,
    ) -> BudgetLimit:
        """CAS humano: criar exige revisão ausente; editar exige a revisão atual."""
        value = LimitInput.model_validate(value.model_dump())
        with self.store.transaction(source="budgets") as uow:
            agent = self._agent(uow, agent_id)
            current = uow.budget_limits.for_agent(agent)
            if current is None:
                if expected_revision is not None:
                    raise RevisionConflict("O limite mudou; releia antes de editar.")
                return uow.budget_limits.create(BudgetLimit(agent_id=agent, **value.model_dump()))
            if expected_revision is None or current.revision != expected_revision:
                raise RevisionConflict("O limite mudou; releia antes de editar.")
            return uow.budget_limits.update(
                current.model_copy(update=value.model_dump()), expected_revision
            )
