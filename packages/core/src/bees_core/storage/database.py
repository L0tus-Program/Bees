"""SQLite durável, conexões independentes e migrações verificadas."""

from __future__ import annotations

import hashlib
import os
import re
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib import resources
from pathlib import Path

import apsw


class DatabaseError(RuntimeError):
    """Falha de configuração ou integridade da persistência."""


class UnsafeSQLiteError(DatabaseError):
    """Runtime SQLite não suportado pela política de durabilidade."""


class MigrationError(DatabaseError):
    """Histórico ou aplicação de migração não confiável."""


def check_sqlite_runtime(version: str | None = None) -> str:
    """Verifica o SQLite do driver utilizado, independentemente do Python instalado."""
    effective = apsw.sqlitelibversion() if version is None else version
    if not re.fullmatch(r"\d+\.\d+\.\d+", effective):
        raise UnsafeSQLiteError("Versão do SQLite inválida; não abrir o estado canônico.")
    parts = tuple(int(part) for part in effective.split("."))
    if parts < (3, 51, 3) or parts == (3, 52, 0):
        raise UnsafeSQLiteError(
            f"SQLite {effective} não suportado: use versão corrigida >= 3.51.3, "
            "exceto 3.52.0 retirada."
        )
    return effective


@dataclass(frozen=True)
class _Migration:
    version: int
    name: str
    checksum: str
    sql: str


class Database:
    """Uma base em disco; nenhum objeto Connection é compartilhado entre chamadas.

    initialize exige serviço quiescido. O lock de manutenção coordena migradores
    cooperantes; não confina processos externos que abram o arquivo diretamente.
    """

    def __init__(self, path: str | Path, busy_timeout_ms: int = 5000) -> None:
        if str(path) == ":memory:" or str(path).startswith("file:"):
            raise ValueError("A base canônica exige caminho de arquivo local em disco.")
        if not isinstance(busy_timeout_ms, int) or busy_timeout_ms < 0:
            raise ValueError("busy_timeout_ms deve ser inteiro não negativo.")
        self.path = Path(path).expanduser().resolve()
        self.busy_timeout_ms = busy_timeout_ms

    def _prepare_directory(self) -> None:
        missing: list[Path] = []
        parent = self.path.parent
        while not parent.exists():
            missing.append(parent)
            parent = parent.parent
        for directory in reversed(missing):
            directory.mkdir(mode=0o700, exist_ok=True)
            if os.name != "nt":
                directory.chmod(0o700)

    def connect(self) -> apsw.Connection:
        """O chamador possui a conexão retornada e precisa fechá-la."""
        check_sqlite_runtime()
        connection = apsw.Connection(str(self.path))
        try:
            if os.name != "nt":
                self.path.chmod(0o600)
            connection.set_busy_timeout(self.busy_timeout_ms)
            if connection.execute("PRAGMA journal_mode=WAL").get != "wal":
                raise DatabaseError("Não foi possível ativar SQLite WAL.")
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute("PRAGMA foreign_keys=ON")
            if connection.execute("PRAGMA synchronous").get != 2:
                raise DatabaseError("SQLite sem synchronous=FULL.")
            if connection.execute("PRAGMA foreign_keys").get != 1:
                raise DatabaseError("SQLite sem validação de foreign keys.")
            if connection.execute("PRAGMA busy_timeout").get != self.busy_timeout_ms:
                raise DatabaseError("Timeout SQLite diferente do configurado.")
            return connection
        except BaseException:
            connection.close()
            raise

    @contextmanager
    def transaction(self, write: bool = True) -> Iterator[apsw.Connection]:
        """Cria, confirma/reverte e fecha a própria conexão, inclusive em falhas."""
        connection = self.connect()
        try:
            if not write:
                connection.execute("PRAGMA query_only=ON")
            connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            try:
                yield connection
                connection.execute("COMMIT")
            except BaseException:
                if connection.in_transaction:
                    connection.execute("ROLLBACK")
                raise
        finally:
            connection.close()

    def _migrations(self) -> list[_Migration]:
        migration_directory = resources.files("bees_core.storage.migrations")
        migrations: list[_Migration] = []
        for file in migration_directory.iterdir():
            if not file.name.endswith(".sql"):
                continue
            match = re.fullmatch(r"(\d{4})_([a-z0-9_]+)\.sql", file.name)
            if match is None:
                raise MigrationError(f"Nome de migração inválido: {file.name}.")
            # Windows/Linux devem reconhecer a mesma revisão distribuída pelo Git.
            raw = file.read_bytes().replace(b"\r\n", b"\n")
            migrations.append(
                _Migration(
                    int(match[1]), file.name, hashlib.sha256(raw).hexdigest(), raw.decode("utf-8")
                )
            )
        migrations.sort(key=lambda item: item.version)
        if not migrations or [item.version for item in migrations] != list(
            range(1, len(migrations) + 1)
        ):
            raise MigrationError("Migrações precisam ser numeradas consecutivamente a partir de 1.")
        return migrations

    @contextmanager
    def _maintenance_lock(self) -> Iterator[None]:
        lock_path = self.path.with_name(f"{self.path.name}.maintenance.lock")
        descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        with os.fdopen(descriptor, "r+b", buffering=0) as lock:
            if os.name != "nt":
                lock_path.chmod(0o600)
            if lock.seek(0, os.SEEK_END) == 0:
                lock.write(b"0")
            deadline = time.monotonic() + self.busy_timeout_ms / 1000
            while True:
                lock.seek(0)
                try:
                    if os.name == "nt":
                        import msvcrt

                        msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl

                        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError as error:
                    if time.monotonic() >= deadline:
                        raise MigrationError("Outra manutenção está usando esta base.") from error
                    time.sleep(min(0.05, max(0, deadline - time.monotonic())))
            try:
                yield
            finally:
                lock.seek(0)
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    def _applied_migrations(self, connection: apsw.Connection) -> list[tuple[int, str, str]]:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_schema WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        }
        if "schema_migrations" not in tables:
            if tables:
                raise MigrationError("Base existente sem histórico de migrações reconhecido.")
            return []
        try:
            return list(
                connection.execute(
                    "SELECT version, name, checksum FROM schema_migrations ORDER BY version"
                )
            )
        except apsw.Error as error:
            raise MigrationError("Histórico de migrações inválido.") from error

    @staticmethod
    def _validate_history(
        applied: list[tuple[int, str, str]], migrations: list[_Migration]
    ) -> None:
        expected = {item.version: item for item in migrations}
        if [row[0] for row in applied] != list(range(1, len(applied) + 1)):
            raise MigrationError("Histórico de migrações contém lacunas ou ordem inválida.")
        for version, name, checksum in applied:
            migration = expected.get(version)
            if migration is None:
                raise MigrationError(f"Revisão de banco desconhecida: {version}.")
            if name != migration.name or checksum != migration.checksum:
                raise MigrationError(f"Checksum/nome de migração alterado: {version}.")

    def _backup(self, source: apsw.Connection) -> Path:
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        backup_path = self.path.with_name(f"{self.path.name}.backup-{stamp}.sqlite3")
        descriptor = os.open(backup_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(descriptor)
        destination = apsw.Connection(str(backup_path))
        try:
            with destination.backup("main", source, "main") as backup:
                backup.step(-1)
            if destination.execute("PRAGMA quick_check").get != "ok":
                raise MigrationError("Backup anterior à migração não passou na verificação.")
        except BaseException:
            destination.close()
            backup_path.unlink(missing_ok=True)
            raise
        destination.close()
        return backup_path

    @staticmethod
    def _execute_migration(connection: apsw.Connection, sql: str) -> None:
        def authorize(action: int, *_: str | None) -> int:
            if action in {
                apsw.SQLITE_TRANSACTION,
                apsw.SQLITE_SAVEPOINT,
                apsw.SQLITE_ATTACH,
                apsw.SQLITE_DETACH,
                apsw.SQLITE_PRAGMA,
            }:
                return apsw.SQLITE_DENY
            return apsw.SQLITE_OK

        # SQL versionado não pode encerrar a transação nem desativar constraints.
        connection.set_authorizer(authorize)
        try:
            # APSW avança múltiplos statements na iteração, inclusive após SELECT.
            for _ in connection.execute(sql):
                pass
        finally:
            connection.set_authorizer(None)

    def initialize(self) -> None:
        """Valida histórico e aplica todas as pendências em uma transação exclusiva."""
        check_sqlite_runtime()
        migrations = self._migrations()
        self._prepare_directory()
        with self._maintenance_lock():
            existed = self.path.exists() and self.path.stat().st_size > 0
            connection = self.connect()
            try:
                applied = self._applied_migrations(connection)
                self._validate_history(applied, migrations)
                pending = migrations[len(applied) :]
                if not pending:
                    return
                if existed:
                    self._backup(connection)
                connection.execute("BEGIN EXCLUSIVE")
                try:
                    # Revalida após adquirir a exclusão SQL, além do lock de arquivo.
                    current = self._applied_migrations(connection)
                    self._validate_history(current, migrations)
                    if current != applied:
                        raise MigrationError("Histórico mudou durante a manutenção.")
                    connection.execute(
                        "CREATE TABLE IF NOT EXISTS schema_migrations ("
                        "version INTEGER PRIMARY KEY CHECK(version > 0), "
                        "name TEXT NOT NULL, checksum TEXT NOT NULL CHECK(length(checksum)=64), "
                        "applied_at TEXT NOT NULL) STRICT"
                    )
                    for migration in pending:
                        self._execute_migration(connection, migration.sql)
                        connection.execute(
                            "INSERT INTO schema_migrations(version,name,checksum,applied_at) "
                            "VALUES(?,?,?,?)",
                            (
                                migration.version,
                                migration.name,
                                migration.checksum,
                                datetime.now(UTC).isoformat(),
                            ),
                        )
                    if list(connection.execute("PRAGMA foreign_key_check")):
                        raise MigrationError("Migração produziu vínculos inválidos.")
                    connection.execute("COMMIT")
                except BaseException:
                    if connection.in_transaction:
                        connection.execute("ROLLBACK")
                    raise
            except apsw.Error as error:
                raise MigrationError(
                    "Falha SQLite ao inicializar a base; migrações revertidas."
                ) from error
            finally:
                connection.close()

    def schema_version(self) -> int:
        """Retorna apenas revisão de esquema, nunca conteúdo de produto."""
        with self.transaction(write=False) as connection:
            applied = self._applied_migrations(connection)
            self._validate_history(applied, self._migrations())
            return applied[-1][0] if applied else 0
