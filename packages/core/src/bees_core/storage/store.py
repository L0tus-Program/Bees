"""Repositórios tipados, revisões otimistas e ledger transacional sem conteúdo."""

import hashlib
import json
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import wraps
from typing import Literal
from uuid import UUID, uuid4

import apsw
from pydantic import JsonValue, TypeAdapter

from bees_core.models import (
    Action,
    ActionStatus,
    Agent,
    Approval,
    Artifact,
    Conversation,
    DomainEvent,
    EntityType,
    Environment,
    ExecutionClaim,
    HostJob,
    LeaseToken,
    Memory,
    Message,
    ModelCall,
    PluginInstallation,
    Policy,
    Record,
    Routine,
    Run,
    Task,
    TaskCommand,
    TaskStatus,
    ToolGrant,
    utc_now,
)
from bees_core.storage.database import Database


class StoreError(RuntimeError):
    """Erro previsível dos repositórios, sem valores ou SQL em mensagens."""


class IntegrityError(StoreError):
    """Vínculo, identificador ou campo canônico incompatível."""


class NotFoundError(StoreError):
    """Registro não encontrado."""


class RevisionConflict(StoreError):
    """Outro escritor já alterou a revisão observada."""


class InvalidTransition(StoreError):
    """Transição pode repetir efeito ou destruir evidência de resultado."""


class TransactionClosed(StoreError):
    """Repositórios só podem ser utilizados dentro de sua UnitOfWork."""


class ReadOnlyTransaction(StoreError):
    """Tentativa de escrita numa UnitOfWork de leitura."""


JSON_COLUMNS = {
    "metadata": "metadata_json",
    "provider_config": "model_config_json",
    "checkpoint": "checkpoint_json",
    "parameters": "parameters_json",
    "result": "result_json",
    "scope": "scope_json",
    "schedule": "schedule_json",
    "request": "request_json",
    "response": "response_json",
    "snapshot": "snapshot_json",
    "payload": "payload_json",
    "manifest": "manifest_json",
}


def _json_columns(model: type[Record]) -> dict[str, str]:
    # scope é um enum textual em Memory e um documento em Policy/Approval.
    return {
        name: column
        for name, column in JSON_COLUMNS.items()
        if name != "scope" or model is not Memory
    }


@dataclass(frozen=True)
class _Spec[T: Record]:
    table: str
    entity: EntityType
    model: type[T]
    immutable: tuple[str, ...] = ()

    @property
    def columns(self) -> tuple[str, ...]:
        mappings = _json_columns(self.model)
        return tuple(mappings.get(name, name) for name in self.model.model_fields)


@dataclass
class _Context:
    connection: apsw.Connection
    write: bool
    actor: str
    source: str
    correlation_id: str | None
    active: bool = True

    def check(self, write: bool = False) -> None:
        if not self.active:
            raise TransactionClosed("UnitOfWork encerrada.")
        if write and not self.write:
            raise ReadOnlyTransaction("UnitOfWork de leitura não permite alterações.")


def _json(value: JsonValue) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def _timestamp(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Instante precisa ter timezone.")
    return value.astimezone(UTC).isoformat(timespec="microseconds")


def _encode(record: Record) -> tuple:
    result = []
    json_columns = _json_columns(type(record))
    for name in type(record).model_fields:
        value = getattr(record, name)
        if name in json_columns:
            value = _json(value) if value is not None else None
        elif isinstance(value, UUID):
            value = str(value)
        elif isinstance(value, datetime):
            value = _timestamp(value)
        result.append(value)
    return tuple(result)


def _pagination(limit: int, offset: int = 0) -> None:
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 1000:
        raise ValueError("limit deve ser inteiro entre 1 e 1000.")
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
        raise ValueError("offset deve ser inteiro não negativo.")


class _Repository[T: Record]:
    """SQL interno usa apenas especificações estáticas; valores são parâmetros."""

    def __init__(self, context: _Context, spec: _Spec[T]) -> None:
        self._context = context
        self._spec = spec

    def _decode(self, row: tuple) -> T:
        values = dict(zip(self._spec.model.model_fields, row, strict=True))
        if self._spec.model in (PluginInstallation, ToolGrant):
            values["enabled"] = bool(values["enabled"])
        for name in _json_columns(self._spec.model):
            if name in values and values[name] is not None:
                values[name] = json.loads(values[name])
        return self._spec.model.model_validate(values)

    def get(self, id: UUID | str) -> T | None:
        self._context.check()
        row = self._context.connection.execute(
            f"SELECT {','.join(self._spec.columns)} FROM {self._spec.table} WHERE id=?",
            (str(UUID(str(id))),),
        ).fetchone()
        return self._decode(row) if row is not None else None

    def _list(
        self, filters: dict, limit: int, offset: int, *, newest_first: bool = False
    ) -> list[T]:
        self._context.check()
        _pagination(limit, offset)
        filters = {name: value for name, value in filters.items() if value is not None}
        if not filters.keys() <= self._spec.model.model_fields.keys():
            raise ValueError("Filtro desconhecido.")
        filters = {
            name: TypeAdapter(self._spec.model.model_fields[name].annotation).validate_python(value)
            for name, value in filters.items()
        }
        clauses = " AND ".join(f"{name}=?" for name in filters)
        values = tuple(
            str(value) if isinstance(value, UUID) else value for value in filters.values()
        )
        where = f" WHERE {clauses}" if clauses else ""
        rows = self._context.connection.execute(
            f"SELECT {','.join(self._spec.columns)} FROM {self._spec.table}{where} "
            + (
                "ORDER BY created_at DESC,id DESC LIMIT ? OFFSET ?"
                if newest_first
                else "ORDER BY created_at,id LIMIT ? OFFSET ?"
            ),
            (*values, limit, offset),
        )
        return [self._decode(row) for row in rows]

    @contextmanager
    def _mutation(self) -> Iterator[None]:
        self._context.check(write=True)
        connection = self._context.connection
        connection.execute("SAVEPOINT bees_record_mutation")
        try:
            yield
            connection.execute("RELEASE bees_record_mutation")
        except BaseException as error:
            connection.execute("ROLLBACK TO bees_record_mutation")
            connection.execute("RELEASE bees_record_mutation")
            if isinstance(error, apsw.ConstraintError):
                raise IntegrityError("Registro viola integridade do estado canônico.") from error
            raise

    def _event(self, record: T, event_type: str, previous: T | None = None) -> None:
        payload: dict[str, JsonValue] = {"revision": record.revision}
        if hasattr(record, "status"):
            payload["status"] = record.status
        if isinstance(record, PluginInstallation):
            payload.update({"manifest_hash": record.manifest_hash, "enabled": record.enabled})
        if isinstance(record, ToolGrant):
            payload.update(
                {
                    "agent_id": str(record.agent_id),
                    "plugin_id": str(record.plugin_id),
                    "tool_name": record.tool_name,
                    "enabled": record.enabled,
                }
            )
        if isinstance(record, Environment):
            payload.update(
                {
                    "agent_id": str(record.agent_id),
                    "template_id": record.template_id,
                    "cpu_count": record.cpu_count,
                    "memory_mib": record.memory_mib,
                    "disk_gib": record.disk_gib,
                    "reason_code": record.reason_code,
                }
            )
        if isinstance(record, HostJob):
            payload.update(
                {
                    "environment_id": str(record.environment_id),
                    "correlation_id": str(record.correlation_id),
                    "owner_id": str(record.owner_id) if record.owner_id else None,
                    "recovery_evidence": str(record.recovery_evidence)
                    if record.recovery_evidence
                    else None,
                }
            )
        if isinstance(previous, (PluginInstallation, ToolGrant)):
            payload["previous_enabled"] = previous.enabled
        if previous is not None and hasattr(previous, "status"):
            payload["previous_status"] = previous.status
        self._context.connection.execute(
            "INSERT INTO domain_events(id,created_at,entity_type,entity_id,event_type,"
            "payload_json,actor,source,correlation_id) VALUES(?,?,?,?,?,?,?,?,?)",
            (
                str(uuid4()),
                _timestamp(utc_now()),
                self._spec.entity,
                str(record.id),
                event_type,
                _json(payload),
                self._context.actor,
                self._context.source,
                self._context.correlation_id,
            ),
        )

    def _validate_links(self, record: T) -> None:
        connection = self._context.connection
        if isinstance(record, ToolGrant):
            row = connection.execute(
                "SELECT manifest_json FROM plugins WHERE id=?", (str(record.plugin_id),)
            ).fetchone()
            if row is None or record.tool_name not in {
                tool.get("tool_name")
                for tool in json.loads(row[0]).get("tools", [])
                if isinstance(tool, dict)
            }:
                raise IntegrityError("Concessão exige ferramenta declarada pela extensão.")
        if isinstance(record, Task):
            for table, id in (
                ("conversations", record.conversation_id),
                ("routines", record.routine_id),
            ):
                if id is not None:
                    row = connection.execute(
                        f"SELECT agent_id FROM {table} WHERE id=?", (str(id),)
                    ).fetchone()
                    if row is None or row[0] != str(record.agent_id):
                        raise IntegrityError("Tarefa e vínculo precisam pertencer ao mesmo agente.")
        elif isinstance(record, Artifact) and record.run_id is not None:
            row = connection.execute(
                "SELECT task_id FROM runs WHERE id=?", (str(record.run_id),)
            ).fetchone()
            if row is None or row[0] != str(record.task_id):
                raise IntegrityError("Artefato e execução precisam pertencer à mesma tarefa.")
        elif isinstance(record, ModelCall) and record.output_message_id is not None:
            row = connection.execute(
                "SELECT t.conversation_id,m.conversation_id,m.role FROM runs r "
                "JOIN tasks t ON t.id=r.task_id JOIN messages m ON m.id=? WHERE r.id=?",
                (str(record.output_message_id), str(record.run_id)),
            ).fetchone()
            if row is None or row[0] != row[1] or row[2] != "assistant":
                raise IntegrityError("Resultado precisa pertencer à conversa da tarefa.")
        elif isinstance(record, Memory) and record.task_id is not None:
            row = connection.execute(
                "SELECT agent_id FROM tasks WHERE id=?", (str(record.task_id),)
            ).fetchone()
            if row is None or row[0] != str(record.agent_id):
                raise IntegrityError("Memória e tarefa precisam pertencer ao mesmo agente.")
        elif isinstance(record, Approval) and record.policy_id is not None:
            row = connection.execute(
                "SELECT p.agent_id,t.agent_id FROM policies p JOIN actions a ON a.id=? "
                "JOIN runs r ON r.id=a.run_id JOIN tasks t ON t.id=r.task_id WHERE p.id=?",
                (str(record.action_id), str(record.policy_id)),
            ).fetchone()
            if row is None or (row[0] is not None and row[0] != row[1]):
                raise IntegrityError(
                    "Aprovação e política precisam ter escopo de agente compatível."
                )

    def _validated(self, record: T) -> T:
        if not isinstance(record, self._spec.model):
            raise TypeError(f"Repositório exige {self._spec.model.__name__}.")
        return self._spec.model.model_validate(record.model_dump())

    def create(self, record: T) -> T:
        validated = self._validated(record)
        if validated.revision != 1:
            raise IntegrityError("Novo registro precisa ter revisão 1.")
        with self._mutation():
            self._validate_links(validated)
            self._context.connection.execute(
                f"INSERT INTO {self._spec.table}({','.join(self._spec.columns)}) "
                f"VALUES({','.join('?' for _ in self._spec.columns)})",
                _encode(validated),
            )
            self._event(validated, "created")
        return validated

    def update(self, record: T, expected_revision: int) -> T:
        return self._update(record, expected_revision)

    def _update(self, record: T, expected_revision: int, *, reconciled: bool = False) -> T:
        validated = self._validated(record)
        if isinstance(expected_revision, bool) or not isinstance(expected_revision, int):
            raise ValueError("expected_revision precisa ser inteiro.")
        with self._mutation():
            previous = self.get(validated.id)
            if previous is None:
                raise NotFoundError("Registro não encontrado para atualização.")
            if previous.revision != expected_revision or validated.revision != expected_revision:
                raise RevisionConflict("A revisão mudou; releia o registro antes de editar.")
            if isinstance(previous, Message):
                raise IntegrityError("Histórico de mensagens é append-only neste ciclo.")
            for name in ("created_at", *self._spec.immutable):
                if getattr(previous, name) != getattr(validated, name):
                    raise IntegrityError("Vínculos e identidade canônica são imutáveis.")
            if isinstance(previous, Approval) and previous.policy_id != validated.policy_id:
                # Uma decisão pode criar seu vínculo pela primeira vez; uma
                # autorização já vinculada nunca é transferida para outra regra.
                if (
                    previous.policy_id is not None
                    or previous.status != "pending"
                    or validated.status != "approved"
                    or validated.decision != "allow_rule"
                    or validated.actor != "user"
                ):
                    raise IntegrityError("Vínculo de aprovação não pode ser substituído.")
            if isinstance(previous, Action) and isinstance(validated, Action):
                _validate_action_update(previous, validated, reconciled=reconciled)
            if isinstance(previous, Environment):
                transitions = {
                    "awaiting_host": {"cancelled", "provisioning"},
                    "provisioning": {"outcome_unknown"},
                    "outcome_unknown": set(),
                    "cancelled": set(),
                }
                if validated.status not in transitions[previous.status]:
                    raise InvalidTransition("Ambiente terminal ou transição sem evidência.")
            if isinstance(previous, HostJob):
                transitions = {
                    "awaiting_host": {"cancelled", "dispatch_started"},
                    "dispatch_started": {"outcome_unknown"},
                    "outcome_unknown": set(),
                    "cancelled": set(),
                }
                if validated.status not in transitions[previous.status]:
                    raise InvalidTransition("Job terminal não pode repetir provisionamento.")
                if previous.status == "dispatch_started" and (
                    previous.owner_id != validated.owner_id
                    or previous.dispatched_at != validated.dispatched_at
                ):
                    raise IntegrityError("Evidência de despacho do host é imutável.")
            if isinstance(previous, Artifact) and previous.status == "ready":
                if validated.status not in ("ready", "deleted"):
                    raise InvalidTransition("Artefato pronto não volta ao rascunho.")
                for name in ("storage_key", "sha256", "size_bytes", "media_type"):
                    if getattr(previous, name) != getattr(validated, name):
                        raise IntegrityError(
                            "Artefato pronto exige novo registro para outra versão."
                        )
            if isinstance(previous, Artifact) and previous.status in ("failed", "deleted"):
                raise InvalidTransition("Artefato terminal é imutável; crie outro registro.")
            self._validate_links(validated)
            updated = self._spec.model.model_validate(
                validated.model_dump()
                | {"revision": expected_revision + 1, "updated_at": utc_now()}
            )
            assignments = ",".join(f"{column}=?" for column in self._spec.columns)
            self._context.connection.execute(
                f"UPDATE {self._spec.table} SET {assignments} WHERE id=? AND revision=?",
                (*_encode(updated), str(updated.id), expected_revision),
            )
            if self._context.connection.changes() != 1:
                raise RevisionConflict("A revisão mudou durante a atualização.")
            self._event(updated, "reconciled" if reconciled else "updated", previous)
        return updated


_ACTION_TRANSITIONS: dict[str, frozenset[str]] = {
    "prepared": frozenset({"awaiting_approval", "ready", "cancelled"}),
    "awaiting_approval": frozenset({"ready", "cancelled"}),
    "ready": frozenset({"awaiting_approval", "dispatch_started", "cancelled"}),
    "dispatch_started": frozenset({"confirmed", "failed_no_effect", "outcome_unknown"}),
    "confirmed": frozenset(),
    "failed_no_effect": frozenset(),
    "outcome_unknown": frozenset(),
    "cancelled": frozenset(),
}


def _validate_action_update(previous: Action, updated: Action, *, reconciled: bool) -> None:
    if previous.status in ("confirmed", "failed_no_effect", "cancelled"):
        raise InvalidTransition("Ação terminal é imutável; preserve sua evidência.")
    if previous.status == "outcome_unknown" and not reconciled:
        raise InvalidTransition("Resultado desconhecido exige reconciliação explícita.")
    if previous.status != "prepared":
        for name in ("tool_name", "parameters", "idempotency_key", "attempt"):
            if getattr(previous, name) != getattr(updated, name):
                raise InvalidTransition("Parâmetros de ação já preparada/autorizada são imutáveis.")
    if previous.status in ("dispatch_started", "outcome_unknown"):
        for name in ("policy_revision", "lease_generation"):
            if getattr(previous, name) != getattr(updated, name):
                raise InvalidTransition("Evidência de despacho é imutável após início.")
        if previous.result is not None and previous.result != updated.result:
            raise InvalidTransition("Resultado já registrado não pode ser substituído.")
        if updated.status == previous.status:
            raise InvalidTransition("Ação despachada exige conclusão; não reescrever evidência.")
    if updated.status == previous.status:
        return
    if reconciled:
        if previous.status != "outcome_unknown" or updated.status not in (
            "confirmed",
            "failed_no_effect",
        ):
            raise InvalidTransition(
                "Reconciliação exige resultado desconhecido e conclusão segura."
            )
    elif updated.status not in _ACTION_TRANSITIONS[previous.status]:
        raise InvalidTransition("Transição de ação não permitida; não repetir efeito desconhecido.")


class Agents(_Repository[Agent]):
    def find_onboarding_receipt(self, receipt_hash: str) -> list[Agent]:
        """Localiza até duas criações para detectar também um estado ambíguo."""
        self._context.check()
        rows = self._context.connection.execute(
            f"SELECT {','.join(self._spec.columns)} FROM agents "
            "WHERE json_extract(metadata_json, '$.onboarding_command.receipt_hash')=? LIMIT 2",
            (receipt_hash,),
        )
        return [self._decode(row) for row in rows]

    def list(
        self,
        *,
        status: Literal["active", "paused", "archived"] | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Agent]:
        return self._list({"status": status}, limit, offset)


class Conversations(_Repository[Conversation]):
    def list(
        self,
        *,
        agent_id: UUID | None = None,
        status: Literal["active", "archived"] | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Conversation]:
        return self._list({"agent_id": agent_id, "status": status}, limit, offset)


class Messages(_Repository[Message]):
    def count(self, *, conversation_id: UUID) -> int:
        self._context.check()
        return self._context.connection.execute(
            "SELECT count(*) FROM messages WHERE conversation_id=?",
            (str(UUID(str(conversation_id))),),
        ).get

    def list(
        self, *, conversation_id: UUID | None = None, limit: int = 100, offset: int = 0
    ) -> list[Message]:
        return self._list({"conversation_id": conversation_id}, limit, offset)


class Tasks(_Repository[Task]):
    def find_submission(self, client_request_id: UUID | str) -> Task | None:
        self._context.check()
        row = self._context.connection.execute(
            f"SELECT {','.join(self._spec.columns)} FROM tasks WHERE submission_key=?",
            (str(UUID(str(client_request_id))),),
        ).fetchone()
        return self._decode(row) if row else None

    def list(
        self,
        *,
        agent_id: UUID | None = None,
        status: TaskStatus | None = None,
        limit: int = 100,
        offset: int = 0,
        newest_first: bool = False,
    ) -> list[Task]:
        return self._list(
            {"agent_id": agent_id, "status": status}, limit, offset, newest_first=newest_first
        )


class Runs(_Repository[Run]):
    def latest(self, task_id: UUID) -> Run | None:
        runs = self._list({"task_id": task_id}, 1, 0, newest_first=True)
        return runs[0] if runs else None

    def list(self, *, task_id: UUID | None = None, limit: int = 100, offset: int = 0) -> list[Run]:
        return self._list({"task_id": task_id}, limit, offset)


class TaskCommands(_Repository[TaskCommand]):
    def list(self, *, task_id: UUID, limit: int = 100, offset: int = 0) -> list[TaskCommand]:
        return self._list({"task_id": task_id}, limit, offset)

    def find_request(
        self, task_id: UUID | str, client_request_id: UUID | str
    ) -> TaskCommand | None:
        self._context.check()
        row = self._context.connection.execute(
            f"SELECT {','.join(self._spec.columns)} FROM task_commands "
            "WHERE task_id=? AND client_request_id=?",
            (str(UUID(str(task_id))), str(UUID(str(client_request_id)))),
        ).fetchone()
        return self._decode(row) if row else None

    def update(self, record: TaskCommand, expected_revision: int) -> TaskCommand:
        raise IntegrityError("Comandos são append-only.")


class ModelCalls(_Repository[ModelCall]):
    @staticmethod
    def request_digest(provider_config: dict, request: dict, snapshot: dict) -> str:
        data = {"provider_config": provider_config, "request": request, "snapshot": snapshot}
        encoded = json.dumps(
            data, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":")
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def create(self, record: ModelCall) -> ModelCall:
        if record.status != "prepared":
            raise IntegrityError("Novo journal precisa de intenção prepared.")
        validated = self._validated(record)
        if validated.request_hash != self.request_digest(
            validated.provider_config, validated.request, validated.snapshot
        ):
            raise IntegrityError("Hash não corresponde ao snapshot validado.")
        return super().create(validated)

    def list(
        self, *, run_id: UUID, status: str | None = None, limit: int = 100, offset: int = 0
    ) -> list[ModelCall]:
        return self._list({"run_id": run_id, "status": status}, limit, offset)

    def update(self, record: ModelCall, expected_revision: int) -> ModelCall:
        raise IntegrityError("Journal exige operação de despacho/conclusão com fencing.")

    def _change(self, previous: ModelCall, **updates) -> ModelCall:
        changed = previous.model_copy(update=updates)
        return self._update(changed, previous.revision)


def _execution_atomic(method):
    @wraps(method)
    def guarded(self, *args, **kwargs):
        with self.tasks._mutation():
            return method(self, *args, **kwargs)

    return guarded


class Execution:
    """Fila textual global1+agente1; leases não cancelam processamento remoto.

    A quarentena é da tarefa/run. Outra tarefa pode usar um recurso expirado,
    enquanto um provedor ainda processa a chamada antiga de resultado desconhecido.
    """

    GLOBAL_RESOURCE = "worker_slot:0"

    def __init__(self, context: _Context, tasks: Tasks, runs: Runs, calls: ModelCalls) -> None:
        self._context = context
        self.tasks, self.runs, self.calls = tasks, runs, calls

    @staticmethod
    def _ttl(ttl_seconds: int) -> None:
        if (
            isinstance(ttl_seconds, bool)
            or not isinstance(ttl_seconds, int)
            or not 5 <= ttl_seconds <= 300
        ):
            raise ValueError("Lease TTL entre 5 e 300 segundos.")

    def _lease(self, resource_key: str):
        return self._context.connection.execute(
            "SELECT owner_id,run_id,generation,expires_at FROM execution_leases "
            "WHERE resource_key=?",
            (resource_key,),
        ).fetchone()

    def assert_claim(self, claim: ExecutionClaim, *, now: datetime) -> None:
        self._context.check()
        keys = {self.GLOBAL_RESOURCE, f"agent:{claim.task.agent_id}"}
        if {token.resource_key for token in claim.leases} != keys or len(claim.leases) != 2:
            raise RevisionConflict("Lease incompleta ou incompatível.")
        instant = _timestamp(now)
        for token in claim.leases:
            row = self._lease(token.resource_key)
            if (
                row is None
                or token.owner_id != claim.owner_id
                or token.run_id != claim.run.id
                or row[:3] != (str(claim.owner_id), str(claim.run.id), token.generation)
                or row[3] <= instant
            ):
                raise RevisionConflict("Lease antiga ou expirada; não gravar/despachar.")

    def _tokens(self, owner_id: UUID, run: Run, agent_id: UUID, now: datetime, ttl: int):
        tokens = []
        expires = now + timedelta(seconds=ttl)
        for resource in (self.GLOBAL_RESOURCE, f"agent:{agent_id}"):
            row = self._lease(resource)
            if row is not None and row[0] is not None and row[3] > _timestamp(now):
                return None
            generation = row[2] + 1 if row else 1
            tokens.append(
                LeaseToken(
                    resource_key=resource,
                    owner_id=owner_id,
                    run_id=run.id,
                    generation=generation,
                    expires_at=expires,
                )
            )
        for token in tokens:
            self._context.connection.execute(
                "INSERT INTO execution_leases(resource_key,owner_id,run_id,generation,"
                "acquired_at,expires_at) "
                "VALUES(?,?,?,?,?,?) ON CONFLICT(resource_key) DO UPDATE SET "
                "owner_id=excluded.owner_id,run_id=excluded.run_id,generation=excluded.generation,"
                "acquired_at=excluded.acquired_at,expires_at=excluded.expires_at",
                (
                    token.resource_key,
                    str(owner_id),
                    str(run.id),
                    token.generation,
                    _timestamp(now),
                    _timestamp(expires),
                ),
            )
        return tuple(tokens)

    @_execution_atomic
    def claim_next(
        self, owner_id: UUID | str, *, now: datetime, ttl_seconds: int = 30
    ) -> ExecutionClaim | None:
        self._context.check(write=True)
        self._ttl(ttl_seconds)
        owner_id = UUID(str(owner_id))
        self.recover_expired(now=now)
        global_lease = self._lease(self.GLOBAL_RESOURCE)
        if global_lease and global_lease[0] is not None and global_lease[3] > _timestamp(now):
            return None
        rows = self._context.connection.execute(
            f"SELECT {','.join('t.' + column for column in self.tasks._spec.columns)} FROM tasks t "
            "JOIN agents a ON a.id=t.agent_id WHERE t.status='queued' "
            "AND t.desired_state='running' "
            "AND a.status='active' AND t.calls_started<t.max_calls "
            "AND t.active_milliseconds<t.max_active_seconds*1000 "
            "AND NOT EXISTS(SELECT 1 FROM model_calls c JOIN runs r ON r.id=c.run_id "
            "WHERE r.task_id=t.id AND c.status='outcome_unknown' "
            "AND c.unknown_acknowledged_at IS NULL) "
            "ORDER BY t.created_at,t.id LIMIT 100",
        )
        for row in list(rows):
            task = self.tasks._decode(row)
            existing = self.runs.list(task_id=task.id, limit=1000)
            unfinished = [run for run in existing if run.status in ("queued", "running")]
            run = unfinished[-1] if unfinished else self.runs.create(Run(task_id=task.id))
            tokens = self._tokens(owner_id, run, task.agent_id, now, ttl_seconds)
            if tokens is None:
                continue
            task = self.tasks.update(task.model_copy(update={"status": "running"}), task.revision)
            run = self.runs.update(
                run.model_copy(update={"status": "running", "started_at": run.started_at or now}),
                run.revision,
            )
            return ExecutionClaim(task=task, run=run, owner_id=owner_id, leases=tokens)
        return None

    @_execution_atomic
    def renew(
        self, claim: ExecutionClaim, *, now: datetime, ttl_seconds: int = 30
    ) -> ExecutionClaim:
        self._ttl(ttl_seconds)
        self.assert_claim(claim, now=now)
        expires = now + timedelta(seconds=ttl_seconds)
        tokens = []
        for token in claim.leases:
            self._context.connection.execute(
                "UPDATE execution_leases SET expires_at=? WHERE resource_key=? AND owner_id=? "
                "AND run_id=? AND generation=?",
                (
                    _timestamp(expires),
                    token.resource_key,
                    str(claim.owner_id),
                    str(claim.run.id),
                    token.generation,
                ),
            )
            tokens.append(token.model_copy(update={"expires_at": expires}))
        return claim.model_copy(update={"leases": tuple(tokens)})

    @_execution_atomic
    def release(self, claim: ExecutionClaim) -> None:
        self._context.check(write=True)
        # Não exigir TTL vigente para liberar, mas nunca liberar a geração de outro dono.
        for token in claim.leases:
            self._context.connection.execute(
                "UPDATE execution_leases SET owner_id=NULL,run_id=NULL,expires_at=NULL "
                "WHERE resource_key=? AND owner_id=? AND run_id=? AND generation=?",
                (token.resource_key, str(claim.owner_id), str(claim.run.id), token.generation),
            )

    @_execution_atomic
    def charge_active(self, claim: ExecutionClaim, elapsed_ms: int, *, now: datetime) -> Task:
        self.assert_claim(claim, now=now)
        if isinstance(elapsed_ms, bool) or not isinstance(elapsed_ms, int) or elapsed_ms < 0:
            raise ValueError("Tempo ativo deve ser quantidade não negativa de milissegundos.")
        task = self.tasks.get(claim.task.id)
        return self.tasks.update(
            task.model_copy(update={"active_milliseconds": task.active_milliseconds + elapsed_ms}),
            task.revision,
        )

    @_execution_atomic
    def begin_call(self, claim: ExecutionClaim, call: ModelCall, *, now: datetime) -> ModelCall:
        self.assert_claim(claim, now=now)
        task = self.tasks.get(claim.task.id)
        agent = self._context.connection.execute(
            "SELECT revision,status FROM agents WHERE id=?", (str(task.agent_id),)
        ).fetchone()
        if (
            task.status != "running"
            or task.desired_state != "running"
            or task.control_revision != call.task_control_revision
            or agent != (call.agent_revision, "active")
            or call.run_id != claim.run.id
            or call.lease_generation != claim.generation
        ):
            raise RevisionConflict("Intenção mudou antes do despacho.")
        current_run = self.runs.get(claim.run.id)
        if current_run.provider_config != call.provider_config:
            raise RevisionConflict("Configuração difere do snapshot da execução.")
        if (
            task.calls_started >= task.max_calls
            or task.active_milliseconds >= task.max_active_seconds * 1000
        ):
            raise InvalidTransition("Orçamento da tarefa esgotado.")
        unresolved = self._context.connection.execute(
            "SELECT EXISTS(SELECT 1 FROM model_calls c JOIN runs r ON r.id=c.run_id "
            "WHERE r.task_id=? "
            "AND (c.status='dispatch_started' OR (c.status='outcome_unknown' "
            "AND c.unknown_acknowledged_at IS NULL)))",
            (str(task.id),),
        ).get
        if unresolved:
            raise InvalidTransition("Chamada pendente/desconhecida impede novo despacho.")
        previous = self.calls.get(call.id)
        if previous is None:
            previous = self.calls.create(call)
        if previous.status != "prepared" or previous.model_dump() != call.model_dump():
            raise RevisionConflict("Intenção já despachada ou alterada.")
        self.tasks.update(
            task.model_copy(update={"calls_started": task.calls_started + 1}), task.revision
        )
        return self.calls._change(previous, status="dispatch_started", started_at=now)

    @_execution_atomic
    def finish_call(
        self,
        claim: ExecutionClaim,
        call_id: UUID | str,
        *,
        expected_revision: int,
        status: str,
        response: dict | None = None,
        output_message_id: UUID | None = None,
        error_code: str | None = None,
        obsolete: bool = False,
        now: datetime,
    ) -> ModelCall:
        self.assert_claim(claim, now=now)
        call = self.calls.get(call_id)
        if call is None:
            raise NotFoundError("Chamada não encontrada.")
        if (
            call.run_id != claim.run.id
            or call.lease_generation != claim.generation
            or call.revision != expected_revision
        ):
            raise RevisionConflict("Journal pertence a outro despacho/revisão.")
        if call.status != "dispatch_started" or status not in (
            "confirmed",
            "failed_no_effect",
            "outcome_unknown",
        ):
            raise InvalidTransition("Conclusão inválida do journal.")
        # Um erro HTTP após despacho não prova ausência de efeito: usar unknown.
        if status == "failed_no_effect":
            raise InvalidTransition(
                "Após despacho, ausência de efeito exige prova/reconciliação futura."
            )
        return self.calls._change(
            call,
            status=status,
            response=response,
            output_message_id=output_message_id,
            error_code=error_code,
            finished_at=now,
            metadata=call.metadata | {"obsolete": obsolete},
        )

    @_execution_atomic
    def discard_prepared(
        self,
        claim: ExecutionClaim,
        call_id: UUID | str,
        *,
        now: datetime,
        error_code: str | None = None,
    ) -> ModelCall:
        self.assert_claim(claim, now=now)
        call = self.calls.get(call_id)
        if call is None:
            raise NotFoundError("Intenção não encontrada.")
        if call.run_id != claim.run.id or call.status != "prepared":
            raise InvalidTransition("Somente intenção não despachada pode ser descartada.")
        return self.calls._change(call, status="cancelled", error_code=error_code, finished_at=now)

    @_execution_atomic
    def acknowledge_unknown(self, task_id: UUID | str, *, command_id: UUID, now: datetime) -> None:
        self._context.check(write=True)
        command = self._context.connection.execute(
            "SELECT task_id,kind,payload_json,expected_revision FROM task_commands WHERE id=?",
            (str(command_id),),
        ).fetchone()
        if (
            command is None
            or command[0] != str(task_id)
            or command[1] != "resume"
            or json.loads(command[2]).get("acknowledge_unknown") is not True
            or command[3] != self.tasks.get(task_id).revision
        ):
            raise IntegrityError("Reconhecimento exige comando explícito de retomada.")
        rows = self._context.connection.execute(
            f"SELECT {','.join('c.' + column for column in self.calls._spec.columns)} "
            "FROM model_calls c "
            "JOIN runs r ON r.id=c.run_id WHERE r.task_id=? AND c.status='outcome_unknown' "
            "AND c.unknown_acknowledged_at IS NULL",
            (str(task_id),),
        )
        for row in list(rows):
            self.calls._change(self.calls._decode(row), unknown_acknowledged_at=now)

    @_execution_atomic
    def recover_expired(self, *, now: datetime) -> int:
        self._context.check(write=True)
        rows = self._context.connection.execute(
            "SELECT DISTINCT run_id FROM execution_leases WHERE owner_id IS NOT NULL "
            "AND expires_at<=?",
            (_timestamp(now),),
        )
        count = 0
        for (run_id,) in list(rows):
            run = self.runs.get(run_id)
            task = self.tasks.get(run.task_id)
            calls = self.calls.list(run_id=run.id, status="dispatch_started", limit=1000)
            unknown = bool(calls)
            for call in calls:
                self.calls._change(
                    call, status="outcome_unknown", error_code="worker_lost", finished_at=now
                )
                # A decisão já foi consumida no mesmo commit de begin_call. Uma
                # morte do processo deve preservar também seu journal de ação.
                if call.metadata.get("approval_id"):
                    approvals = Approvals(
                        self._context,
                        _Spec("approvals", "approval", Approval, ("action_id",)),
                    )
                    actions = Actions(
                        self._context,
                        _Spec("actions", "action", Action, ("run_id",)),
                    )
                    approval = approvals.get(call.metadata["approval_id"])
                    action = actions.get(approval.action_id) if approval else None
                    if (
                        action is None
                        or action.run_id != run.id
                        or approval.metadata.get("model_call_id") != str(call.id)
                    ):
                        raise IntegrityError("Journal de aprovação incompatível com a chamada.")
                    if action.status == "dispatch_started":
                        actions.update(
                            action.model_copy(update={"status": "outcome_unknown"}),
                            action.revision,
                        )
                if call.started_at is not None:
                    duration = min(
                        int(call.provider_config.get("deadline_seconds", 60) * 1000),
                        max(0, int((now - call.started_at).total_seconds() * 1000)),
                    )
                    task = self.tasks.update(
                        task.model_copy(
                            update={"active_milliseconds": task.active_milliseconds + duration}
                        ),
                        task.revision,
                    )
            if task.status not in ("completed", "failed", "cancelled"):
                status = (
                    "cancelled"
                    if task.desired_state == "cancelled"
                    else "paused"
                    if unknown or task.desired_state == "paused"
                    else "queued"
                )
                desired = (
                    "paused"
                    if unknown and task.desired_state != "cancelled"
                    else task.desired_state
                )
                task = self.tasks.update(
                    task.model_copy(update={"status": status, "desired_state": desired}),
                    task.revision,
                )
                run = self.runs.update(
                    run.model_copy(
                        update={
                            "status": status,
                            "error": "outcome_unknown" if unknown else run.error,
                            "checkpoint": run.checkpoint | {"attention_required": unknown},
                        }
                    ),
                    run.revision,
                )
            elif unknown:
                self.runs.update(
                    run.model_copy(
                        update={
                            "error": "outcome_unknown",
                            "checkpoint": run.checkpoint | {"attention_required": True},
                        }
                    ),
                    run.revision,
                )
            self._context.connection.execute(
                "UPDATE execution_leases SET owner_id=NULL,run_id=NULL,expires_at=NULL "
                "WHERE run_id=? AND expires_at<=?",
                (run_id, _timestamp(now)),
            )
            count += 1
        return count


class Actions(_Repository[Action]):
    def list(
        self,
        *,
        run_id: UUID | None = None,
        status: ActionStatus | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Action]:
        return self._list({"run_id": run_id, "status": status}, limit, offset)

    def reconcile(
        self,
        id: UUID | str,
        outcome: Literal["confirmed", "failed_no_effect"],
        *,
        expected_revision: int,
        evidence_ref: str,
    ) -> Action:
        self._context.check(write=True)
        if outcome not in ("confirmed", "failed_no_effect") or not evidence_ref.strip():
            raise ValueError("Reconciliação exige conclusão e referência de evidência.")
        action = self.get(id)
        if action is None:
            raise NotFoundError("Ação não encontrada para reconciliação.")
        updated = action.model_copy(
            update={
                "status": outcome,
                "metadata": action.metadata | {"reconciliation_ref": evidence_ref},
            }
        )
        return self._update(updated, expected_revision, reconciled=True)


class Policies(_Repository[Policy]):
    def list(
        self, *, agent_id: UUID | None = None, limit: int = 100, offset: int = 0
    ) -> list[Policy]:
        return self._list({"agent_id": agent_id}, limit, offset)


class Environments(_Repository[Environment]):
    def list(self, agent_id=None, limit=100, offset=0):
        return self._list({"agent_id": agent_id}, limit, offset, newest_first=True)

    def request(self, agent_id: UUID, client_request_id: UUID) -> Environment | None:
        records = self._list({"agent_id": agent_id, "client_request_id": client_request_id}, 1, 0)
        return records[0] if records else None


class HostJobs(_Repository[HostJob]):
    def for_environment(self, environment_id: UUID) -> HostJob | None:
        records = self._list({"environment_id": environment_id}, 1, 0)
        return records[0] if records else None


class Plugins(_Repository[PluginInstallation]):
    def list(self, *, limit: int = 100, offset: int = 0) -> list[PluginInstallation]:
        return self._list({}, limit, offset)


class ToolGrants(_Repository[ToolGrant]):
    def list(self, *, agent_id: UUID, limit: int = 100, offset: int = 0) -> list[ToolGrant]:
        return self._list({"agent_id": agent_id}, limit, offset)

    def find(self, agent_id: UUID, plugin_id: UUID, tool_name: str) -> ToolGrant | None:
        records = self._list(
            {"agent_id": agent_id, "plugin_id": plugin_id, "tool_name": tool_name}, 1, 0
        )
        return records[0] if records else None


class Approvals(_Repository[Approval]):
    def list(
        self, *, action_id: UUID | None = None, limit: int = 100, offset: int = 0
    ) -> list[Approval]:
        return self._list({"action_id": action_id}, limit, offset)


class Routines(_Repository[Routine]):
    def list(
        self, *, agent_id: UUID | None = None, limit: int = 100, offset: int = 0
    ) -> list[Routine]:
        return self._list({"agent_id": agent_id}, limit, offset)


class Memories(_Repository[Memory]):
    def delete(self, id: UUID | str, *, expected_revision: int) -> None:
        """Apaga o registro canônico; o ledger conserva apenas identidade e revisão."""
        if (
            isinstance(expected_revision, bool)
            or not isinstance(expected_revision, int)
            or expected_revision < 1
        ):
            raise ValueError("expected_revision precisa ser inteiro positivo.")
        with self._mutation():
            previous = self.get(id)
            if previous is None:
                raise NotFoundError("Memória não encontrada.")
            if previous.revision != expected_revision:
                raise RevisionConflict("A memória mudou; releia antes de apagar.")
            self._context.connection.execute(
                "DELETE FROM memories WHERE id=? AND revision=?",
                (str(previous.id), expected_revision),
            )
            if self._context.connection.changes() != 1:
                raise RevisionConflict("A memória mudou durante a exclusão.")
            self._context.connection.execute(
                "INSERT INTO domain_events(id,created_at,entity_type,entity_id,event_type,"
                "payload_json,actor,source,correlation_id) VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    str(uuid4()),
                    _timestamp(utc_now()),
                    "memory",
                    str(previous.id),
                    "deleted",
                    _json(
                        {
                            "revision": expected_revision + 1,
                            "previous_revision": expected_revision,
                            "scope": previous.scope,
                        }
                    ),
                    self._context.actor,
                    self._context.source,
                    self._context.correlation_id,
                ),
            )

    def visible(
        self,
        *,
        agent_id: UUID,
        task_id: UUID | None = None,
        all_tasks: bool = False,
        active_only: bool = False,
    ) -> Iterator[Memory]:
        """Percorre apenas os escopos elegíveis, sem carregar dados de outra abelha."""
        self._context.check()
        agent_id = UUID(str(agent_id))
        task_id = UUID(str(task_id)) if task_id is not None else None
        task_clause = "1=1" if all_tasks else "task_id=?"
        values: tuple = (str(agent_id), str(agent_id))
        if not all_tasks:
            values += (str(task_id) if task_id is not None else None,)
        active = (
            " AND status='active' AND deleted_at IS NULL AND source='user'" if active_only else ""
        )
        rows = self._context.connection.execute(
            f"SELECT {','.join(self._spec.columns)} FROM memories "
            "WHERE (scope='user' OR (scope='agent' AND agent_id=?) "
            f"OR (scope='task' AND agent_id=? AND {task_clause})){active} "
            "ORDER BY created_at,id",
            values,
        )
        for row in rows:
            self._context.check()
            yield self._decode(row)

    def list(
        self,
        *,
        agent_id: UUID | None = None,
        task_id: UUID | None = None,
        scope: Literal["user", "agent", "task"] | None = None,
        status: Literal["active", "archived"] | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Memory]:
        return self._list(
            {"agent_id": agent_id, "task_id": task_id, "scope": scope, "status": status},
            limit,
            offset,
        )


class Artifacts(_Repository[Artifact]):
    def list(
        self, *, task_id: UUID | None = None, limit: int = 100, offset: int = 0
    ) -> list[Artifact]:
        return self._list({"task_id": task_id}, limit, offset)


class Events:
    def __init__(self, context: _Context) -> None:
        self._context = context

    def list(
        self,
        *,
        entity_type: EntityType | None = None,
        entity_id: UUID | None = None,
        after_seq: int = 0,
        limit: int = 100,
    ) -> list[DomainEvent]:
        self._context.check()
        _pagination(limit, after_seq)
        clauses = ["seq>?"]
        values: list = [after_seq]
        if entity_type is not None:
            clauses.append("entity_type=?")
            values.append(entity_type)
        if entity_id is not None:
            clauses.append("entity_id=?")
            values.append(str(entity_id))
        rows = self._context.connection.execute(
            "SELECT seq,id,created_at,entity_type,entity_id,event_type,payload_json,actor,source,"
            f"correlation_id FROM domain_events WHERE {' AND '.join(clauses)} ORDER BY seq LIMIT ?",
            (*values, limit),
        )
        names = tuple(DomainEvent.model_fields)
        result = []
        for row in rows:
            data = dict(zip(names, row, strict=True))
            data["payload"] = json.loads(data["payload"])
            result.append(DomainEvent.model_validate(data))
        return result


class UnitOfWork:
    def __init__(self, context: _Context) -> None:
        self.agents = Agents(context, _Spec("agents", "agent", Agent))
        self.conversations = Conversations(
            context, _Spec("conversations", "conversation", Conversation, ("agent_id",))
        )
        self.messages = Messages(
            context, _Spec("messages", "message", Message, ("conversation_id",))
        )
        self.tasks = Tasks(
            context, _Spec("tasks", "task", Task, ("agent_id", "conversation_id", "routine_id"))
        )
        self.runs = Runs(context, _Spec("runs", "run", Run, ("task_id",)))
        self.model_calls = ModelCalls(
            context,
            _Spec(
                "model_calls",
                "model_call",
                ModelCall,
                (
                    "run_id",
                    "ordinal",
                    "phase",
                    "task_control_revision",
                    "agent_revision",
                    "lease_generation",
                    "provider_config",
                    "request",
                    "snapshot",
                    "request_hash",
                ),
            ),
        )
        self.task_commands = TaskCommands(
            context, _Spec("task_commands", "task_command", TaskCommand)
        )
        self.execution = Execution(context, self.tasks, self.runs, self.model_calls)
        self.actions = Actions(context, _Spec("actions", "action", Action, ("run_id",)))
        self.policies = Policies(context, _Spec("policies", "policy", Policy, ("agent_id",)))
        self.plugins = Plugins(
            context, _Spec("plugins", "plugin", PluginInstallation, ("manifest", "manifest_hash"))
        )
        self.environments = Environments(
            context,
            _Spec(
                "environments",
                "environment",
                Environment,
                (
                    "agent_id",
                    "name",
                    "template_id",
                    "cpu_count",
                    "memory_mib",
                    "disk_gib",
                    "client_request_id",
                    "request_hash",
                ),
            ),
        )
        self.host_jobs = HostJobs(
            context,
            _Spec(
                "host_jobs", "host_job", HostJob, ("environment_id", "operation", "correlation_id")
            ),
        )
        self.tool_grants = ToolGrants(
            context,
            _Spec("tool_grants", "tool_grant", ToolGrant, ("agent_id", "plugin_id", "tool_name")),
        )
        self.approvals = Approvals(
            context, _Spec("approvals", "approval", Approval, ("action_id",))
        )
        self.routines = Routines(context, _Spec("routines", "routine", Routine, ("agent_id",)))
        self.memories = Memories(
            context, _Spec("memories", "memory", Memory, ("agent_id", "task_id", "scope"))
        )
        self.artifacts = Artifacts(
            context, _Spec("artifacts", "artifact", Artifact, ("task_id", "run_id", "version"))
        )
        self.events = Events(context)


class StateStore:
    def __init__(self, database: Database, cache_ttl_seconds: int = 86400) -> None:
        self._positive_ttl(cache_ttl_seconds)
        self.database = database
        self.cache_ttl_seconds = cache_ttl_seconds

    @contextmanager
    def transaction(
        self,
        *,
        write: bool = True,
        actor: str = "user",
        source: str = "store",
        correlation_id: str | None = None,
    ) -> Iterator[UnitOfWork]:
        if not actor or not source:
            raise ValueError("Transação exige actor e source.")
        with self.database.transaction(write=write) as connection:
            context = _Context(connection, write, actor, source, correlation_id)
            try:
                yield UnitOfWork(context)
            finally:
                context.active = False

    @staticmethod
    def _positive_ttl(value: int) -> None:
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ValueError("TTL precisa ser inteiro positivo.")

    def put_cache(self, key: str, value: JsonValue, ttl_seconds: int | None = None) -> None:
        if not key:
            raise ValueError("Cache exige chave não vazia.")
        ttl = self.cache_ttl_seconds if ttl_seconds is None else ttl_seconds
        self._positive_ttl(ttl)
        now = utc_now()
        encoded = _json(value)
        with self.database.transaction() as connection:
            connection.execute(
                "INSERT INTO cache_entries(key,value_json,created_at,expires_at) VALUES(?,?,?,?) "
                "ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json,"
                "created_at=excluded.created_at,expires_at=excluded.expires_at",
                (key, encoded, _timestamp(now), _timestamp(now + timedelta(seconds=ttl))),
            )

    def get_cache(self, key: str) -> JsonValue:
        with self.database.transaction(write=False) as connection:
            row = connection.execute(
                "SELECT value_json FROM cache_entries WHERE key=? AND expires_at>?",
                (key, _timestamp(utc_now())),
            ).fetchone()
            return json.loads(row[0]) if row is not None else None

    def prune_cache(self, limit: int = 1000, now: datetime | None = None) -> int:
        _pagination(limit)
        instant = _timestamp(utc_now() if now is None else now)
        with self.database.transaction() as connection:
            connection.execute(
                "DELETE FROM cache_entries WHERE key IN (SELECT key FROM cache_entries "
                "WHERE expires_at<=? ORDER BY expires_at,key LIMIT ?)",
                (instant, limit),
            )
            return connection.changes()
