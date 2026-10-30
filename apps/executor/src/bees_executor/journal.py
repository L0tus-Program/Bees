"""Journal local de comandos: replay devolve o recibo; início sem recibo vira desconhecido."""

import json
import sqlite3
import threading
import time
from pathlib import Path


class Journal:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.lock = threading.Lock()
        self.connection = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self.connection.execute("PRAGMA journal_mode=DELETE")
        self.connection.execute("PRAGMA synchronous=FULL")
        self.connection.execute(
            "CREATE TABLE IF NOT EXISTS commands(id TEXT PRIMARY KEY, tool TEXT NOT NULL, "
            "status TEXT NOT NULL CHECK(status IN ('started','done','unknown')), "
            "result TEXT, started_at REAL NOT NULL, finished_at REAL)"
        )
        # Um processo anterior interrompido não comprova ausência nem conclusão do efeito.
        self.connection.execute(
            "UPDATE commands SET status='unknown', finished_at=? WHERE status='started'",
            (time.time(),),
        )

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
