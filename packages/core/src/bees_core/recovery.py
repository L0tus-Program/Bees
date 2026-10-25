"""Read-only canonical inventory, not executor inspection or recovery authority."""

from typing import Literal, get_args
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from bees_core.models import ActionStatus, TaskStatus, utc_now
from bees_core.storage.database import Database
from bees_core.storage.store import NotFoundError


class RecoveryCounts(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    prepared: int = Field(default=0, ge=0)
    awaiting_approval: int = Field(default=0, ge=0)
    ready: int = Field(default=0, ge=0)
    dispatch_started: int = Field(default=0, ge=0)
    confirmed: int = Field(default=0, ge=0)
    failed_no_effect: int = Field(default=0, ge=0)
    outcome_unknown: int = Field(default=0, ge=0)
    cancelled: int = Field(default=0, ge=0)
    unknown_acknowledged: int = Field(default=0, ge=0)
    unknown_unacknowledged: int = Field(default=0, ge=0)
    total: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def coherent(self):
        if self.total != sum(getattr(self, key) for key in get_args(ActionStatus)):
            raise ValueError("Invalid recovery totals")
        if self.outcome_unknown != self.unknown_acknowledged + self.unknown_unacknowledged:
            raise ValueError("Invalid unknown totals")
        return self


class RecoveryInventory(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    agent_id: UUID
    task_id: UUID
    task_revision: int = Field(ge=1, strict=True)
    task_status: TaskStatus
    observed_at: AwareDatetime
    scope: Literal["all_canonical_task_history"] = "all_canonical_task_history"
    executor_evidence: Literal["not_inspected"] = "not_inspected"
    runs_total: int = Field(ge=0, strict=True)
    actions: RecoveryCounts
    model_calls: RecoveryCounts


class RecoveryAction(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: UUID
    run_id: UUID
    revision: int = Field(ge=1, strict=True)
    tool_name: str = Field(min_length=1, max_length=200)
    status: ActionStatus
    created_at: AwareDatetime
    updated_at: AwareDatetime
    unknown_acknowledged: bool = Field(strict=True)
    # Estado da fila do servidor para o executor (não é inspeção do executor).
    executor_command: (
        Literal["queued", "sent", "confirmed", "failed", "unknown", "expired"] | None
    ) = None
    executor_code: str | None = Field(default=None, max_length=64)


class RecoveryActionPage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    agent_id: UUID
    task_id: UUID
    task_revision: int = Field(ge=1, strict=True)
    observed_at: AwareDatetime
    scope: Literal["all_canonical_task_actions"] = "all_canonical_task_actions"
    executor_evidence: Literal["not_inspected"] = "not_inspected"
    items: list[RecoveryAction]
    has_more: bool = Field(strict=True)
    next_cursor: UUID | None


class RecoveryInventoryService:
    """One query-only snapshot; never open or initialize executor-owned stores."""

    def __init__(self, database: Database):
        self.database = database

    @staticmethod
    def _counts(connection, task_id: str, *, models: bool) -> RecoveryCounts:
        # Both table names are trusted constants, never supplied by a caller.
        table = "model_calls" if models else "actions"
        rows = connection.execute(
            f"SELECT x.status, COUNT(*), "
            "SUM(CASE WHEN x.status='outcome_unknown' "
            "AND x.unknown_acknowledged_at IS NOT NULL THEN 1 ELSE 0 END) "
            f"FROM {table} x JOIN runs r ON r.id=x.run_id "
            "WHERE r.task_id=? GROUP BY x.status",
            (task_id,),
        )
        counts = dict.fromkeys(get_args(ActionStatus), 0)
        acknowledged = 0
        for status, count, ack in rows:
            if status not in counts:
                raise ValueError("Invalid recovery status")
            counts[status] = count
            acknowledged += ack
        return RecoveryCounts(
            **counts,
            unknown_acknowledged=acknowledged,
            unknown_unacknowledged=counts["outcome_unknown"] - acknowledged,
            total=sum(counts.values()),
        )

    def inspect(self, agent_id: UUID | str, task_id: UUID | str) -> RecoveryInventory:
        agent, task = str(UUID(str(agent_id))), str(UUID(str(task_id)))
        with self.database.transaction(write=False) as connection:
            row = connection.execute(
                "SELECT revision,status FROM tasks WHERE id=? AND agent_id=?", (task, agent)
            ).fetchone()
            if row is None:
                raise NotFoundError("Task not found")
            return RecoveryInventory(
                agent_id=UUID(agent),
                task_id=UUID(task),
                task_revision=row[0],
                task_status=row[1],
                observed_at=utc_now(),
                runs_total=connection.execute(
                    "SELECT COUNT(*) FROM runs WHERE task_id=?", (task,)
                ).get,
                actions=self._counts(connection, task, models=False),
                model_calls=self._counts(connection, task, models=True),
            )

    def actions(
        self,
        agent_id: UUID | str,
        task_id: UUID | str,
        *,
        after: UUID | str | None = None,
        limit: int = 50,
    ) -> RecoveryActionPage:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise ValueError("Invalid recovery page limit")
        agent, task = str(UUID(str(agent_id))), str(UUID(str(task_id)))
        cursor = str(UUID(str(after))) if after is not None else None
        with self.database.transaction(write=False) as connection:
            row = connection.execute(
                "SELECT revision FROM tasks WHERE id=? AND agent_id=?", (task, agent)
            ).fetchone()
            if row is None:
                raise NotFoundError("Task not found")
            clauses, parameters = ["r.task_id=?"], [task]
            if cursor is not None:
                reference = connection.execute(
                    "SELECT x.created_at,x.id FROM actions x JOIN runs r ON r.id=x.run_id "
                    "WHERE r.task_id=? AND x.id=?",
                    (task, cursor),
                ).fetchone()
                if reference is None:
                    raise NotFoundError("Action not found")
                clauses.append("(x.created_at>? OR (x.created_at=? AND x.id>?))")
                parameters.extend([reference[0], reference[0], reference[1]])
            parameters.append(limit + 1)
            queue = connection.execute(
                "SELECT 1 FROM sqlite_schema WHERE type='table' AND name='executor_commands'"
            ).fetchone()
            command = (
                "c.status,c.error_code FROM actions x LEFT JOIN executor_commands c ON c.id=x.id "
                if queue
                else "NULL,NULL FROM actions x "
            )
            records = connection.execute(
                "SELECT x.id,x.run_id,x.revision,x.tool_name,x.status,x.created_at,x.updated_at,"
                "x.unknown_acknowledged_at IS NOT NULL," + command
                + f"JOIN runs r ON r.id=x.run_id WHERE {' AND '.join(clauses)} "
                "ORDER BY x.created_at,x.id LIMIT ?",
                tuple(parameters),
            ).fetchall()
            items = [
                RecoveryAction(
                    id=record[0],
                    run_id=record[1],
                    revision=record[2],
                    tool_name=record[3],
                    status=record[4],
                    created_at=record[5],
                    updated_at=record[6],
                    unknown_acknowledged=bool(record[7]),
                    executor_command=record[8],
                    executor_code=(record[9] or None) and str(record[9])[:64],
                )
                for record in records[:limit]
            ]
            has_more = len(records) > limit
            return RecoveryActionPage(
                agent_id=UUID(agent),
                task_id=UUID(task),
                task_revision=row[0],
                observed_at=utc_now(),
                items=items,
                has_more=has_more,
                next_cursor=items[-1].id if has_more else None,
            )
