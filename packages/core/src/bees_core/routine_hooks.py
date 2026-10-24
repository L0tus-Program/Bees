"""Gatilho externo (webhook) de rotina.

Fronteiras:
- O segredo ``bh_`` (256 bits) aparece uma única vez, na criação; o banco guarda o hash.
- Cada disparo usa o caminho de execução manual (mesma validação de revisão, parada
  global, ocorrência pendente e vínculo da skill), com ator ``webhook``.
- O corpo do pedido externo é ignorado: dados de terceiros nunca viram entrada da
  rotina nem instrução. Intervalo mínimo entre disparos limita abuso e consumo.
"""

import hashlib
import json
import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4, uuid5

from pydantic import BaseModel, ConfigDict, Field

from bees_core.models import utc_now
from bees_core.routine_manual import ManualRoutineInput, ManualRoutineService
from bees_core.storage.database import Database
from bees_core.storage.store import NotFoundError, RevisionConflict, StateStore

PREFIX = "bh_"
COLUMNS = (
    "id,created_at,updated_at,revision,agent_id,routine_id,token_hash,status,"
    "min_interval_seconds,last_triggered_at,last_result,trigger_count"
)


class HookError(RuntimeError):
    """Códigos: hook_not_found, hook_rate_limited, hook_rejected."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class CreateHook(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    min_interval_seconds: int = Field(default=60, ge=10, le=86400)


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="microseconds")


def _row(values) -> dict:
    return dict(zip(COLUMNS.split(","), values, strict=True))


def view(row: dict | None) -> dict | None:
    if row is None:
        return None
    return {
        key: row[key]
        for key in (
            "id",
            "routine_id",
            "status",
            "min_interval_seconds",
            "last_triggered_at",
            "last_result",
            "trigger_count",
            "revision",
            "created_at",
        )
    }


class RoutineHookService:
    def __init__(self, database: Database, *, clock=utc_now) -> None:
        self.database = database
        self.store = StateStore(database)
        self.clock = clock

    def active(self, agent_id, routine_id) -> dict | None:
        with self.database.transaction(write=False) as connection:
            values = connection.execute(
                f"SELECT {COLUMNS} FROM routine_hooks WHERE agent_id=? AND routine_id=? "
                "AND status='active'",
                (str(agent_id), str(routine_id)),
            ).fetchone()
        return _row(values) if values else None

    def create(self, agent_id, routine_id, value: CreateHook) -> tuple[dict, str]:
        """Cria (ou gira) o gatilho; o anterior deixa de funcionar no mesmo commit."""
        token = PREFIX + secrets.token_urlsafe(32)
        now = _iso(self.clock())
        hook_id = uuid4()
        with self.database.transaction() as connection:
            owner = connection.execute(
                "SELECT agent_id FROM routines WHERE id=?", (str(routine_id),)
            ).fetchone()
            if owner is None or owner[0] != str(agent_id):
                raise NotFoundError("Rotina não encontrada.")
            connection.execute(
                "UPDATE routine_hooks SET status='revoked',updated_at=?,revision=revision+1 "
                "WHERE routine_id=? AND status='active'",
                (now, str(routine_id)),
            )
            connection.execute(
                f"INSERT INTO routine_hooks({COLUMNS}) "
                "VALUES(?,?,?,1,?,?,?,'active',?,NULL,NULL,0)",
                (
                    str(hook_id),
                    now,
                    now,
                    str(agent_id),
                    str(routine_id),
                    _hash(token),
                    value.min_interval_seconds,
                ),
            )
            row = _row(
                connection.execute(
                    f"SELECT {COLUMNS} FROM routine_hooks WHERE id=?", (str(hook_id),)
                ).fetchone()
            )
        return row, token

    def revoke(self, agent_id, routine_id, expected_revision: int) -> dict:
        with self.database.transaction() as connection:
            values = connection.execute(
                f"SELECT {COLUMNS} FROM routine_hooks WHERE agent_id=? AND routine_id=? "
                "AND status='active'",
                (str(agent_id), str(routine_id)),
            ).fetchone()
            if values is None:
                raise NotFoundError("Gatilho não encontrado.")
            row = _row(values)
            if row["revision"] != expected_revision:
                raise RevisionConflict("Gatilho alterado; atualize a tela.")
            connection.execute(
                "UPDATE routine_hooks SET status='revoked',updated_at=?,revision=revision+1 "
                "WHERE id=?",
                (_iso(self.clock()), row["id"]),
            )
        return row | {"status": "revoked", "revision": row["revision"] + 1}

    def _reserve(self, token: str) -> tuple[dict, datetime]:
        """Confere o segredo e reserva o intervalo antes de qualquer execução."""
        if not token.startswith(PREFIX) or len(token) > 100:
            raise HookError("hook_not_found")
        now = self.clock()
        with self.database.transaction() as connection:
            values = connection.execute(
                f"SELECT {COLUMNS} FROM routine_hooks WHERE token_hash=? AND status='active'",
                (_hash(token),),
            ).fetchone()
            if values is None:
                raise HookError("hook_not_found")
            row = _row(values)
            last = row["last_triggered_at"]
            if last and now < datetime.fromisoformat(last) + timedelta(
                seconds=row["min_interval_seconds"]
            ):
                raise HookError("hook_rate_limited")
            connection.execute(
                "UPDATE routine_hooks SET last_triggered_at=?,trigger_count=trigger_count+1 "
                "WHERE id=?",
                (_iso(now), row["id"]),
            )
        return row, now

    def _result(self, hook_id, result: str) -> None:
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE routine_hooks SET last_result=? WHERE id=?", (result, hook_id)
            )

    def _prior(self, token: str, key: str) -> dict | None:
        """Reenvio com a mesma chave devolve o recibo anterior sem nova execução."""
        with self.database.transaction(write=False) as connection:
            values = connection.execute(
                f"SELECT {COLUMNS} FROM routine_hooks WHERE token_hash=? AND status='active'",
                (_hash(token),),
            ).fetchone()
            if values is None:
                raise HookError("hook_not_found")
            row = _row(values)
            request_id = uuid5(UUID(row["id"]), f"bees.routine-hook/key/{key}")
            prior = connection.execute(
                "SELECT response_json FROM routine_commands WHERE client_request_id=?",
                (str(request_id),),
            ).fetchone()
        if prior is None:
            return None
        occurrence = json.loads(prior[0])["occurrence"]
        return {
            "accepted": True,
            "replayed": True,
            "occurrence_id": occurrence["id"],
            "task_id": occurrence.get("task_id"),
        }

    def trigger(self, token: str, idempotency_key: str | None = None) -> dict:
        if idempotency_key is not None:
            if not 1 <= len(idempotency_key) <= 200 or any(
                ord(c) < 33 or ord(c) > 126 for c in idempotency_key
            ):
                raise HookError("hook_rejected")
            prior = self._prior(token, idempotency_key)
            if prior is not None:
                return prior
        row, now = self._reserve(token)
        with self.store.transaction(write=False) as unit:
            routine = unit.agents._context.connection.execute(
                "SELECT revision FROM routines WHERE id=? AND agent_id=?",
                (row["routine_id"], row["agent_id"]),
            ).fetchone()
        if routine is None:
            self._result(row["id"], "routine_missing")
            raise HookError("hook_rejected")
        request_id = uuid5(
            UUID(row["id"]),
            f"bees.routine-hook/key/{idempotency_key}"
            if idempotency_key
            else f"bees.routine-hook/{_iso(now)}",
        )
        try:
            receipt = ManualRoutineService(self.database, clock=self.clock).execute(
                row["agent_id"],
                row["routine_id"],
                ManualRoutineInput(client_request_id=request_id, expected_revision=routine[0]),
                actor="webhook",
                source="routine_webhook",
            )
        except Exception as error:
            code = getattr(error, "code", None) or type(error).__name__
            self._result(row["id"], str(code)[:64])
            raise HookError("hook_rejected") from None
        self._result(row["id"], "accepted")
        return {
            "accepted": True,
            "occurrence_id": str(receipt.occurrence.id),
            "task_id": str(receipt.occurrence.task_id) if receipt.occurrence.task_id else None,
        }
