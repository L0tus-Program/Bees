"""Repositórios tipados, revisões otimistas e ledger transacional sem conteúdo."""

import json
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
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
    Memory,
    Message,
    Policy,
    Record,
    Routine,
    Run,
    Task,
    TaskStatus,
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

    def _list(self, filters: dict, limit: int, offset: int) -> list[T]:
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
            "ORDER BY created_at,id LIMIT ? OFFSET ?",
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
            if isinstance(previous, Action) and isinstance(validated, Action):
                _validate_action_update(previous, validated, reconciled=reconciled)
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
        self, *, agent_id: UUID | None = None, limit: int = 100, offset: int = 0
    ) -> list[Conversation]:
        return self._list({"agent_id": agent_id}, limit, offset)


class Messages(_Repository[Message]):
    def list(
        self, *, conversation_id: UUID | None = None, limit: int = 100, offset: int = 0
    ) -> list[Message]:
        return self._list({"conversation_id": conversation_id}, limit, offset)


class Tasks(_Repository[Task]):
    def list(
        self,
        *,
        agent_id: UUID | None = None,
        status: TaskStatus | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Task]:
        return self._list({"agent_id": agent_id, "status": status}, limit, offset)


class Runs(_Repository[Run]):
    def list(self, *, task_id: UUID | None = None, limit: int = 100, offset: int = 0) -> list[Run]:
        return self._list({"task_id": task_id}, limit, offset)


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
    def list(
        self,
        *,
        agent_id: UUID | None = None,
        task_id: UUID | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Memory]:
        return self._list({"agent_id": agent_id, "task_id": task_id}, limit, offset)


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
        self.actions = Actions(context, _Spec("actions", "action", Action, ("run_id",)))
        self.policies = Policies(context, _Spec("policies", "policy", Policy, ("agent_id",)))
        self.approvals = Approvals(
            context, _Spec("approvals", "approval", Approval, ("action_id", "policy_id"))
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
