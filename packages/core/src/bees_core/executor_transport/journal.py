"""Journal privado stdlib do recurso próprio; nenhum acesso à base do core.

Formato 1, DELETE/FULL, dono exclusivo por processo. Abrir conserva intenção;
start interrompido vira unknown. Receipts/unknown nunca voltam ao executor.
"""

import json
import os
import sqlite3
import stat
from contextlib import closing, contextmanager
from pathlib import Path
from uuid import uuid4

from .contracts import TransportError, canonical, canonical_uuid, decode, digest, exact

APPLICATION_ID = 0x42455831
MAX_ACTIONS = 10000


def _safe_parents(path):
    # A configuração é confiável, mas não deve redirecionar o journal por links.
    # Ancestrais compartilhados como /tmp não precisam de modo privado.
    try:
        for ancestor in reversed(Path(path).absolute().parents):
            info = ancestor.lstat()
            if not stat.S_ISDIR(info.st_mode):
                raise TransportError("executor_private_storage_required")
    except OSError:
        raise TransportError("executor_journal_missing") from None


def private(path, *, directory=False):
    if os.name != "posix":
        raise TransportError("executor_platform_unavailable")
    _safe_parents(path)
    try:
        info = Path(path).lstat()
    except OSError:
        raise TransportError("executor_journal_missing") from None
    if (
        info.st_uid != os.geteuid()
        or info.st_mode & 0o077
        or (
            not stat.S_ISDIR(info.st_mode)
            if directory
            else not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
        )
    ):
        raise TransportError("executor_private_storage_required")


def _sync_directory(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


class Journal:
    @classmethod
    def initialize(cls, directory, identity):
        if os.name != "posix":
            raise TransportError("executor_platform_unavailable")
        directory = Path(directory).absolute()
        _safe_parents(directory)
        if not directory.exists():
            directory.mkdir(mode=0o700)
        private(directory, directory=True)
        files = [directory / name for name in ("identity.json", "journal.sqlite3", "resource.lock")]
        sidecars = [
            directory / ("journal.sqlite3" + suffix) for suffix in ("-journal", "-wal", "-shm")
        ]
        if any(path.exists() or path.is_symlink() for path in (*files, *sidecars)):
            raise TransportError("executor_journal_already_present")
        marker = {"format": 1, "journal_id": str(uuid4()), **identity.fields()}
        try:
            for path, contents in (
                (files[0], canonical(marker)),
                (files[1], b""),
                (files[2], b"0"),
            ):
                descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                with os.fdopen(descriptor, "wb") as output:
                    output.write(contents)
                    output.flush()
                    os.fsync(output.fileno())
            _sync_directory(directory)
            with closing(sqlite3.connect(files[1], isolation_level=None)) as connection:
                connection.execute("PRAGMA journal_mode=DELETE")
                connection.execute("PRAGMA synchronous=FULL")
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    "CREATE TABLE identity(id INTEGER PRIMARY KEY CHECK(id=1),"
                    "binding_hash TEXT NOT NULL,journal_id TEXT NOT NULL,"
                    "generation INTEGER NOT NULL,owner_id TEXT,run_id TEXT) STRICT"
                )
                connection.execute(
                    "CREATE TABLE actions(action_id TEXT PRIMARY KEY,"
                    "request_id TEXT NOT NULL UNIQUE,"
                    "request_hash TEXT NOT NULL,status TEXT NOT NULL CHECK(status IN "
                    "('intent','started','receipt','unknown','stopped')),response_json TEXT "
                    "CHECK(response_json IS NULL OR (json_valid(response_json) "
                    "AND json_type(response_json)='object')), "
                    "CHECK((status='receipt')=(response_json IS NOT NULL))) STRICT"
                )
                connection.execute(
                    "CREATE TABLE events(sequence INTEGER PRIMARY KEY,action_id TEXT NOT NULL "
                    "REFERENCES actions(action_id),status TEXT NOT NULL CHECK(status IN "
                    "('intent','started','receipt','unknown','stopped'))) STRICT"
                )
                connection.execute(
                    "CREATE TRIGGER action_identity BEFORE UPDATE ON actions WHEN "
                    "NEW.action_id IS NOT OLD.action_id OR NEW.request_id IS NOT OLD.request_id "
                    "OR NEW.request_hash IS NOT OLD.request_hash "
                    "BEGIN SELECT RAISE(ABORT,'executor action identity is immutable'); END"
                )
                connection.execute(
                    "CREATE TRIGGER action_transition BEFORE UPDATE ON actions WHEN NOT ("
                    "(OLD.status='intent' AND NEW.status IN ('started','stopped')) OR "
                    "(OLD.status='started' AND NEW.status IN ('receipt','unknown'))) "
                    "BEGIN SELECT RAISE(ABORT,'executor transition is invalid'); END"
                )
                connection.execute(
                    "CREATE TRIGGER action_terminal BEFORE UPDATE ON actions "
                    "WHEN OLD.status IN ('receipt','unknown','stopped') "
                    "BEGIN SELECT RAISE(ABORT,'executor settlement is final'); END"
                )
                for table in ("actions", "events"):
                    connection.execute(
                        f"CREATE TRIGGER {table}_no_delete BEFORE DELETE ON {table} "
                        "BEGIN SELECT RAISE(ABORT,'executor evidence is append-only'); END"
                    )
                connection.execute(
                    "CREATE TRIGGER events_no_update BEFORE UPDATE ON events "
                    "BEGIN SELECT RAISE(ABORT,'executor evidence is append-only'); END"
                )
                connection.execute(
                    "INSERT INTO identity VALUES(1,?,?,0,NULL,NULL)",
                    (digest(identity.fields()), marker["journal_id"]),
                )
                connection.execute(f"PRAGMA application_id={APPLICATION_ID}")
                connection.execute("PRAGMA user_version=1")
                connection.execute("COMMIT")
            _sync_directory(directory)
        except OSError, sqlite3.Error:
            # Conservar instalação parcial. Não apagar evidência nem tentar recriar.
            raise TransportError("executor_journal_unavailable") from None
        return cls(directory, identity)

    def __init__(self, directory, identity):
        self.directory = Path(directory).absolute()
        self.identity = identity
        self.connection = None
        self._lock = None
        private(self.directory, directory=True)
        import fcntl

        for name in ("identity.json", "journal.sqlite3", "resource.lock"):
            private(self.directory / name)
        for suffix in ("-journal", "-wal", "-shm"):
            extra = self.directory / ("journal.sqlite3" + suffix)
            if extra.exists() or extra.is_symlink():
                private(extra)
        try:
            self._lock = os.open(self.directory / "resource.lock", os.O_RDWR | os.O_NOFOLLOW)
            try:
                fcntl.flock(self._lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise TransportError("executor_resource_busy") from None
            marker_path = self.directory / "identity.json"
            with marker_path.open("rb") as source:
                contents = source.read(2049)
            if len(contents) > 2048:
                raise TransportError("executor_journal_invalid")
            marker = decode(contents)
            canonical_uuid(marker.get("journal_id"))
            exact(marker, {"format", "journal_id", *identity.fields()})
            if (
                type(marker["format"]) is not int
                or marker["format"] != 1
                or any(marker[key] != value for key, value in identity.fields().items())
            ):
                raise TransportError("executor_binding_mismatch")
            self.connection = sqlite3.connect(
                (self.directory / "journal.sqlite3").as_uri() + "?mode=rw",
                uri=True,
                timeout=1,
                isolation_level=None,
            )
            self.connection.execute("PRAGMA trusted_schema=OFF")
            if (
                self.connection.execute("PRAGMA user_version").fetchone() != (1,)
                or self.connection.execute("PRAGMA application_id").fetchone() != (APPLICATION_ID,)
                or self.connection.execute(
                    "SELECT binding_hash,journal_id FROM identity WHERE id=1"
                ).fetchone()
                != (digest(identity.fields()), marker["journal_id"])
            ):
                raise TransportError("executor_journal_invalid")
            if self.connection.execute("PRAGMA journal_mode=DELETE").fetchone() != ("delete",):
                raise TransportError("executor_journal_invalid")
            self.connection.execute("PRAGMA synchronous=FULL")
            self.connection.execute("PRAGMA foreign_keys=ON")
            with self.transaction():
                interrupted = self.connection.execute(
                    "SELECT action_id FROM actions WHERE status='started'"
                ).fetchall()
                for (action_id,) in interrupted:
                    self._transition(action_id, "unknown")
        except BaseException as error:
            self.close()
            if isinstance(error, (OSError, sqlite3.Error, ValueError, UnicodeError)):
                raise TransportError("executor_journal_invalid") from None
            raise

    def close(self):
        if self.connection is not None:
            self.connection.close()
            self.connection = None
        if self._lock is not None:
            os.close(self._lock)
            self._lock = None

    @contextmanager
    def transaction(self):
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            yield
            self.connection.execute("COMMIT")
        except BaseException as error:
            if self.connection.in_transaction:
                self.connection.execute("ROLLBACK")
            if isinstance(error, sqlite3.Error):
                raise TransportError("executor_journal_unavailable") from None
            raise

    def _fence(self, request):
        generation, owner, run = self.connection.execute(
            "SELECT generation,owner_id,run_id FROM identity WHERE id=1"
        ).fetchone()
        if request["generation"] < generation or (
            request["generation"] == generation
            and (request["owner_id"], request["run_id"]) != (owner, run)
        ):
            raise TransportError("executor_fenced")

    def lookup(self, request):
        row = self.connection.execute(
            "SELECT request_id,request_hash,status,response_json FROM actions WHERE action_id=?",
            (request["action_id"],),
        ).fetchone()
        if row is None:
            return None
        if (row[0], row[1]) != (request["request_id"], digest(request)):
            raise TransportError("executor_request_conflict")
        return row[2], json.loads(row[3]) if row[3] is not None else None

    def intent(self, request):
        with self.transaction():
            prior = self.lookup(request)
            if prior is not None:
                return prior
            self._fence(request)
            if self.connection.execute(
                "SELECT 1 FROM actions WHERE request_id=?",
                (request["request_id"],),
            ).fetchone():
                raise TransportError("executor_request_conflict")
            if self.connection.execute("SELECT count(*) FROM actions").fetchone()[0] >= MAX_ACTIONS:
                raise TransportError("executor_journal_limit")
            self.connection.execute(
                "INSERT INTO actions VALUES(?,?,?,'intent',NULL)",
                (request["action_id"], request["request_id"], digest(request)),
            )
            self.connection.execute(
                "INSERT INTO events(action_id,status) VALUES(?,'intent')",
                (request["action_id"],),
            )
        return None

    def _transition(self, action_id, status, response=None):
        self.connection.execute(
            "UPDATE actions SET status=?,response_json=? WHERE action_id=?",
            (status, canonical(response).decode() if response is not None else None, action_id),
        )
        self.connection.execute(
            "INSERT INTO events(action_id,status) VALUES(?,?)",
            (action_id, status),
        )

    def start(self, request):
        with self.transaction():
            if self.lookup(request) != ("intent", None):
                raise TransportError("executor_request_replay")
            self._fence(request)
            self.connection.execute(
                "UPDATE identity SET generation=?,owner_id=?,run_id=? WHERE id=1",
                (request["generation"], request["owner_id"], request["run_id"]),
            )
            self._transition(request["action_id"], "started")

    def finish(self, request, result):
        with self.transaction():
            if self.lookup(request) != ("started", None):
                raise TransportError("executor_request_replay")
            self._fence(request)
            self._transition(request["action_id"], "receipt", result)

    def stop(self, request):
        with self.transaction():
            if self.lookup(request) == ("intent", None):
                self._transition(request["action_id"], "stopped")

    def unknown(self, request):
        with self.transaction():
            if self.lookup(request) == ("started", None):
                self._transition(request["action_id"], "unknown")
