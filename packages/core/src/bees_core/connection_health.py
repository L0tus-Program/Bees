"""Conexões que precisam de ação humana, calculadas do estado atual (sem duplicatas).

Somente leitura: canais com falha, conectores MCP com erro, executores revogados ou
offline com comandos pendentes, computadores com falha e gatilhos recusados.
Nenhum segredo, argumento ou conteúdo de mensagem é exposto.
"""

from datetime import timedelta
from uuid import UUID

from bees_core.executors import ONLINE_SECONDS
from bees_core.models import utc_now
from bees_core.storage.database import Database


def _table(connection, name: str) -> bool:
    return (
        connection.execute(
            "SELECT 1 FROM sqlite_schema WHERE type='table' AND name=?", (name,)
        ).fetchone()
        is not None
    )


def connection_issues(database: Database, agent_id: UUID | str, *, now=None) -> list[dict]:
    now = now or utc_now()
    agent = str(agent_id)
    issues: list[dict] = []
    with database.transaction(write=False) as connection:
        if _table(connection, "channel_links"):
            for row in connection.execute(
                "SELECT id,name,kind,status,reason_code,updated_at FROM channel_links "
                "WHERE agent_id=? AND (status='failed' OR (status='active' AND reason_code IS NOT "
                "NULL))",
                (agent,),
            ):
                issues.append(
                    {
                        "kind": "channel",
                        "id": row[0],
                        "name": row[1],
                        "detail": row[2],
                        "code": row[4] or "channel_failed",
                        "blocking": row[3] == "failed",
                        "since": row[5],
                    }
                )
        if _table(connection, "mcp_servers"):
            for row in connection.execute(
                "SELECT id,name,reason_code,updated_at FROM mcp_servers WHERE agent_id=? "
                "AND status='failed' AND enabled=1",
                (agent,),
            ):
                issues.append(
                    {
                        "kind": "mcp",
                        "id": row[0],
                        "name": row[1],
                        "detail": None,
                        "code": row[2] or "mcp_failed",
                        "blocking": True,
                        "since": row[3],
                    }
                )
        if _table(connection, "computers"):
            for row in connection.execute(
                "SELECT c.id,c.name,c.status,c.reason_code,c.updated_at,e.status,e.last_seen_at "
                "FROM computers c JOIN executors e ON e.id=c.executor_id "
                "WHERE c.agent_id=? AND c.status IN ('failed','ready','provisioning')",
                (agent,),
            ):
                offline = (
                    row[5] != "active"
                    or not row[6]
                    or (now - _parse(row[6]) > timedelta(seconds=ONLINE_SECONDS))
                )
                if row[2] == "failed" or offline:
                    issues.append(
                        {
                            "kind": "computer",
                            "id": row[0],
                            "name": row[1],
                            "detail": None,
                            "code": row[3]
                            or ("computer_failed" if row[2] == "failed" else "runtime_offline"),
                            "blocking": True,
                            "since": row[4],
                        }
                    )
            for row in connection.execute(
                "SELECT e.id,e.name,e.status,e.last_seen_at,count(c.id) FROM executors e "
                "JOIN executor_commands c ON c.executor_id=e.id WHERE c.agent_id=? "
                "AND c.status IN ('queued','sent') GROUP BY e.id",
                (agent,),
            ):
                offline = (
                    row[2] != "active"
                    or not row[3]
                    or (now - _parse(row[3]) > timedelta(seconds=ONLINE_SECONDS))
                )
                if offline:
                    issues.append(
                        {
                            "kind": "executor",
                            "id": row[0],
                            "name": row[1],
                            "detail": str(row[4]),
                            "code": "executor_revoked"
                            if row[2] != "active"
                            else "executor_offline",
                            "blocking": True,
                            "since": row[3],
                        }
                    )
        if _table(connection, "routine_hooks"):
            for row in connection.execute(
                "SELECT h.id,r.name,h.last_result,h.last_triggered_at FROM routine_hooks h "
                "JOIN routines r ON r.id=h.routine_id WHERE h.agent_id=? AND h.status='active' "
                "AND h.last_result IS NOT NULL AND h.last_result<>'accepted'",
                (agent,),
            ):
                issues.append(
                    {
                        "kind": "webhook",
                        "id": row[0],
                        "name": row[1],
                        "detail": None,
                        "code": "hook_rejected",
                        "blocking": False,
                        "since": row[3],
                    }
                )
    return issues


def _parse(value: str):
    from datetime import datetime

    return datetime.fromisoformat(value)
