"""Consumo canônico das gerações e limite de tokens por abelha (BEES-022.1).

A reserva é gravada no mesmo commit da autorização imediatamente antes da rede; a
liquidação usa o uso informado pelo provedor. Ausência de uso, uso zerado ou absurdo
nunca vira zero: o registro conta a reserva. O limite local bloqueia a próxima geração
antes da rede; não corta uma resposta em andamento nem garante teto na fatura.
"""

import json
import math
from datetime import datetime, timedelta
from typing import Self
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from bees_core.models import BudgetLimit, UsageEntry, utc_now
from bees_core.providers.contracts import ChatRequest, Usage
from bees_core.providers.errors import ProviderError
from bees_core.storage.database import Database
from bees_core.storage.store import NotFoundError, RevisionConflict, StateStore

# Estimativa declarada: textos latinos comuns ficam perto de 4 caracteres por token, e
# dividir por 3 os superestima. Outras escritas (como CJK) podem ser subestimadas. Não é
# contagem do fornecedor.
ESTIMATE_METHOD = "chars_div_3_v1"
DEFAULT_OUTPUT_ALLOWANCE = 4096
# Acima disso um contador informado não é plausível nem cabe com folga no SQLite.
MAX_TOKEN_COUNT = 10**12
# Respostas HTTP que recusam o pedido sem gerá-lo. 408/409/425/499 e 5xx podem ocorrer
# depois de processamento em intermediários e permanecem incertos.
RELEASING_STATUS = frozenset({400, 401, 403, 404, 413, 422, 429})


class LimitInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    token_limit: int = Field(ge=1, le=1_000_000_000)
    window_seconds: int = Field(default=86400, ge=3600, le=2_592_000)
    output_allowance: int = Field(default=DEFAULT_OUTPUT_ALLOWANCE, ge=1, le=1_000_000)
    status: str = Field(default="active", pattern="^(active|disabled)$")

    @model_validator(mode="after")
    def fits_one_generation(self) -> Self:
        # Um limite menor que a folga de saída bloquearia a abelha para sempre.
        if self.token_limit <= self.output_allowance:
            raise ValueError("O limite precisa comportar ao menos uma geração.")
        return self


def estimate_input(request: ChatRequest) -> int:
    payload = json.dumps(request.model_dump(mode="json"), ensure_ascii=False, separators=(",", ":"))
    return math.ceil(len(payload) / 3)


def releases(error: BaseException) -> bool:
    """Liberar só quando há prova de que nenhuma geração ocorreu."""
    if not isinstance(error, ProviderError):
        return False
    return (
        error.undelivered
        or error.code == "redirect_refused"
        or error.upstream_status in RELEASING_STATUS
    )


def trusted_usage(usage: Usage | None) -> Usage | None:
    """Contadores zerados ou fora de escala viram desconhecidos e contam a reserva."""
    if usage is None or usage.kind == "unknown":
        return usage
    counts = (usage.input_tokens, usage.output_tokens, usage.total_tokens)
    if any(value is not None and value > MAX_TOKEN_COUNT for value in counts):
        return Usage()
    total = usage.total_tokens
    if total is None and usage.input_tokens is not None and usage.output_tokens is not None:
        total = usage.input_tokens + usage.output_tokens
    if total == 0:
        return Usage()
    return usage


def _allowance(limit: BudgetLimit | None) -> int:
    return limit.output_allowance if limit is not None else DEFAULT_OUTPUT_ALLOWANCE


def _exceeds(uow, prepared, limit: BudgetLimit | None, reserved: int, now: datetime) -> bool:
    if limit is None or limit.status != "active":
        return False
    window = uow.usage_entries.window(
        prepared.agent_id, now - timedelta(seconds=limit.window_seconds)
    )
    return window["counted_tokens"] + reserved > limit.token_limit


def fits(uow, prepared, *, now: datetime | None = None) -> bool:
    """Pré-checagem sem reserva, para recusar antes de gravar entrada ou pedir decisão."""
    limit = uow.budget_limits.for_agent(prepared.agent_id)
    reserved = estimate_input(prepared.request) + _allowance(limit)
    return not _exceeds(uow, prepared, limit, reserved, now or utc_now())


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
    reserved = estimate_input(prepared.request) + _allowance(limit)
    if _exceeds(uow, prepared, limit, reserved, now):
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
    usage = trusted_usage(usage)
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


def settle_safely(uow, entry_id: UUID, **values) -> None:
    """Falha de liquidação não pode apagar resposta paga nem o erro original.

    O registro continua ``reserved`` e segue contando a reserva inteira.
    """
    try:
        settle(uow, entry_id, **values)
    except Exception:
        pass


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
            totals = uow.usage_entries.window(agent, now - timedelta(seconds=window))
        return totals | {
            "limit": limit,
            "window_seconds": window,
            "remaining_tokens": (
                max(limit.token_limit - totals["counted_tokens"], 0)
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
