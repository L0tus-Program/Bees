"""Atenção local durável; fatos e leitura humana não concedem autorização."""

import hashlib
import json
from datetime import datetime
from typing import Literal
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from pydantic import BaseModel, ConfigDict, Field

from bees_core.models import Action, Approval, ModelCall, Run, Task, utc_now
from bees_core.routine_results import quiet_completion
from bees_core.storage.database import Database
from bees_core.storage.store import NotFoundError, RevisionConflict, StateStore

AttentionKind = Literal["task_completed", "task_failed", "approval_required", "outcome_unknown"]
KINDS: tuple[AttentionKind, ...] = (
    "task_completed",
    "task_failed",
    "approval_required",
    "outcome_unknown",
)


class _Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)


class ReadInput(_Contract):
    client_request_id: UUID
    expected_revision: int = Field(ge=1, strict=True)
    read: bool = Field(strict=True)


class PreferenceInput(_Contract):
    client_request_id: UUID
    expected_revision: int = Field(ge=0, strict=True)
    enabled: bool = Field(strict=True)


class AttentionItem(_Contract):
    id: UUID
    agent_id: UUID
    task_id: UUID
    task_title: str = Field(min_length=1, max_length=200)
    approval_id: UUID | None
    kind: AttentionKind
    created_at: datetime
    revision: int
    read: bool
    requires_attention: bool
    muted: bool


class AttentionPage(_Contract):
    items: list[AttentionItem]
    next_before: int | None
    unread_count: int


class ConversationAttentionPage(AttentionPage):
    conversation_id: UUID


class AttentionConversation(_Contract):
    id: UUID
    title: str = Field(max_length=200)
    created_at: datetime


class AttentionConversations(_Contract):
    agent_id: UUID
    items: list[AttentionConversation]
    next_offset: int | None


class AttentionPreference(_Contract):
    kind: AttentionKind
    enabled: bool
    revision: int


def project_event(connection, record, previous, event_id: UUID) -> None:
    """Chamado pelo writer do ledger no mesmo savepoint; sem evento recursivo.

    A identidade do fato elimina atualizações repetidas. Action de aprovação e
    ModelCall da mesma geração desconhecida usam a mesma identidade de ModelCall.
    """
    kind = None
    approval_id = None
    entity = None
    entity_id = record.id
    task_id = None
    if isinstance(record, Task) and record.status in ("completed", "failed"):
        if previous is not None and previous.status == record.status:
            return
        kind = "task_completed" if record.status == "completed" else "task_failed"
        task_id, agent_id, entity = record.id, record.agent_id, "task"
        run = connection.execute(
            "SELECT id FROM runs WHERE task_id=? ORDER BY created_at DESC,id DESC LIMIT 1",
            (str(record.id),),
        ).fetchone()
        if run is not None:
            entity_id, entity = run[0], "run"
            if record.status == "completed" and quiet_completion(connection, record, run[0]):
                return
    elif isinstance(record, Run) and record.status in ("paused", "failed") and record.error:
        if record.error in (
            "policy_approval_required",
            "approval_denied",
            "outcome_unknown",
            "global_stop",
        ):
            return
        if record.status != "failed" and record.checkpoint.get("attention_required") is not True:
            return
        kind, entity, entity_id, task_id = "task_failed", "run", record.id, record.task_id
        row = connection.execute(
            "SELECT agent_id FROM tasks WHERE id=?", (str(record.task_id),)
        ).fetchone()
        if row is not None:
            agent_id = row[0]
    elif isinstance(record, Approval) and record.status == "pending" and previous is None:
        kind, entity, approval_id = "approval_required", "approval", record.id
        row = connection.execute(
            "SELECT r.task_id,t.agent_id FROM actions a JOIN runs r ON r.id=a.run_id "
            "JOIN tasks t ON t.id=r.task_id WHERE a.id=?",
            (str(record.action_id),),
        ).fetchone()
        if row is not None:
            task_id, agent_id = row
    elif isinstance(record, (ModelCall, Action)) and record.status == "outcome_unknown":
        if previous is not None and previous.status == record.status:
            return
        kind = "outcome_unknown"
        entity = "model_call" if isinstance(record, ModelCall) else "action"
        if isinstance(record, Action) and record.tool_name == "model":
            linked = connection.execute(
                "SELECT id FROM model_calls WHERE id=? AND run_id=?",
                (str(record.metadata.get("model_call_id", "")), str(record.run_id)),
            ).fetchone()
            if linked is not None:
                entity_id, entity = linked[0], "model_call"
        row = connection.execute(
            "SELECT r.task_id,t.agent_id FROM runs r JOIN tasks t ON t.id=r.task_id WHERE r.id=?",
            (str(record.run_id),),
        ).fetchone()
        if row is not None:
            task_id, agent_id = row
    if kind is None or task_id is None:
        return
    stamp = utc_now().isoformat(timespec="microseconds")
    connection.execute(
        "INSERT INTO attention_items(id,agent_id,task_id,approval_id,kind,entity_type,"
        "entity_id,source_event_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(kind,entity_type,entity_id) DO NOTHING",
        (
            str(uuid4()),
            str(agent_id),
            str(task_id),
            str(approval_id) if approval_id else None,
            kind,
            entity,
            str(entity_id),
            str(event_id),
            stamp,
            stamp,
        ),
    )


_SELECT = (
    "SELECT i.seq,i.id,i.agent_id,i.task_id,i.approval_id,i.kind,i.created_at,"
    "i.revision,i.is_read,COALESCE(p.enabled,1),t.title FROM attention_items i "
    "JOIN tasks t ON t.id=i.task_id "
    "LEFT JOIN attention_preferences p ON p.agent_id=i.agent_id AND p.kind=i.kind "
)


class AttentionService:
    def __init__(self, database: Database) -> None:
        self.database = database
        self.store = StateStore(database)

    @staticmethod
    def _owned(unit, agent_id):
        agent_id = UUID(str(agent_id))
        if unit.agents.get(agent_id) is None:
            raise NotFoundError("Abelha não encontrada.")
        return agent_id, unit.tasks._context.connection

    @staticmethod
    def _conversation(unit, agent_id, conversation_id):
        if conversation_id is None:
            return None
        conversation_id = UUID(str(conversation_id))
        conversation = unit.conversations.get(conversation_id)
        if conversation is None or conversation.agent_id != agent_id:
            raise NotFoundError("Conversa não encontrada nesta abelha.")
        return conversation_id

    def conversations(self, agent_id: UUID, *, limit=50, offset=0) -> AttentionConversations:
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("Página deve ter entre 1 e 100 itens.")
        if type(offset) is not int or not 0 <= offset <= 2**63 - 1:
            raise ValueError("Posição inválida.")
        with self.store.transaction(write=False) as unit:
            agent_id, connection = self._owned(unit, agent_id)
            rows = list(
                connection.execute(
                    "SELECT id,title,created_at FROM conversations WHERE agent_id=? "
                    "ORDER BY created_at DESC,id DESC LIMIT ? OFFSET ?",
                    (str(agent_id), limit + 1, offset),
                )
            )
            return AttentionConversations(
                agent_id=agent_id,
                items=[
                    AttentionConversation(id=row[0], title=row[1][:200], created_at=row[2])
                    for row in rows[:limit]
                ],
                next_offset=offset + limit if len(rows) > limit else None,
            )

    def _item(self, unit, row) -> AttentionItem:
        requires = True
        if row[5] == "approval_required":
            # Importação local evita ciclo com o writer de persistência. validity
            # consulta regras/snapshots, sem resolver credencial, rede ou escrita.
            from bees_core.approvals import ApprovalService

            approval = unit.approvals.get(row[4])
            task = unit.tasks.get(row[3])
            action = unit.actions.get(approval.action_id) if approval else None
            if action is not None and action.metadata.get("approval_kind") == "resource_tool":
                from bees_core.resource_tools import ResourceToolService

                service = ResourceToolService(self.database)
            elif action is not None and action.metadata.get("approval_kind") == "agent_tool":
                from bees_core.agent_tools.service import AgentToolService

                service = AgentToolService(self.database)
            else:
                service = ApprovalService(self.database)
            requires = (
                approval is not None
                and approval.status == "pending"
                and task is not None
                and task.status == "waiting_approval"
                and service.validity(unit, approval) is None
            )
        return AttentionItem(
            id=row[1],
            agent_id=row[2],
            task_id=row[3],
            task_title=row[10],
            approval_id=row[4],
            kind=row[5],
            created_at=row[6],
            revision=row[7],
            read=bool(row[8]),
            requires_attention=requires,
            muted=not bool(row[9]),
        )

    def list(
        self,
        agent_id: UUID,
        *,
        limit: int = 50,
        before: int | None = None,
        conversation_id: UUID | None = None,
    ) -> AttentionPage | ConversationAttentionPage:
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("Página deve ter entre 1 e 100 itens.")
        if before is not None and (type(before) is not int or not 1 <= before <= 2**63 - 1):
            raise ValueError("Cursor inválido.")
        with self.store.transaction(write=False) as unit:
            agent_id, connection = self._owned(unit, agent_id)
            conversation_id = self._conversation(unit, agent_id, conversation_id)
            where = "WHERE i.agent_id=? "
            parameters = [str(agent_id)]
            if conversation_id is not None:
                where += "AND t.conversation_id=? "
                parameters.append(str(conversation_id))
                if (
                    before is not None
                    and connection.execute(
                        "SELECT 1 FROM attention_items i JOIN tasks t ON t.id=i.task_id "
                        + where
                        + "AND i.seq=?",
                        (*parameters, before),
                    ).get
                    is None
                ):
                    raise NotFoundError("Página não encontrada nesta conversa.")
            scope_where, scope_parameters = where, tuple(parameters)
            if before is not None:
                where += "AND i.seq<? "
                parameters.append(before)
            rows = list(
                connection.execute(
                    _SELECT + where + "ORDER BY i.seq DESC LIMIT ?",
                    (*parameters, limit + 1),
                )
            )
            count = connection.execute(
                "SELECT count(*) FROM attention_items i JOIN tasks t ON t.id=i.task_id "
                "LEFT JOIN attention_preferences p ON p.agent_id=i.agent_id AND p.kind=i.kind "
                + scope_where
                + "AND i.is_read=0 AND COALESCE(p.enabled,1)=1 AND i.kind<>'approval_required'",
                scope_parameters,
            ).get
            # Apenas decisões pendentes entram neste conjunto; o histórico grande
            # de conclusões/falhas é contado diretamente pelo SQLite.
            candidates = connection.execute(
                _SELECT
                + "JOIN approvals a ON a.id=i.approval_id "
                + scope_where
                + "AND i.is_read=0 "
                "AND COALESCE(p.enabled,1)=1 AND a.status='pending' "
                "AND t.status='waiting_approval'",
                scope_parameters,
            )
            count += sum(self._item(unit, row).requires_attention for row in candidates)
            page = dict(
                items=[self._item(unit, row) for row in rows[:limit]],
                next_before=rows[limit - 1][0] if len(rows) > limit else None,
                unread_count=count,
            )
            return (
                ConversationAttentionPage(**page, conversation_id=conversation_id)
                if conversation_id is not None
                else AttentionPage(**page)
            )

    def preferences(self, agent_id: UUID) -> dict:
        with self.store.transaction(write=False) as unit:
            agent_id, connection = self._owned(unit, agent_id)
            return {
                "agent_id": str(agent_id),
                "items": [
                    self._preference(connection, agent_id, kind).model_dump(mode="json")
                    for kind in KINDS
                ],
            }

    @staticmethod
    def _preference(connection, agent_id, kind) -> AttentionPreference:
        row = connection.execute(
            "SELECT enabled,revision FROM attention_preferences WHERE agent_id=? AND kind=?",
            (str(agent_id), kind),
        ).fetchone()
        return AttentionPreference(
            kind=kind, enabled=bool(row[0]) if row else True, revision=row[1] if row else 0
        )

    @staticmethod
    def _replay(connection, agent_id, target, value) -> tuple[bool, str]:
        digest = hashlib.sha256(
            json.dumps(
                value.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
            ).encode()
        ).hexdigest()
        row = connection.execute(
            "SELECT agent_id,target,request_hash FROM attention_commands WHERE client_request_id=?",
            (str(value.client_request_id),),
        ).fetchone()
        if row is not None and row != (str(agent_id), target, digest):
            raise RevisionConflict("UUID de atenção já utilizado para outra intenção.")
        return row is not None, digest

    @staticmethod
    def _audit(connection, agent_id, target, value, digest, entity, entity_id, payload):
        stamp = utc_now().isoformat(timespec="microseconds")
        connection.execute(
            "INSERT INTO attention_commands VALUES(?,?,?,?,?,?)",
            (str(value.client_request_id), str(agent_id), target, digest, stamp, "user"),
        )
        connection.execute(
            "INSERT INTO domain_events(id,created_at,entity_type,entity_id,event_type,"
            "payload_json,actor,source,correlation_id) VALUES(?,?,?,?,?,?,?,?,?)",
            (
                str(uuid4()),
                stamp,
                entity,
                str(entity_id),
                "updated",
                json.dumps(payload),
                "user",
                "attention_human",
                str(value.client_request_id),
            ),
        )

    def set_read(
        self,
        agent_id: UUID,
        item_id: UUID,
        value: ReadInput,
        *,
        conversation_id: UUID | None = None,
    ) -> AttentionItem:
        value = ReadInput.model_validate(value.model_dump())
        with self.store.transaction(actor="user", source="attention_read") as unit:
            agent_id, connection = self._owned(unit, agent_id)
            conversation_id = self._conversation(unit, agent_id, conversation_id)
            scope = " AND t.conversation_id=?" if conversation_id is not None else ""
            parameters = (str(agent_id), str(item_id)) + (
                (str(conversation_id),) if conversation_id is not None else ()
            )
            row = connection.execute(
                _SELECT + "WHERE i.agent_id=? AND i.id=?" + scope, parameters
            ).fetchone()
            if row is None:
                raise NotFoundError("Aviso não encontrado nesta abelha.")
            target = "read:" + str(item_id)
            replay, digest = self._replay(connection, agent_id, target, value)
            if not replay:
                if row[7] != value.expected_revision:
                    raise RevisionConflict("Aviso mudou; atualize antes de editar.")
                connection.execute(
                    "UPDATE attention_items SET is_read=?,revision=revision+1,updated_at=? "
                    "WHERE id=? AND revision=?",
                    (int(value.read), utc_now().isoformat(), str(item_id), value.expected_revision),
                )
                self._audit(
                    connection,
                    agent_id,
                    target,
                    value,
                    digest,
                    "attention",
                    item_id,
                    {
                        "revision": value.expected_revision + 1,
                        "read": value.read,
                        "previous_read": bool(row[8]),
                    },
                )
            current = connection.execute(
                _SELECT + "WHERE i.agent_id=? AND i.id=?", (str(agent_id), str(item_id))
            ).fetchone()
            return self._item(unit, current)

    def set_preference(
        self, agent_id: UUID, kind: AttentionKind, value: PreferenceInput
    ) -> AttentionPreference:
        value = PreferenceInput.model_validate(value.model_dump())
        if kind not in KINDS:
            raise ValueError("Tipo de aviso inválido.")
        with self.store.transaction(actor="user", source="attention_preference") as unit:
            agent_id, connection = self._owned(unit, agent_id)
            current = self._preference(connection, agent_id, kind)
            target = "preference:" + kind
            replay, digest = self._replay(connection, agent_id, target, value)
            if not replay:
                if current.revision != value.expected_revision:
                    raise RevisionConflict("Preferência mudou; atualize antes de editar.")
                stamp = utc_now().isoformat(timespec="microseconds")
                if current.revision == 0:
                    connection.execute(
                        "INSERT INTO attention_preferences VALUES(?,?,?,?,?,?)",
                        (str(agent_id), kind, int(value.enabled), 1, stamp, stamp),
                    )
                else:
                    connection.execute(
                        "UPDATE attention_preferences SET enabled=?,"
                        "revision=revision+1,updated_at=? "
                        "WHERE agent_id=? AND kind=? AND revision=?",
                        (int(value.enabled), stamp, str(agent_id), kind, current.revision),
                    )
                identity = uuid5(NAMESPACE_URL, f"bees:attention:{agent_id}:{kind}")
                self._audit(
                    connection,
                    agent_id,
                    target,
                    value,
                    digest,
                    "attention_preference",
                    identity,
                    {
                        "agent_id": str(agent_id),
                        "kind": kind,
                        "revision": current.revision + 1,
                        "enabled": value.enabled,
                        "previous_enabled": current.enabled,
                    },
                )
            return self._preference(connection, agent_id, kind)
