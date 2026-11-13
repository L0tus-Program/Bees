"""Journal local de comandos: replay devolve o recibo; início sem recibo vira desconhecido."""

import json
import math
import sqlite3
import stat
import threading
import time
from pathlib import Path


class JournalStateError(RuntimeError):
    """Histórico local inválido: preservar evidência, sem reconstrução automática."""


def _validate(path: Path) -> bool:
    """Valida em leitura antes de qualquer DDL ou recuperação de started."""
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return False
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_size == 0:
        raise ValueError
    connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        if connection.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
            raise ValueError
        columns = connection.execute("PRAGMA table_info(commands)").fetchall()
        expected = [
            (0, "id", "TEXT", 0, None, 1),
            (1, "tool", "TEXT", 1, None, 0),
            (2, "status", "TEXT", 1, None, 0),
            (3, "result", "TEXT", 0, None, 0),
            (4, "started_at", "REAL", 1, None, 0),
            (5, "finished_at", "REAL", 0, None, 0),
        ]
        if columns != expected:
            raise ValueError
        for command_id, tool, status, result, started, finished in connection.execute(
            "SELECT id,tool,status,result,started_at,finished_at FROM commands"
        ):
            if (
                not isinstance(command_id, str)
                or not command_id
                or not isinstance(tool, str)
                or not tool
                or status not in ("started", "done", "unknown")
                or not isinstance(started, (int, float))
                or not math.isfinite(started)
                or (
                    finished is not None
                    and (not isinstance(finished, (int, float)) or not math.isfinite(finished))
                )
            ):
                raise ValueError
            if status == "done":
                if not isinstance(result, str) or not isinstance(json.loads(result), dict):
                    raise ValueError
            elif result is not None:
                raise ValueError
    finally:
        connection.close()
    return True


class Journal:
    def __init__(self, path: Path):
        self.path = path
        self.lock = threading.Lock()
        connection = None
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            existing = _validate(path)
            # mode=rw não recria um arquivo existente que desapareceu após a validação.
            connection = sqlite3.connect(
                path.resolve().as_uri() + ("?mode=rw" if existing else "?mode=rwc"),
                uri=True,
                check_same_thread=False,
                isolation_level=None,
            )
            connection.execute("PRAGMA journal_mode=DELETE")
            connection.execute("PRAGMA synchronous=FULL")
            if not existing:
                connection.execute(
                    "CREATE TABLE commands(id TEXT PRIMARY KEY, tool TEXT NOT NULL, "
                    "status TEXT NOT NULL CHECK(status IN ('started','done','unknown')), "
                    "result TEXT, started_at REAL NOT NULL, finished_at REAL)"
                )
            # Um processo anterior interrompido não comprova ausência nem conclusão do efeito.
            connection.execute(
                "UPDATE commands SET status='unknown', finished_at=? WHERE status='started'",
                (time.time(),),
            )
        except (OSError, sqlite3.Error, ValueError, RecursionError):
            if connection is not None:
                connection.close()
            raise JournalStateError("executor_journal_invalid") from None
        self.connection = connection

    def begin(self, command_id: str, tool: str):
        """Retorna o recibo conhecido ou None quando o efeito pode começar agora."""
        with self.lock:
            row = self.connection.execute(
                "SELECT status,result FROM commands WHERE id=?", (command_id,)
            ).fetchone()
            if row is not None:
                status, result = row
                if status == "done":
                    return json.loads(result)
                return {"status": "unknown", "code": "executor_replay_unknown"}
            self.connection.execute(
                "INSERT INTO commands(id,tool,status,started_at) VALUES(?,?,'started',?)",
                (command_id, tool, time.time()),
            )
            return None

    def finish(self, command_id: str, receipt: dict) -> None:
        with self.lock:
            self.connection.execute(
                "UPDATE commands SET status='done', result=?, finished_at=? "
                "WHERE id=? AND status='started'",
                (json.dumps(receipt, ensure_ascii=False), time.time(), command_id),
            )

    def close(self):
        self.connection.close()
