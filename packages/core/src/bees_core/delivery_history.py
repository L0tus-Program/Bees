"""Consultas locais de entregas preservadas, sem executor, modelo ou escrita."""

import base64
import hashlib
import json
from uuid import UUID

from bees_core.models import utc_now
from bees_core.storage.database import Database
from bees_core.storage.store import NotFoundError


class DeliveryQueryError(ValueError):
    """Cursor ou filtro inválido; não contém dados da consulta."""


# Os vínculos vêm de FKs e do ID explícito do checkpoint, nunca da ordem do chat.
_RESULT = """
SELECT 'result' AS kind,m.id AS id,m.created_at AS created_at,t.agent_id AS agent_id,
 t.conversation_id AS conversation_id,t.id AS task_id,t.title AS title,r.id AS run_id,
 m.revision AS version,m.revision AS revision,r.status AS status,
 CASE WHEN json_extract(r.checkpoint_json,'$.deliverables.partial')=1 THEN 1 ELSE 0 END AS partial,
 substr(m.content,1,300) AS summary
FROM tasks t JOIN runs r ON r.task_id=t.id
 JOIN messages m ON m.id=json_extract(r.checkpoint_json,'$.result_message_id')
 AND m.conversation_id=t.conversation_id AND m.role='assistant'
WHERE t.agent_id=:agent_id AND r.status='completed'
"""
_ARTIFACT = """
SELECT 'artifact' AS kind,a.id AS id,a.created_at AS created_at,a.agent_id AS agent_id,
 a.conversation_id AS conversation_id,a.task_id AS task_id,a.name AS title,a.run_id AS run_id,
 a.version AS version,a.revision AS revision,a.status AS status,
 CASE WHEN a.status IN ('draft','failed') THEN 1 ELSE 0 END AS partial,
 '' AS summary
FROM artifacts a WHERE a.agent_id=:agent_id
"""
_COLUMNS = (
    "kind",
    "id",
    "created_at",
    "agent_id",
    "conversation_id",
    "task_id",
    "title",
    "run_id",
    "version",
    "revision",
    "status",
    "partial",
    "summary",
)


class DeliveryHistory:
    def __init__(self, database: Database) -> None:
        self.database = database

    @staticmethod
    def _scope(connection, agent_id, conversation_id, task_id):
        if not connection.execute("SELECT 1 FROM agents WHERE id=?", (str(agent_id),)).get:
            raise NotFoundError("Abelha não encontrada.")
        if (
            conversation_id
            and not connection.execute(
                "SELECT 1 FROM conversations WHERE id=? AND agent_id=?",
                (str(conversation_id), str(agent_id)),
            ).get
        ):
            raise NotFoundError("Conversa não encontrada.")
        if task_id:
            row = connection.execute(
                "SELECT conversation_id FROM tasks WHERE id=? AND agent_id=?",
                (str(task_id), str(agent_id)),
            ).fetchone()
            if row is None or conversation_id and row[0] != str(conversation_id):
                raise NotFoundError("Tarefa não encontrada.")

    def list(
        self,
        agent_id: UUID,
        *,
        query: str = "",
        conversation_id: UUID | None = None,
        task_id: UUID | None = None,
        limit: int = 50,
        cursor: str | None = None,
    ) -> dict:
        if not isinstance(query, str) or len(query) > 200 or "\x00" in query:
            raise DeliveryQueryError()
        if type(limit) is not int or not 1 <= limit <= 100:
            raise DeliveryQueryError()
        query = query.strip()
        scope = {
            "agent_id": str(agent_id),
            "query": query,
            "conversation_id": str(conversation_id) if conversation_id else None,
            "task_id": str(task_id) if task_id else None,
        }
        fingerprint = hashlib.sha256(json.dumps(scope, sort_keys=True).encode()).hexdigest()
        until = utc_now().isoformat(timespec="microseconds")
        before_date, before_key = until, "~"
        if cursor is not None:
            try:
                if not isinstance(cursor, str) or len(cursor) > 2048:
                    raise ValueError
                decoded = json.loads(base64.b64decode(cursor, altchars=b"-_", validate=True))
                if not isinstance(decoded, list) or len(decoded) != 4:
                    raise ValueError
                binding, until, before_date, before_key = decoded
                if binding != fingerprint or not all(isinstance(v, str) for v in decoded):
                    raise ValueError
                # Dates are never interpolated into SQL; also reject malformed keys.
                from datetime import datetime

                if datetime.fromisoformat(until).utcoffset() is None:
                    raise ValueError
                if datetime.fromisoformat(before_date).utcoffset() is None:
                    raise ValueError
                kind, identity = before_key.split(":", 1)
                if kind not in ("result", "artifact") or str(UUID(identity)) != identity:
                    raise ValueError
            except ValueError, TypeError, UnicodeError:
                raise DeliveryQueryError() from None
        parameters = {
            **scope,
            "until": until,
            "before_date": before_date,
            "before_key": before_key,
            "limit": limit + 1,
        }
        filters = """
 AND (:conversation_id IS NULL OR conversation_id=:conversation_id)
 AND (:task_id IS NULL OR task_id=:task_id)
 AND (:query='' OR instr(bees_casefold(title),bees_casefold(:query))>0 OR id=:query
      OR task_id=:query OR conversation_id=:query OR run_id=:query)
 AND created_at<=:until
 AND (created_at<:before_date OR (created_at=:before_date AND kind||':'||id<:before_key))
"""
        sql = "SELECT * FROM (" + _RESULT + " UNION ALL " + _ARTIFACT + ") WHERE 1=1" + filters
        sql += " ORDER BY created_at DESC,kind||':'||id DESC LIMIT :limit"
        with self.database.transaction(write=False) as connection:
            connection.create_scalar_function("bees_casefold", str.casefold, 1, deterministic=True)
            self._scope(connection, agent_id, conversation_id, task_id)
            rows = list(connection.execute(sql, parameters))
        items = [dict(zip(_COLUMNS, row, strict=True)) for row in rows[:limit]]
        for item in items:
            item["partial"] = bool(item["partial"])
        next_cursor = None
        if len(rows) > limit:
            last = items[-1]
            next_cursor = base64.urlsafe_b64encode(
                json.dumps(
                    [
                        fingerprint,
                        until,
                        last["created_at"],
                        last["kind"] + ":" + last["id"],
                    ],
                    separators=(",", ":"),
                ).encode()
            ).decode()
        return {
            "agent_id": str(agent_id),
            "scope": {key: scope[key] for key in ("query", "conversation_id", "task_id")},
            "items": items,
            "next_cursor": next_cursor,
            "limit": limit,
            "until": until,
        }

    def result(self, agent_id: UUID, message_id: UUID, run_id: UUID) -> dict:
        """Conteúdo completo por vínculo exato; nunca reproduz a execução."""
        with self.database.transaction(write=False) as connection:
            row = connection.execute(
                _RESULT + " AND m.id=:message_id AND r.id=:run_id",
                {"agent_id": str(agent_id), "message_id": str(message_id), "run_id": str(run_id)},
            ).fetchone()
            if row is None:
                raise NotFoundError("Entrega não encontrada nesta abelha e execução.")
            item = dict(zip(_COLUMNS, row, strict=True))
            item["partial"] = bool(item["partial"])
            content = connection.execute(
                "SELECT content FROM messages WHERE id=?", (str(message_id),)
            ).get
        return {"item": item, "content": content}
