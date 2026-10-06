"""Integridade, migrações e falhas usando arquivos SQLite reais."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from importlib import resources
from pathlib import Path

import apsw
import pytest

from bees_core.storage import database as database_module
from bees_core.storage.database import (
    Database,
    MigrationError,
    UnsafeSQLiteError,
    check_sqlite_runtime,
)

NOW = "2026-10-04T12:00:00+00:00"


def add_agent(connection: apsw.Connection, agent_id: str = "agent-1") -> None:
    connection.execute(
        "INSERT INTO agents(id,name,created_at,updated_at) VALUES(?,?,?,?)",
        (agent_id, agent_id, NOW, NOW),
    )


def local_migrations(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Copia arquivos SQL reais para testar upgrades sem alterar arquivos do projeto."""
    target = tmp_path / "migration-files"
    target.mkdir()
    for file in resources.files("bees_core.storage.migrations").iterdir():
        if file.name.endswith(".sql"):
            (target / file.name).write_bytes(file.read_bytes())
    monkeypatch.setattr(database_module.resources, "files", lambda package: target)
    return target


@pytest.mark.parametrize("version", ["3.50.4", "3.51.2", "3.52.0", "garbage", "3.53"])
def test_rejects_unsafe_runtime(version: str) -> None:
    with pytest.raises(UnsafeSQLiteError):
        check_sqlite_runtime(version)


def test_safe_runtime_and_actual_driver() -> None:
    assert check_sqlite_runtime("3.51.3") == "3.51.3"
    assert check_sqlite_runtime() == apsw.sqlitelibversion()


def test_runtime_guard_precedes_filesystem_writes(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(database_module.apsw, "sqlitelibversion", lambda: "3.50.4")
    path = tmp_path / "private" / "bees.sqlite3"
    with pytest.raises(UnsafeSQLiteError):
        Database(path).initialize()
    assert not path.parent.exists()


def test_initialization_is_idempotent_and_survives_reopen(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "private" / "bees.sqlite3"
    database = Database(path)
    database.initialize()
    with database.transaction() as connection:
        add_agent(connection)
    reopened = Database(path)
    reopened.initialize()
    assert reopened.schema_version() == 6
    with reopened.transaction(write=False) as connection:
        assert connection.execute("SELECT name FROM agents WHERE id='agent-1'").get == "agent-1"
        assert connection.execute("SELECT count(*) FROM schema_migrations").get == 6
        assert connection.execute("PRAGMA integrity_check").get == "ok"
    assert not list(path.parent.glob("*.backup-*.sqlite3"))


def test_every_connection_validates_pragmas(tmp_path: Path) -> None:
    database = Database(tmp_path / "bees.sqlite3", busy_timeout_ms=123)
    database.initialize()
    for _ in range(2):
        connection = database.connect()
        try:
            assert connection.execute("PRAGMA journal_mode").get == "wal"
            assert connection.execute("PRAGMA synchronous").get == 2
            assert connection.execute("PRAGMA foreign_keys").get == 1
            assert connection.execute("PRAGMA busy_timeout").get == 123
        finally:
            connection.close()


def test_new_data_directories_and_files_are_private_when_supported(tmp_path: Path) -> None:
    import os

    database = Database(tmp_path / "private" / "bees.sqlite3")
    database.initialize()
    if os.name != "nt":
        assert database.path.parent.stat().st_mode & 0o777 == 0o700
        assert database.path.stat().st_mode & 0o777 == 0o600


def test_transaction_rolls_back_and_owns_connection(tmp_path: Path) -> None:
    database = Database(tmp_path / "bees.sqlite3")
    database.initialize()
    with pytest.raises(ValueError, match="falha controlada"):
        with database.transaction() as connection:
            add_agent(connection)
            raise ValueError("falha controlada")
    with pytest.raises(apsw.ConnectionClosedError):
        connection.execute("SELECT 1")
    with database.transaction(write=False) as check:
        assert check.execute("SELECT count(*) FROM agents").get == 0


def test_read_transaction_cannot_write(tmp_path: Path) -> None:
    database = Database(tmp_path / "bees.sqlite3")
    database.initialize()
    with database.transaction(write=False) as connection:
        with pytest.raises(apsw.ReadOnlyError):
            add_agent(connection)


def test_foreign_keys_json_status_and_revisions_are_enforced(tmp_path: Path) -> None:
    database = Database(tmp_path / "bees.sqlite3")
    database.initialize()
    with database.transaction() as connection:
        with pytest.raises(apsw.ConstraintError):
            connection.execute(
                "INSERT INTO conversations(id,agent_id,created_at,updated_at) VALUES(?,?,?,?)",
                ("conversation-1", "missing", NOW, NOW),
            )
        add_agent(connection)
        for column, value in [
            ("status", "inexistente"),
            ("metadata_json", "[1]"),
            ("model_config_json", "not-json"),
            ("revision", 0),
        ]:
            with pytest.raises(apsw.ConstraintError):
                connection.execute(f"UPDATE agents SET {column}=? WHERE id='agent-1'", (value,))


def test_cross_agent_and_cross_task_links_are_rejected(tmp_path: Path) -> None:
    database = Database(tmp_path / "bees.sqlite3")
    database.initialize()
    with database.transaction() as connection:
        add_agent(connection)
        add_agent(connection, "agent-2")
        connection.execute(
            "INSERT INTO conversations(id,agent_id,created_at,updated_at) VALUES(?,?,?,?)",
            ("conversation-2", "agent-2", NOW, NOW),
        )
        with pytest.raises(apsw.ConstraintError):
            connection.execute(
                "INSERT INTO tasks(id,agent_id,conversation_id,title,objective,"
                "created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?)",
                ("bad-task", "agent-1", "conversation-2", "Tarefa", "Objetivo", NOW, NOW),
            )
        for task_id in ["task-1", "task-2"]:
            connection.execute(
                "INSERT INTO tasks(id,agent_id,title,objective,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?)",
                (task_id, "agent-1", "Tarefa", "Objetivo", NOW, NOW),
            )
        connection.execute(
            "INSERT INTO runs(id,task_id,created_at,updated_at) VALUES(?,?,?,?)",
            ("run-1", "task-1", NOW, NOW),
        )
        with pytest.raises(apsw.ConstraintError):
            connection.execute(
                "INSERT INTO artifacts(id,task_id,run_id,name,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?)",
                ("bad-artifact", "task-2", "run-1", "Arquivo", NOW, NOW),
            )
        with pytest.raises(apsw.ConstraintError):
            connection.execute(
                "INSERT INTO memories(id,agent_id,task_id,scope,content,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?)",
                ("bad-memory", "agent-2", "task-1", "task", "Dado", NOW, NOW),
            )


def test_policy_approval_integrity_and_scope_identity_are_enforced(tmp_path: Path) -> None:
    database = Database(tmp_path / "bees.sqlite3")
    database.initialize()
    with database.transaction() as connection:
        for agent_id in ["agent-1", "agent-2"]:
            add_agent(connection, agent_id)
        connection.execute(
            "INSERT INTO tasks(id,agent_id,title,objective,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?)",
            ("task-1", "agent-1", "Tarefa", "Objetivo", NOW, NOW),
        )
        connection.execute(
            "INSERT INTO runs(id,task_id,created_at,updated_at) VALUES(?,?,?,?)",
            ("run-1", "task-1", NOW, NOW),
        )
        connection.execute(
            "INSERT INTO actions(id,run_id,tool_name,created_at,updated_at) VALUES(?,?,?,?,?)",
            ("action-1", "run-1", "file.read", NOW, NOW),
        )
        for policy_id, agent_id in [("policy-2", "agent-2"), ("global", None)]:
            connection.execute(
                "INSERT INTO policies(id,agent_id,name,effect,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?)",
                (policy_id, agent_id, "Regra", "allow", NOW, NOW),
            )
        with pytest.raises(apsw.ConstraintError, match="another agent"):
            connection.execute(
                "INSERT INTO approvals(id,action_id,policy_id,created_at,updated_at) "
                "VALUES(?,?,?,?,?)",
                ("approval-1", "action-1", "policy-2", NOW, NOW),
            )
        connection.execute(
            "INSERT INTO approvals(id,action_id,policy_id,created_at,updated_at) VALUES(?,?,?,?,?)",
            ("approval-1", "action-1", "global", NOW, NOW),
        )
        with pytest.raises(apsw.ConstraintError, match="another agent"):
            connection.execute("UPDATE approvals SET policy_id='policy-2' WHERE id='approval-1'")
        with pytest.raises(apsw.ConstraintError, match="immutable"):
            connection.execute("UPDATE tasks SET agent_id='agent-2' WHERE id='task-1'")
        with pytest.raises(apsw.ConstraintError, match="immutable"):
            connection.execute("UPDATE policies SET agent_id='agent-1' WHERE id='global'")


def test_checksum_tampering_and_unknown_revision_fail_closed(tmp_path: Path) -> None:
    database = Database(tmp_path / "bees.sqlite3")
    database.initialize()
    migrations = database._migrations()
    with database.transaction() as connection:
        connection.execute("UPDATE schema_migrations SET checksum=?", ("0" * 64,))
    with pytest.raises(MigrationError, match="Checksum"):
        database.initialize()
    with database.transaction() as connection:
        connection.execute("DELETE FROM schema_migrations")
        for migration in migrations:
            connection.execute(
                "INSERT INTO schema_migrations(version,name,checksum,applied_at) VALUES(?,?,?,?)",
                (migration.version, migration.name, migration.checksum, NOW),
            )
        connection.execute(
            "INSERT INTO schema_migrations(version,name,checksum,applied_at) VALUES(7,?,?,?)",
            ("0007_future.sql", "0" * 64, NOW),
        )
    with pytest.raises(MigrationError, match="desconhecida"):
        database.initialize()


def test_unrecognized_existing_database_is_not_overwritten(tmp_path: Path) -> None:
    path = tmp_path / "bees.sqlite3"
    connection = apsw.Connection(str(path))
    connection.execute("CREATE TABLE unrelated(value TEXT)")
    connection.close()
    with pytest.raises(MigrationError, match="sem histórico"):
        Database(path).initialize()
    connection = apsw.Connection(str(path))
    try:
        assert connection.execute("SELECT name FROM sqlite_schema WHERE name='unrelated'").get
        assert connection.execute("SELECT name FROM sqlite_schema WHERE name='agents'").get is None
    finally:
        connection.close()


def test_upgrade_creates_consistent_backup_before_migration(tmp_path: Path, monkeypatch) -> None:
    migrations = local_migrations(tmp_path, monkeypatch)
    database = Database(tmp_path / "bees.sqlite3")
    database.initialize()
    with database.transaction() as connection:
        add_agent(connection)
    (migrations / "0007_extra.sql").write_text(
        "CREATE TABLE extra (id TEXT PRIMARY KEY) STRICT;", encoding="utf-8"
    )
    database.initialize()
    assert database.schema_version() == 7
    backups = list(tmp_path.glob("*.backup-*.sqlite3"))
    assert len(backups) == 1
    backup = apsw.Connection(str(backups[0]))
    try:
        assert backup.execute("PRAGMA integrity_check").get == "ok"
        assert backup.execute("SELECT count(*) FROM agents").get == 1
        assert backup.execute("SELECT max(version) FROM schema_migrations").get == 6
        assert backup.execute("SELECT name FROM sqlite_schema WHERE name='extra'").get is None
    finally:
        backup.close()


def test_failed_upgrade_rolls_back_schema_and_history(tmp_path: Path, monkeypatch) -> None:
    migrations = local_migrations(tmp_path, monkeypatch)
    database = Database(tmp_path / "bees.sqlite3")
    database.initialize()
    with database.transaction() as connection:
        add_agent(connection)
    (migrations / "0007_first_step.sql").write_text(
        "CREATE TABLE first_step (id TEXT PRIMARY KEY) STRICT;", encoding="utf-8"
    )
    (migrations / "0008_broken.sql").write_text(
        "CREATE TABLE temporary_table (id TEXT); INSERT INTO nonexistent VALUES(1);",
        encoding="utf-8",
    )
    with pytest.raises(MigrationError, match="revertidas"):
        database.initialize()
    with database.transaction(write=False) as connection:
        assert connection.execute("SELECT max(version) FROM schema_migrations").get == 6
        assert (
            connection.execute("SELECT name FROM sqlite_schema WHERE name='first_step'").get is None
        )
        assert connection.execute("SELECT count(*) FROM agents").get == 1
        assert (
            connection.execute("SELECT name FROM sqlite_schema WHERE name='temporary_table'").get
            is None
        )
    assert len(list(tmp_path.glob("*.backup-*.sqlite3"))) == 1


def test_backup_creation_failure_preserves_original_before_upgrade(
    tmp_path: Path, monkeypatch
) -> None:
    migrations = local_migrations(tmp_path, monkeypatch)
    database = Database(tmp_path / "bees.sqlite3")
    database.initialize()
    with database.transaction() as connection:
        add_agent(connection)
    (migrations / "0007_extra.sql").write_text(
        "CREATE TABLE extra (id TEXT PRIMARY KEY) STRICT;", encoding="utf-8"
    )

    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 10, 4, 12, tzinfo=UTC)

    monkeypatch.setattr(database_module, "datetime", FixedDatetime)
    conflicting_path = tmp_path / "bees.sqlite3.backup-20261004T120000000000Z.sqlite3"
    conflicting_path.write_text("Não sobrescrever backup existente", encoding="utf-8")
    with pytest.raises(FileExistsError):
        database.initialize()
    with database.transaction(write=False) as connection:
        assert connection.execute("SELECT max(version) FROM schema_migrations").get == 6
        assert connection.execute("SELECT count(*) FROM agents").get == 1
        assert connection.execute("SELECT name FROM sqlite_schema WHERE name='extra'").get is None
    assert conflicting_path.read_text(encoding="utf-8") == "Não sobrescrever backup existente"


def test_migration_cannot_commit_its_transaction(tmp_path: Path, monkeypatch) -> None:
    migrations = local_migrations(tmp_path, monkeypatch)
    database = Database(tmp_path / "bees.sqlite3")
    database.initialize()
    (migrations / "0007_commit.sql").write_text(
        "CREATE TABLE extra (id TEXT); COMMIT; INSERT INTO nonexistent VALUES(1);",
        encoding="utf-8",
    )
    with pytest.raises(MigrationError, match="revertidas"):
        database.initialize()
    with database.transaction(write=False) as connection:
        assert connection.execute("SELECT max(version) FROM schema_migrations").get == 6
        assert connection.execute("SELECT name FROM sqlite_schema WHERE name='extra'").get is None


def test_migration_executes_statements_after_result_rows(tmp_path: Path, monkeypatch) -> None:
    migrations = local_migrations(tmp_path, monkeypatch)
    database = Database(tmp_path / "bees.sqlite3")
    database.initialize()
    (migrations / "0007_select.sql").write_text(
        "SELECT 1; CREATE TABLE extra (id TEXT PRIMARY KEY) STRICT;", encoding="utf-8"
    )
    database.initialize()
    with database.transaction(write=False) as connection:
        assert (
            connection.execute("SELECT name FROM sqlite_schema WHERE name='extra'").get == "extra"
        )
    assert database.schema_version() == 7


def test_checksum_is_portable_between_line_endings(tmp_path: Path, monkeypatch) -> None:
    migrations = local_migrations(tmp_path, monkeypatch)
    database = Database(tmp_path / "bees.sqlite3")
    database.initialize()
    sql_file = migrations / "0001_initial.sql"
    sql_file.write_bytes(sql_file.read_bytes().replace(b"\n", b"\r\n"))
    database.initialize()
    assert database.schema_version() == 6


def test_two_concurrent_initializers_apply_once(tmp_path: Path) -> None:
    path = tmp_path / "bees.sqlite3"
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda _: Database(path).initialize(), range(2)))
    database = Database(path)
    assert database.schema_version() == 6
    with database.transaction(write=False) as connection:
        assert connection.execute("SELECT count(*) FROM schema_migrations").get == 6


def test_maintenance_lock_contention_fails_with_bounded_wait(tmp_path: Path) -> None:
    database = Database(tmp_path / "bees.sqlite3", busy_timeout_ms=20)
    database.initialize()
    with database._maintenance_lock():
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(database.initialize)
            with pytest.raises(MigrationError, match="Outra manutenção"):
                future.result(timeout=2)


def test_write_contention_and_readers_use_independent_connections(tmp_path: Path) -> None:
    database = Database(tmp_path / "bees.sqlite3", busy_timeout_ms=20)
    database.initialize()
    with database.transaction() as connection:
        add_agent(connection)
        with ThreadPoolExecutor(max_workers=1) as pool:

            def competing_write() -> None:
                with database.transaction() as other:
                    add_agent(other, "agent-2")

            with pytest.raises(apsw.BusyError):
                pool.submit(competing_write).result(timeout=2)
        with database.transaction(write=False) as reader:
            assert reader.execute("SELECT count(*) FROM agents").get == 0
    with database.transaction(write=False) as reader:
        assert reader.execute("SELECT count(*) FROM agents").get == 1


@pytest.mark.parametrize("path", [":memory:", "file:bees.sqlite3?mode=memory"])
def test_rejects_ephemeral_canonical_database(path: str) -> None:
    with pytest.raises(ValueError, match="disco"):
        Database(path)
