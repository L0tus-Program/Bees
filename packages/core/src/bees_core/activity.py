"""Projeções operacionais fechadas: um snapshot SQL, sem executar ou recuperar efeitos."""

import json
from datetime import UTC, timedelta
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from bees_core.models import utc_now
from bees_core.providers.errors import MESSAGES
from bees_core.storage.database import Database
from bees_core.storage.store import Actions, NotFoundError, RevisionConflict, StateStore


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Snapshot(Contract):
    event_seq: int = Field(ge=0)
    observed_at: AwareDatetime


def observe(connection, *, now=None) -> Snapshot:
    """A primeira SELECT fixa o snapshot SQLite; o relógio fixa as derivadas temporais."""
    seq = connection.execute("SELECT COALESCE(MAX(seq),0) FROM domain_events").get
    stamp = now or utc_now()
    if stamp.tzinfo is None or stamp.utcoffset() is None:
        raise ValueError("Instante deve informar fuso.")
    return Snapshot(event_seq=seq, observed_at=stamp.astimezone(UTC))


class Scope(Contract):
    task_id: UUID | None
    run_id: UUID | None


class Interval(Contract):
    since: AwareDatetime
    until: AwareDatetime


class UsageTotals(Contract):
    counted_tokens: int
    reported_tokens: int
    estimated_tokens: int
    reserved_tokens: int
    unknown_tokens: int
    released_entries: int
    entries: int
    open_reservations: int
    unknown_entries: int
    estimate_methods: list[Literal["chars_div_3_v1", "unknown"]]


class Operations(Contract):
    model_calls_in_flight: int
    tool_actions_in_flight: int
    chat_reservations: int
    unknown_model_calls: int
    unknown_tool_actions: int
    waiting_approval: int
    waiting_resource: int


class EnvironmentView(Contract):
    kind: Literal["textual", "unassociated"]
    environment_id: None = None


class UsageView(Contract):
    status: Literal["reserved", "confirmed", "unknown", "released"]
    source: Literal["chat", "task"]
    usage_kind: Literal["reported", "estimated", "unknown"] | None
    reserved_tokens: int
    counted_tokens: int
    input_tokens: int | None
    output_tokens: int | None
    total_tokens: int | None
    estimate_method: Literal["chars_div_3_v1", "unknown"]


Entity = Literal["task", "run", "model_call", "action", "approval", "task_command", "usage_entry"]


class ActivityItem(Contract):
    id: UUID
    seq: int
    occurred_at: AwareDatetime
    entity_type: Entity
    entity_id: UUID
    task_id: UUID | None
    task_title: str | None
    run_id: UUID | None
    action_id: UUID | None
    status: str
    revision: int
    error_code: str | None
    environment: EnvironmentView
    usage: UsageView | None


class ActivityPage(Contract):
    agent_id: UUID
    snapshot: Snapshot
    scope: Scope
    interval: Interval
    unit: Literal["tokens"] = "tokens"
    usage: UsageTotals
    operations: Operations
    items: list[ActivityItem]
    next_before: int | None


_INFORMED = (
    "CASE WHEN u.status='confirmed' AND u.usage_kind IN ('reported','estimated') THEN "
    "CASE WHEN u.total_tokens IS NOT NULL THEN NULLIF(u.total_tokens,0) "
    "WHEN u.input_tokens IS NOT NULL AND u.output_tokens IS NOT NULL "
    "THEN NULLIF(u.input_tokens+u.output_tokens,0) END END"
)
_USAGE_FROM = (
    "FROM usage_entries u LEFT JOIN model_calls c ON c.id=u.model_call_id "
    "LEFT JOIN runs r ON r.id=c.run_id LEFT JOIN tasks t ON t.id=r.task_id "
)
_USAGE_BINDING = "(u.source='chat' OR (t.agent_id=u.agent_id AND t.id IS NOT NULL))"


def _stamp(value):
    return value.astimezone(UTC).isoformat(timespec="microseconds")


def _scope_sql(agent_id, task_id, run_id, *, alias="t"):
    clauses, values = [f"{alias}.agent_id=?"], [str(agent_id)]
    if task_id is not None:
        clauses.append(f"{alias}.id=?")
        values.append(str(task_id))
    if run_id is not None:
        clauses.append("r.id=?")
        values.append(str(run_id))
    return " AND ".join(clauses), values


def usage_totals(connection, agent_id, since, until, *, task_id=None, run_id=None):
    clauses = ["u.agent_id=?", _USAGE_BINDING, "u.created_at>=?", "u.created_at<=?"]
    values = [str(agent_id), _stamp(since), _stamp(until)]
    if task_id is not None:
        clauses.append("t.id=?")
        values.append(str(task_id))
    if run_id is not None:
        clauses.append("r.id=?")
        values.append(str(run_id))
    where = " WHERE " + " AND ".join(clauses)
    informed = _INFORMED
    uncertain = f"(u.status='unknown' OR (u.status='confirmed' AND ({informed}) IS NULL))"
    row = connection.execute(
        "SELECT "
        f"COALESCE(SUM(CASE WHEN u.status='released' THEN 0 ELSE COALESCE({informed},"
        "u.reserved_tokens) END),0),"
        f"COALESCE(SUM(CASE WHEN u.usage_kind='reported' THEN {informed} END),0),"
        f"COALESCE(SUM(CASE WHEN u.usage_kind='estimated' THEN {informed} END),0),"
        "COALESCE(SUM(CASE WHEN u.status='reserved' THEN u.reserved_tokens END),0),"
        f"COALESCE(SUM(CASE WHEN {uncertain} THEN u.reserved_tokens END),0),"
        "COALESCE(SUM(u.status='released'),0),COUNT(*),"
        f"COALESCE(SUM(u.status='reserved'),0),COALESCE(SUM({uncertain}),0) " + _USAGE_FROM + where,
        tuple(values),
    ).fetchone()
    methods = connection.execute(
        "SELECT DISTINCT u.estimate_method " + _USAGE_FROM + where, tuple(values)
    ).fetchall()
    return UsageTotals(
        **dict(zip(list(UsageTotals.model_fields)[:-1], row, strict=True)),
        estimate_methods=sorted(
            {"chars_div_3_v1" if x[0] == "chars_div_3_v1" else "unknown" for x in methods}
        ),
    )


_PUBLIC_ERRORS = frozenset(MESSAGES) | {
    "outcome_unknown",
    "worker_lost",
    "context_changed",
    "response_truncated",
    "content_filtered",
    "empty_response",
    "task_limit_reached",
    "approval_denied",
    "approval_revoked",
    "tool_result_unknown",
    "tool_interrupted_unknown",
}
_TASK_STATUSES = {
    "queued",
    "running",
    "paused",
    "waiting_resource",
    "waiting_approval",
    "completed",
    "failed",
    "cancelled",
}
_STATUSES = {
    "task": _TASK_STATUSES,
    "run": _TASK_STATUSES,
    "model_call": {
        "prepared",
        "dispatch_started",
        "confirmed",
        "failed_no_effect",
        "outcome_unknown",
        "cancelled",
    },
    "action": {
        "prepared",
        "awaiting_approval",
        "ready",
        "dispatch_started",
        "confirmed",
        "failed_no_effect",
        "outcome_unknown",
        "cancelled",
    },
    "approval": {"pending", "approved", "denied", "revoked"},
    "task_command": {"pause", "resume", "cancel", "redirect"},
    "usage_entry": {"reserved", "confirmed", "unknown", "released"},
}


class ActivityService:
    def __init__(self, database: Database):
        self.store = StateStore(database)

    @staticmethod
    def _operations(connection, agent_id, task_id, run_id):
        where, values = _scope_sql(agent_id, task_id, run_id)
        calls = connection.execute(
            "SELECT COALESCE(SUM(c.status='dispatch_started'),0),"
            "COALESCE(SUM(c.status='outcome_unknown'),0) FROM model_calls c "
            "JOIN runs r ON r.id=c.run_id JOIN tasks t ON t.id=r.task_id WHERE "
            + where
            + " AND c.status IN ('dispatch_started','outcome_unknown')",
            tuple(values),
        ).fetchone()
        actions = connection.execute(
            "SELECT COALESCE(SUM(x.status='dispatch_started'),0),"
            "COALESCE(SUM(x.status='outcome_unknown'),0) FROM actions x "
            "JOIN runs r ON r.id=x.run_id JOIN tasks t ON t.id=r.task_id WHERE "
            + where
            + " AND "
            + Actions.STANDALONE
            + " AND x.status IN ('dispatch_started','outcome_unknown')",
            tuple(values),
        ).fetchone()
        task_where, task_values = _scope_sql(agent_id, task_id, None)
        if run_id is not None:
            task_where += (
                " AND ?=(SELECT r.id FROM runs r WHERE r.task_id=t.id "
                "ORDER BY r.created_at DESC,r.id DESC LIMIT 1)"
            )
            task_values.append(str(run_id))
        waiting = connection.execute(
            "SELECT COALESCE(SUM(t.status='waiting_approval'),0),"
            "COALESCE(SUM(t.status='waiting_resource'),0) FROM tasks t WHERE "
            + task_where
            + " AND t.status IN ('waiting_approval','waiting_resource')",
            tuple(task_values),
        ).fetchone()
        chats = (
            0
            if task_id
            else connection.execute(
                "SELECT count(*) FROM usage_entries WHERE agent_id=? "
                "AND source='chat' AND status='reserved'",
                (str(agent_id),),
            ).get
        )
        return Operations(
            model_calls_in_flight=calls[0],
            unknown_model_calls=calls[1],
            tool_actions_in_flight=actions[0],
            unknown_tool_actions=actions[1],
            chat_reservations=chats,
            waiting_approval=waiting[0],
            waiting_resource=waiting[1],
        )

    @staticmethod
    def _events(connection, agent_id, task_id, run_id, interval, limit, before):
        # Branches explícitos impedem que um evento de outro tipo exponha payload livre.
        branches = []
        joins = {
            "task": ("tasks t ON t.id=e.entity_id", "NULL", "NULL", "t.status", "NULL"),
            "run": (
                "runs r ON r.id=e.entity_id JOIN tasks t ON t.id=r.task_id",
                "r.id",
                "NULL",
                "r.status",
                "r.error",
            ),
            "model_call": (
                "model_calls c ON c.id=e.entity_id JOIN runs r ON r.id=c.run_id "
                "JOIN tasks t ON t.id=r.task_id",
                "r.id",
                "NULL",
                "c.status",
                "c.error_code",
            ),
            "action": (
                "actions x ON x.id=e.entity_id JOIN runs r ON r.id=x.run_id "
                "JOIN tasks t ON t.id=r.task_id",
                "r.id",
                "x.id",
                "x.status",
                "NULL",
            ),
            "approval": (
                "approvals a ON a.id=e.entity_id JOIN actions x ON x.id=a.action_id "
                "JOIN runs r ON r.id=x.run_id JOIN tasks t ON t.id=r.task_id",
                "r.id",
                "x.id",
                "a.status",
                "NULL",
            ),
            "task_command": (
                "task_commands k ON k.id=e.entity_id JOIN tasks t ON t.id=k.task_id",
                "NULL",
                "NULL",
                "k.kind",
                "NULL",
            ),
        }
        params = []
        for entity, (join, run, action, status, error) in joins.items():
            if run_id is not None and run == "NULL":
                continue
            where, values = _scope_sql(agent_id, task_id, run_id)
            branches.append(
                "SELECT e.seq,e.id,e.created_at,e.entity_type,e.entity_id,e.payload_json,"
                f"t.id,t.title,{run},{action},{status},{error},t.metadata_json,"
                f"{status.split('.')[0]}.revision "
                f"FROM domain_events e JOIN {join} WHERE e.entity_type='{entity}' AND {where}"
            )
            params.extend(values)
        clauses = ["u.agent_id=?", _USAGE_BINDING]
        usage_values = [str(agent_id)]
        if task_id is not None:
            clauses.append("t.id=?")
            usage_values.append(str(task_id))
        if run_id is not None:
            clauses.append("r.id=?")
            usage_values.append(str(run_id))
        branches.append(
            "SELECT e.seq,e.id,e.created_at,e.entity_type,e.entity_id,e.payload_json,"
            "t.id,t.title,r.id,NULL,u.status,NULL,t.metadata_json,u.revision FROM domain_events e "
            "JOIN usage_entries u ON u.id=e.entity_id "
            "LEFT JOIN model_calls c ON c.id=u.model_call_id "
            "LEFT JOIN runs r ON r.id=c.run_id LEFT JOIN tasks t ON t.id=r.task_id "
            "WHERE e.entity_type='usage_entry' AND " + " AND ".join(clauses)
        )
        params.extend(usage_values)
        query = (
            "SELECT * FROM ("
            + " UNION ALL ".join(branches)
            + ") WHERE created_at>=? AND created_at<=?"
        )
        params.extend((_stamp(interval.since), _stamp(interval.until)))
        if before is not None:
            query += " AND seq<?"
            params.append(before)
        query += " ORDER BY seq DESC LIMIT ?"
        params.append(limit + 1)
        return connection.execute(query, tuple(params)).fetchall()

    @staticmethod
    def _item(unit, row):
        (
            seq,
            event_id,
            stamp,
            entity,
            identity,
            payload,
            task,
            title,
            run,
            action,
            current_status,
            error,
            metadata,
            current_revision,
        ) = row
        data = json.loads(payload)
        if not isinstance(data, dict):
            raise RevisionConflict("Evento operacional inválido; consulte o estado canônico.")
        status = data.get("status", current_status)
        revision = data.get("revision")
        if (
            not isinstance(status, str)
            or status not in _STATUSES[entity]
            or type(revision) is not int
            or not 1 <= revision <= current_revision
        ):
            raise RevisionConflict("Evento operacional inválido; consulte o estado canônico.")
        usage = None
        if entity == "usage_entry":
            record = unit.usage_entries.get(identity)
            historical_reserve = status == "reserved"
            if not historical_reserve and (status != record.status or revision != record.revision):
                raise RevisionConflict("Liquidação não corresponde ao evento canônico.")
            usage = UsageView(
                status="reserved" if historical_reserve else record.status,
                source=record.source,
                usage_kind=None if historical_reserve else record.usage_kind,
                reserved_tokens=record.reserved_tokens,
                counted_tokens=record.reserved_tokens
                if historical_reserve
                else record.counted_tokens(),
                input_tokens=None if historical_reserve else record.input_tokens,
                output_tokens=None if historical_reserve else record.output_tokens,
                total_tokens=None if historical_reserve else record.total_tokens,
                estimate_method="chars_div_3_v1"
                if record.estimate_method == "chars_div_3_v1"
                else "unknown",
            )
        return ActivityItem(
            id=event_id,
            seq=seq,
            occurred_at=stamp,
            entity_type=entity,
            entity_id=identity,
            task_id=task,
            task_title=title,
            run_id=run,
            action_id=action,
            status=status,
            revision=revision,
            error_code=error
            if revision == current_revision and status == current_status and error in _PUBLIC_ERRORS
            else None,
            environment=EnvironmentView(
                kind="textual"
                if metadata and json.loads(metadata).get("execution_kind") == "text_task"
                else "unassociated"
            ),
            usage=usage,
        )

    def list(
        self,
        agent_id,
        *,
        task_id=None,
        run_id=None,
        since=None,
        until=None,
        window_seconds=None,
        before=None,
        snapshot_seq=None,
        limit=50,
        now=None,
    ) -> ActivityPage:
        agent_id = UUID(str(agent_id))
        task_id = UUID(str(task_id)) if task_id is not None else None
        run_id = UUID(str(run_id)) if run_id is not None else None
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("Limite de página inválido.")
        if before is not None and (
            type(before) is not int
            or not 1 <= before <= 2**63 - 1
            or snapshot_seq is None
            or since is None
            or until is None
        ):
            raise ValueError("Paginação exige cursor e snapshot.")
        if snapshot_seq is not None and (
            type(snapshot_seq) is not int or not 0 <= snapshot_seq <= 2**63 - 1
        ):
            raise ValueError("Snapshot inválido.")
        if window_seconds is not None and (
            type(window_seconds) is not int
            or window_seconds not in (86400, 604800, 2592000)
            or since is not None
            or until is not None
            or before is not None
        ):
            raise ValueError("Janela deve ser exclusiva e de 24 horas, 7 dias ou 30 dias.")
        if run_id is not None and task_id is None:
            raise ValueError("Execução exige tarefa explícita.")
        with self.store.transaction(write=False) as unit:
            connection = unit.tasks._context.connection
            snapshot = observe(connection, now=now)
            if unit.agents.get(agent_id) is None:
                raise NotFoundError("Abelha não encontrada.")
            if task_id is not None:
                task = unit.tasks.get(task_id)
                if task is None or task.agent_id != agent_id:
                    raise NotFoundError("Tarefa não encontrada nesta abelha.")
            if run_id is not None:
                run = unit.runs.get(run_id)
                if run is None or run.task_id != task_id:
                    raise NotFoundError("Execução não encontrada nesta tarefa.")
            if snapshot_seq is not None and snapshot_seq != snapshot.event_seq:
                raise RevisionConflict("Atividade mudou; atualize a primeira página.")
            until = until or snapshot.observed_at
            since = since or until - timedelta(seconds=window_seconds or 86400)
            if any(value.tzinfo is None or value.utcoffset() is None for value in (since, until)):
                raise ValueError("Intervalo exige fuso explícito.")
            since, until = since.astimezone(UTC), until.astimezone(UTC)
            if since >= until or until > snapshot.observed_at or until - since > timedelta(days=31):
                raise ValueError("Intervalo deve ser passado, crescente e de até 31 dias.")
            interval = Interval(since=since, until=until)
            rows = self._events(connection, agent_id, task_id, run_id, interval, limit, before)
            return ActivityPage(
                agent_id=agent_id,
                snapshot=snapshot,
                scope=Scope(task_id=task_id, run_id=run_id),
                interval=interval,
                usage=usage_totals(
                    connection, agent_id, since, until, task_id=task_id, run_id=run_id
                ),
                operations=self._operations(connection, agent_id, task_id, run_id),
                items=[self._item(unit, row) for row in rows[:limit]],
                next_before=rows[limit - 1][0] if len(rows) > limit else None,
            )
