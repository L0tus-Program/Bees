"""Agenda humana e ocorrências duráveis. Consultas nunca despacham trabalho."""

import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import AwareDatetime, Field, field_validator, model_validator

from bees_core.models import Record, Routine, utc_now
from bees_core.skills import _SECRET, Command, Contract, SkillsRepository, _canonical
from bees_core.storage.database import Database
from bees_core.storage.store import IntegrityError, NotFoundError, RevisionConflict, StateStore


class RoutineSchedule(Contract):
    frequency: Literal["daily", "weekly"]
    time: str = Field(pattern=r"^(?:[01][0-9]|2[0-3]):[0-5][0-9]$")
    weekdays: list[Annotated[int, Field(strict=True)]] = Field(default_factory=list, max_length=7)

    @model_validator(mode="after")
    def days(self):
        if any(type(day) is not int or not 0 <= day <= 6 for day in self.weekdays):
            raise ValueError("Dias usam segunda=0 até domingo=6.")
        if len(set(self.weekdays)) != len(self.weekdays):
            raise ValueError("Dias da semana precisam ser únicos.")
        if (self.frequency == "weekly") != bool(self.weekdays):
            raise ValueError("Agenda semanal exige dias; diária não recebe dias.")
        return self


class RoutineDestination(Contract):
    kind: Literal["conversation"] = "conversation"
    conversation_id: UUID


class RoutineRuntimeBinding(Contract):
    installation_id: UUID
    resource_id: UUID
    plugin_id: UUID
    manifest_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    tool_version: Literal["1"] = "1"


class RoutineDefinition(Contract):
    name: str = Field(min_length=1, max_length=200)
    instructions: str = Field(min_length=1, max_length=4096)
    schedule: RoutineSchedule
    timezone: str
    skill_id: UUID
    skill_version: int = Field(ge=1, strict=True)
    inputs: dict[str, str] = Field(default_factory=dict, max_length=20)
    environment: Literal["none", "own", "personal"]
    destination: RoutineDestination
    notification: Literal["always", "failures", "none"]
    grace_seconds: int = Field(default=60, ge=0, le=3600, strict=True)
    misfire_policy: Literal["skip"] = "skip"
    overlap_policy: Literal["skip"] = "skip"
    # Definições anteriores eram somente preparatórias; upgrade não ativa execução.
    execution_mode: Literal["prepare", "scheduled"] = "prepare"
    runtime_binding: RoutineRuntimeBinding | None = None

    @field_validator("timezone")
    @classmethod
    def timezone_exists(cls, value):
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError, ValueError:
            raise ValueError("timezone deve ser um fuso IANA válido.") from None
        return value

    @model_validator(mode="after")
    def bounded(self):
        raw = self.model_dump_json(
            exclude={"execution_mode", "runtime_binding"}
            if self.execution_mode == "prepare" and self.runtime_binding is None
            else set()
        )
        if len(raw.encode()) > 32768 or _SECRET.search(raw):
            raise ValueError("Rotina excede o limite ou contém possível credencial.")
        if any(not value.strip() or len(value) > 2048 for value in self.inputs.values()):
            raise ValueError("Entradas devem ter conteúdo e até 2048 caracteres.")
        return self


class CreateRoutine(Command):
    definition: RoutineDefinition
    active: bool = Field(default=False, strict=True)


class ReviseRoutine(Command):
    expected_revision: int = Field(ge=1, strict=True)
    definition: RoutineDefinition


class SetRoutineStatus(Command):
    expected_revision: int = Field(ge=1, strict=True)
    status: Literal["active", "paused", "cancelled"]


def command_hash(command):
    payload = command.model_dump(mode="json")
    definition = payload.get("definition")
    if isinstance(definition, dict) and definition.get("execution_mode") == "prepare":
        # Replays anteriores ao scheduler conservam o hash exato já persistido.
        definition.pop("execution_mode")
    if isinstance(definition, dict) and definition.get("runtime_binding") is None:
        definition.pop("runtime_binding", None)
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


class RoutineRevision(Contract):
    routine_id: UUID
    revision: int
    definition: RoutineDefinition
    origin: Literal["human"] = "human"
    created_at: AwareDatetime


class RoutineView(Routine):
    definition: RoutineDefinition | None = None
    origin: Literal["human", "legacy"] = "legacy"


class RoutineOccurrence(Record):
    routine_id: UUID
    routine_revision: int = Field(ge=1)
    scheduled_at: AwareDatetime
    window_end: AwareDatetime
    status: Literal[
        "pending", "running", "completed", "failed", "cancelled", "skipped", "outcome_unknown"
    ]
    reason: str
    task_id: UUID | None = None

    @model_validator(mode="after")
    def ordered_window(self):
        if self.window_end < self.scheduled_at:
            raise ValueError("Janela de ocorrência inválida.")
        if self.window_end != self.scheduled_at and self.status != "skipped":
            raise ValueError("Somente skips podem agregar uma janela de indisponibilidade.")
        return self


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Instante exige timezone.")
    return value.astimezone(UTC)


def _stamp(value: datetime) -> str:
    return _utc(value).isoformat(timespec="microseconds")


def _candidate(definition: RoutineDefinition, day) -> datetime | None:
    if definition.schedule.frequency == "weekly" and day.weekday() not in (
        definition.schedule.weekdays
    ):
        return None
    hour, minute = map(int, definition.schedule.time.split(":"))
    local = datetime(day.year, day.month, day.day, hour, minute)
    # fold=0 seleciona somente a primeira passagem quando o relógio repete.
    zoned = local.replace(tzinfo=ZoneInfo(definition.timezone), fold=0)
    utc = zoned.astimezone(UTC)
    if utc.astimezone(zoned.tzinfo).replace(tzinfo=None) != local:
        return None  # Horário inexistente no avanço DST é pulado.
    return utc


def next_run(definition: RoutineDefinition, after: datetime) -> datetime:
    """Primeiro instante estritamente posterior; limite independe do downtime."""
    after = _utc(after)
    day = after.astimezone(ZoneInfo(definition.timezone)).date()
    for offset in range(16):
        candidate = _candidate(definition, day + timedelta(days=offset))
        if candidate is not None and candidate > after:
            return candidate
    raise ValueError("Não foi possível calcular a agenda no horizonte de 15 dias.")


def _latest_run(definition: RoutineDefinition, at: datetime) -> datetime:
    at = _utc(at)
    day = at.astimezone(ZoneInfo(definition.timezone)).date()
    for offset in range(16):
        candidate = _candidate(definition, day - timedelta(days=offset))
        if candidate is not None and candidate <= at:
            return candidate
    raise ValueError("Não foi possível calcular a agenda anterior.")


class RoutineService:
    def __init__(self, database: Database, *, clock=utc_now):
        self.store, self.clock = StateStore(database), clock

    @staticmethod
    def _connection(unit, agent_id):
        agent_id = UUID(str(agent_id))
        if unit.agents.get(agent_id) is None:
            raise NotFoundError("Abelha não encontrada.")
        return unit.agents._context.connection, agent_id

    @staticmethod
    def _definition(connection, routine_id, revision):
        row = connection.execute(
            "SELECT definition_json,content_hash FROM routine_definitions "
            "WHERE routine_id=? AND revision=?",
            (str(routine_id), revision),
        ).fetchone()
        if row is None:
            return None
        if hashlib.sha256(row[0].encode()).hexdigest() != row[1]:
            raise IntegrityError("Integridade da definição de rotina inválida.")
        return RoutineDefinition.model_validate_json(row[0])

    @classmethod
    def _get(cls, unit, connection, agent_id, routine_id):
        routine = unit.routines.get(routine_id)
        if routine is None or routine.agent_id != agent_id:
            raise NotFoundError("Rotina não encontrada nesta abelha.")
        definition = cls._definition(connection, routine.id, routine.revision)
        cursor = connection.execute(
            "SELECT next_run_at FROM routine_cursors WHERE routine_id=? AND routine_revision=?",
            (str(routine.id), routine.revision),
        ).fetchone()
        values = routine.model_dump()
        if cursor is not None:
            values["next_run_at"] = cursor[0]
        return RoutineView.model_validate(
            values | {"definition": definition, "origin": "human" if definition else "legacy"}
        )

    @staticmethod
    def _page(limit, offset):
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("Página deve ter de 1 a 100 itens.")
        if type(offset) is not int or not 0 <= offset <= 2**31 - 1:
            raise ValueError("Deslocamento inválido.")

    def get(self, agent_id, routine_id) -> RoutineView:
        with self.store.transaction(write=False) as unit:
            connection, agent_id = self._connection(unit, agent_id)
            return self._get(unit, connection, agent_id, UUID(str(routine_id)))

    def list(self, agent_id, *, limit=50, offset=0) -> list[RoutineView]:
        self._page(limit, offset)
        with self.store.transaction(write=False) as unit:
            connection, agent_id = self._connection(unit, agent_id)
            return [
                self._get(unit, connection, agent_id, routine.id)
                for routine in unit.routines.list(agent_id=agent_id, limit=limit, offset=offset)
            ]

    def revisions(self, agent_id, routine_id, *, limit=50, offset=0) -> list[RoutineRevision]:
        self._page(limit, offset)
        with self.store.transaction(write=False) as unit:
            connection, agent_id = self._connection(unit, agent_id)
            routine = self._get(unit, connection, agent_id, UUID(str(routine_id)))
            return [
                RoutineRevision(
                    routine_id=routine.id,
                    revision=row[0],
                    definition=self._definition(connection, routine.id, row[0]),
                    created_at=row[1],
                )
                for row in connection.execute(
                    "SELECT revision,created_at FROM routine_definitions WHERE routine_id=? "
                    "ORDER BY revision DESC LIMIT ? OFFSET ?",
                    (str(routine.id), limit, offset),
                )
            ]

    @staticmethod
    def _validate_links(unit, connection, agent_id, definition, *, active):
        skill = SkillsRepository(connection, agent_id).get(definition.skill_id)
        revision = SkillsRepository(connection, agent_id).revision(
            definition.skill_id, definition.skill_version
        )
        if active and skill.enabled_version != definition.skill_version:
            raise RevisionConflict("A revisão da skill precisa estar habilitada antes de ativar.")
        supplied = set(definition.inputs)
        declared = {item.name for item in revision.definition.inputs}
        required = {item.name for item in revision.definition.inputs if item.required}
        if not required <= supplied or not supplied <= declared:
            raise ValueError("Entradas precisam satisfazer a revisão fixa da skill.")
        if definition.environment != revision.definition.resources.environment:
            raise ValueError("Ambiente precisa corresponder ao declarado pela skill.")
        if definition.execution_mode == "scheduled":
            from bees_core.skill_context import SkillTaskInput, supported

            if not supported(revision.definition):
                raise ValueError("A execução programada exige recursos compatíveis.")
            SkillTaskInput(
                client_request_id=UUID(int=1),
                skill_id=definition.skill_id,
                skill_version=definition.skill_version,
                inputs=definition.inputs,
            )
        conversation = unit.conversations.get(definition.destination.conversation_id)
        if conversation is None or conversation.agent_id != agent_id:
            raise NotFoundError("Conversa de destino não encontrada nesta abelha.")

    def _mutate(self, agent_id, routine_id, command, operation):
        target = operation + (":" + str(routine_id) if routine_id else "")
        digest = command_hash(command)
        with self.store.transaction(actor="user", source="routine_human") as unit:
            connection, agent_id = self._connection(unit, agent_id)
            replay = connection.execute(
                "SELECT agent_id,target,request_hash,response_json FROM routine_commands "
                "WHERE client_request_id=?",
                (str(command.client_request_id),),
            ).fetchone()
            if replay is not None:
                if replay[:3] != (str(agent_id), target, digest):
                    raise RevisionConflict("Identificador já usado para outra intenção de rotina.")
                return RoutineView.model_validate_json(replay[3])
            previous = self._get(unit, connection, agent_id, routine_id) if routine_id else None
            if previous is not None and previous.revision != command.expected_revision:
                raise RevisionConflict("A rotina mudou; atualize antes de editar.")
            if previous is not None and previous.status == "cancelled":
                raise RevisionConflict("Rotina cancelada é terminal; crie outra explicitamente.")
            definition = command.definition if operation != "status" else previous.definition
            if definition is None:
                raise RevisionConflict("Rotina legada exige revisão humana antes de ativar.")
            status = (
                command.status
                if operation == "status"
                else ("active" if operation == "create" and command.active else "paused")
            )
            if definition.execution_mode == "scheduled" and (
                operation != "status" or status == "active"
            ):
                from bees_core.executor_transport.contracts import TransportError
                from bees_core.providers.errors import ProviderError
                from bees_core.tasks import TaskError
                from bees_core.tool_runtime import available_runtime
                from bees_core.tools import ToolError

                try:
                    resource, plugin_id = available_runtime(self.store.database, agent_id)
                    plugin = unit.plugins.get(plugin_id)
                    binding = RoutineRuntimeBinding(
                        installation_id=resource.identity.installation_id,
                        resource_id=resource.identity.resource_id,
                        plugin_id=plugin_id,
                        manifest_hash=plugin.manifest_hash,
                    )
                except TransportError, ProviderError, ToolError, TaskError:
                    raise RevisionConflict(
                        "Recurso atual indisponível; confira instalação e concessão."
                    ) from None
                if definition.runtime_binding is not None and definition.runtime_binding != binding:
                    raise RevisionConflict(
                        "Recurso ou versão mudou; revise e selecione o recurso atual."
                    )
                definition = definition.model_copy(update={"runtime_binding": binding})
            self._validate_links(unit, connection, agent_id, definition, active=status == "active")
            now = _utc(self.clock())
            cursor = (
                connection.execute(
                    "SELECT evaluated_through FROM routine_cursors WHERE routine_id=?",
                    (str(routine_id),),
                ).fetchone()
                if routine_id
                else None
            )
            boundary = max(now, datetime.fromisoformat(cursor[0])) if cursor else now
            upcoming = next_run(definition, boundary) if status == "active" else None
            values = {
                "agent_id": agent_id,
                "name": definition.name,
                "instructions": definition.instructions,
                "schedule": definition.schedule.model_dump(mode="json"),
                "timezone": definition.timezone,
                "next_run_at": upcoming,
                "status": status,
                "misfire_policy": "skip",
                "overlap_policy": "skip",
            }
            if previous is None:
                routine = unit.routines.create(Routine(**values))
            else:
                base = unit.routines.get(previous.id)
                routine = unit.routines.update(
                    Routine.model_validate(base.model_dump() | values), previous.revision
                )
                connection.execute(
                    "UPDATE routine_occurrences SET status='cancelled',reason='routine_changed',"
                    "revision=revision+1,updated_at=max(updated_at,?) "
                    "WHERE routine_id=? AND status='pending'",
                    (_stamp(now), str(routine.id)),
                )
            raw = _canonical(definition)
            connection.execute(
                "INSERT INTO routine_definitions VALUES(?,?,?,?, 'human',?)",
                (
                    str(routine.id),
                    routine.revision,
                    raw,
                    hashlib.sha256(raw.encode()).hexdigest(),
                    _stamp(now),
                ),
            )
            connection.execute(
                "INSERT INTO routine_cursors VALUES(?,?,?,?) ON CONFLICT(routine_id) "
                "DO UPDATE SET routine_revision=excluded.routine_revision,"
                "next_run_at=excluded.next_run_at,evaluated_through=excluded.evaluated_through",
                (
                    str(routine.id),
                    routine.revision,
                    _stamp(upcoming) if upcoming else None,
                    _stamp(boundary),
                ),
            )
            result = self._get(unit, connection, agent_id, routine.id)
            connection.execute(
                "INSERT INTO routine_commands VALUES(?,?,?,?,?,?)",
                (
                    str(command.client_request_id),
                    str(agent_id),
                    target,
                    digest,
                    result.model_dump_json(),
                    _stamp(now),
                ),
            )
            return result

    def create(self, agent_id, value: CreateRoutine) -> RoutineView:
        return self._mutate(
            agent_id, None, CreateRoutine.model_validate(value.model_dump()), "create"
        )

    def revise(self, agent_id, routine_id, value: ReviseRoutine) -> RoutineView:
        return self._mutate(
            agent_id,
            UUID(str(routine_id)),
            ReviseRoutine.model_validate(value.model_dump()),
            "revise",
        )

    def set_status(self, agent_id, routine_id, value: SetRoutineStatus) -> RoutineView:
        return self._mutate(
            agent_id,
            UUID(str(routine_id)),
            SetRoutineStatus.model_validate(value.model_dump()),
            "status",
        )

    @staticmethod
    def _occurrence(row):
        names = (
            "id",
            "routine_id",
            "routine_revision",
            "scheduled_at",
            "window_end",
            "status",
            "reason",
            "task_id",
            "revision",
            "created_at",
            "updated_at",
        )
        return RoutineOccurrence.model_validate(dict(zip(names, row, strict=True)))

    def occurrences(self, agent_id, routine_id, *, limit=50, offset=0) -> list[RoutineOccurrence]:
        self._page(limit, offset)
        with self.store.transaction(write=False) as unit:
            connection, agent_id = self._connection(unit, agent_id)
            routine = self._get(unit, connection, agent_id, UUID(str(routine_id)))
            result = []
            for row in connection.execute(
                "SELECT * FROM routine_occurrences WHERE routine_id=? "
                "ORDER BY scheduled_at DESC,id LIMIT ? OFFSET ?",
                (str(routine.id), limit, offset),
            ):
                occurrence = self._occurrence(row)
                if occurrence.task_id is not None:
                    task = unit.tasks.get(occurrence.task_id)
                    if task is None or task.agent_id != agent_id or task.routine_id != routine.id:
                        raise IntegrityError("Vínculo de tarefa da ocorrência inválido.")
                    occurrence = occurrence.model_copy(
                        update={"metadata": {"task_status": task.status}}
                    )
                result.append(occurrence)
            return result

    def evaluate(self, agent_id, routine_id, *, expected_revision: int, now: datetime):
        """Materialização interna limitada; não inicia Task, modelo ou efeito externo.

        Uma indisponibilidade arbitrária gera no máximo dois registros: janela pulada
        e último horário ainda dentro da tolerância. O cursor avança atomicamente.
        """
        now = _utc(now)
        with self.store.transaction(actor="system", source="routine_temporal") as unit:
            connection, agent_id = self._connection(unit, agent_id)
            routine = self._get(unit, connection, agent_id, UUID(str(routine_id)))
            if type(expected_revision) is not int or routine.revision != expected_revision:
                raise RevisionConflict("A revisão da rotina mudou antes da avaliação.")
            if routine.definition is None or routine.status != "active":
                return []
            cursor = connection.execute(
                "SELECT next_run_at,evaluated_through FROM routine_cursors "
                "WHERE routine_id=? AND routine_revision=?",
                (str(routine.id), routine.revision),
            ).fetchone()
            if cursor is None or cursor[0] is None or now < datetime.fromisoformat(cursor[1]):
                return []
            due = datetime.fromisoformat(cursor[0])
            if due > now:
                return []
            definition = routine.definition
            latest = _latest_run(definition, now)
            ready = (now - latest).total_seconds() <= definition.grace_seconds
            windows = []
            if latest > due:
                # A janela deixa explícito todo o intervalo perdido, sem enfileirá-lo.
                previous = _latest_run(definition, latest - timedelta(microseconds=1))
                windows.append((due, previous if ready else latest, "skipped", "misfire"))
            if latest == due or ready:
                status, reason = "pending", "scheduled"
                if not ready:
                    status, reason = "skipped", "misfire"
                elif connection.execute(
                    "SELECT 1 FROM routine_occurrences WHERE routine_id=? "
                    "AND status IN ('pending','running','outcome_unknown') LIMIT 1",
                    (str(routine.id),),
                ).fetchone():
                    status, reason = "skipped", "overlap"
                else:
                    skill = SkillsRepository(connection, agent_id).get(definition.skill_id)
                    if skill.enabled_version != definition.skill_version:
                        status, reason = "skipped", "skill_unavailable"
                windows.append((latest, latest, status, reason))
            result = []
            for scheduled, end, status, reason in windows:
                occurrence = RoutineOccurrence(
                    routine_id=routine.id,
                    routine_revision=routine.revision,
                    scheduled_at=scheduled,
                    window_end=end,
                    status=status,
                    reason=reason,
                    created_at=now,
                    updated_at=now,
                )
                connection.execute(
                    "INSERT INTO routine_occurrences VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        str(occurrence.id),
                        str(routine.id),
                        routine.revision,
                        _stamp(scheduled),
                        _stamp(end),
                        status,
                        reason,
                        None,
                        1,
                        _stamp(now),
                        _stamp(now),
                    ),
                )
                result.append(occurrence)
            connection.execute(
                "UPDATE routine_cursors SET next_run_at=?,evaluated_through=? "
                "WHERE routine_id=? AND routine_revision=?",
                (_stamp(next_run(definition, now)), _stamp(now), str(routine.id), routine.revision),
            )
            return result
