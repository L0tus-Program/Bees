"""Journal local DELETE/FULL, inicialização explícita e lock nativo por plano.

Não é o banco do core nem o estado bh_ do diagnóstico. Perda/partial nunca inicializa
automaticamente. Operação ambígua conserva a evidência, sem repetir comando Windows.
"""

import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from uuid import UUID, uuid4

from pydantic import ValidationError

from bees_host.errors import HostError
from bees_host.guest_bridge.private import check_ancestors
from bees_host.provisioning.contracts import (
    OPERATIONS,
    Claim,
    Operation,
    ProvisionError,
    ReceiptResult,
    canonical,
)
from bees_host.security import check_private

APPLICATION_ID = 0x42505231


def private(path: Path, *, directory=False):
    try:
        check_private(path, directory=directory)
        check_ancestors(path)
        if not directory and path.stat().st_nlink != 1:
            raise ProvisionError("provision_private_required")
    except HostError, OSError, RuntimeError:
        raise ProvisionError("provision_private_required") from None


def create(path: Path, data: bytes):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as target:
        check_private(path, protect=True)
        target.write(data)
        target.flush()
        os.fsync(target.fileno())


def sync_directory(path: Path):
    if os.name != "nt":
        descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


class Journal:
    @classmethod
    def initialize(cls, directory: Path, claim: Claim):
        """Somente operador confiável, com snapshot emitido pelo core, antes do runner."""
        directory = Path(directory)
        private(directory.parent, directory=True)
        if directory.exists() or directory.is_symlink():
            raise ProvisionError("provision_state_already_present")
        directory.mkdir(mode=0o700)
        check_private(directory, directory=True, protect=True)
        marker = {
            "format": 1,
            "journal_id": str(uuid4()),
            "plan_hash": claim.plan_hash,
            "plan_id": str(claim.plan_id),
        }
        connection = None
        try:
            create(directory / "identity.json", canonical(marker))
            sync_directory(directory)
            create(directory / "owner.lock", b"0")
            path = directory / "journal.sqlite3"
            create(path, b"")
            connection = sqlite3.connect(path, timeout=2, isolation_level=None)
            connection.execute("PRAGMA journal_mode=DELETE")
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "CREATE TABLE binding(id INTEGER PRIMARY KEY CHECK(id=1),"
                "journal_id TEXT NOT NULL,claim_json TEXT NOT NULL)"
            )
            connection.execute(
                "CREATE TABLE operations(operation TEXT PRIMARY KEY,"
                "request_id TEXT NOT NULL UNIQUE,effect_request_id TEXT,"
                "status TEXT NOT NULL CHECK(status IN ('prepared','authority_started','authorized',"
                "'dispatch_started','result_observed','confirmed','unknown')),"
                "pid INTEGER,start_ticks INTEGER,"
                "result_json TEXT,publish_id TEXT NOT NULL UNIQUE)"
            )
            connection.execute(
                "INSERT INTO binding VALUES(1,?,?)", (marker["journal_id"], claim.model_dump_json())
            )
            connection.execute(f"PRAGMA application_id={APPLICATION_ID}")
            connection.execute("PRAGMA user_version=1")
            connection.execute("COMMIT")
            sync_directory(directory)
        except OSError, sqlite3.Error, HostError:
            raise ProvisionError("provision_state_unavailable") from None
        finally:
            if connection is not None:
                connection.close()
        return cls(directory)

    def __init__(self, directory: Path):
        self.directory = Path(directory)
        private(self.directory, directory=True)
        self.path = self.directory / "journal.sqlite3"
        for path in (self.path, self.directory / "identity.json", self.directory / "owner.lock"):
            if not path.exists():
                raise ProvisionError("provision_state_missing")
            private(path)
        for suffix in ("-journal", "-wal", "-shm"):
            extra = Path(str(self.path) + suffix)
            if extra.exists() or extra.is_symlink():
                private(extra)
        if self.path.stat().st_size > 8 * 1024**2:
            raise ProvisionError("provision_state_invalid")
        self._locked = False
        self.connection = None
        try:
            marker_path = self.directory / "identity.json"
            if marker_path.stat().st_size > 1024:
                raise ProvisionError("provision_state_invalid")
            marker = json.loads(marker_path.read_bytes())
            if (
                set(marker) != {"format", "journal_id", "plan_hash", "plan_id"}
                or type(marker["format"]) is not int
                or marker["format"] != 1
            ):
                raise ProvisionError("provision_state_invalid")
            UUID(marker["journal_id"])
            self.connection = sqlite3.connect(
                self.path.as_uri() + "?mode=rw", uri=True, timeout=2, isolation_level=None
            )
            if (
                self.connection.execute("PRAGMA application_id").fetchone()[0] != APPLICATION_ID
                or self.connection.execute("PRAGMA user_version").fetchone()[0] != 1
            ):
                raise ProvisionError("provision_state_invalid")
            row = self.connection.execute(
                "SELECT journal_id,claim_json FROM binding WHERE id=1"
            ).fetchone()
            self.claim = Claim.model_validate_json(row[1])
            if (
                row[0] != marker["journal_id"]
                or self.claim.plan_hash != marker["plan_hash"]
                or str(self.claim.plan_id) != marker["plan_id"]
            ):
                raise ProvisionError("provision_state_invalid")
            if self.connection.execute("PRAGMA journal_mode").fetchone()[0] != "delete":
                raise ProvisionError("provision_state_invalid")
            self.connection.execute("PRAGMA synchronous=FULL")
            self.operations()
        except sqlite3.Error, OSError, TypeError, ValueError, KeyError, ValidationError:
            if self.connection is not None:
                self.connection.close()
            raise ProvisionError("provision_state_invalid") from None
        except BaseException:
            if self.connection is not None:
                self.connection.close()
            raise

    def close(self):
        self.connection.close()

    @contextmanager
    def lock(self):
        if self._locked:
            raise ProvisionError("provision_lock_required")
        path = self.directory / "owner.lock"
        private(path)
        if path.stat().st_size != 1:
            raise ProvisionError("provision_state_invalid")
        descriptor = os.open(path, os.O_RDWR | getattr(os, "O_NOFOLLOW", 0))
        os.set_inheritable(descriptor, False)
        with os.fdopen(descriptor, "r+b") as handle:
            try:
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                raise ProvisionError("provision_owner_running") from None
            self._locked = True
            try:
                yield
            finally:
                self._locked = False
                if os.name == "nt":
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    @contextmanager
    def transaction(self):
        if not self._locked:
            raise ProvisionError("provision_lock_required")
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            yield
            self.connection.execute("COMMIT")
        except BaseException as error:
            if self.connection.in_transaction:
                self.connection.execute("ROLLBACK")
            if isinstance(error, sqlite3.Error):
                raise ProvisionError("provision_state_unavailable") from None
            raise

    def operations(self):
        try:
            self.connection.row_factory = sqlite3.Row
            rows = self.connection.execute("SELECT * FROM operations").fetchall()
            if len(rows) > len(OPERATIONS) or {row["operation"] for row in rows} != set(
                OPERATIONS[: len(rows)]
            ):
                raise ProvisionError("provision_state_invalid")
            for row in rows:
                if row["status"] not in {
                    "prepared",
                    "authority_started",
                    "authorized",
                    "dispatch_started",
                    "result_observed",
                    "confirmed",
                    "unknown",
                }:
                    raise ProvisionError("provision_state_invalid")
                for name in ("request_id", "publish_id", "effect_request_id"):
                    if row[name] is not None and (
                        not UUID(row[name]).int or str(UUID(row[name])) != row[name]
                    ):
                        raise ProvisionError("provision_state_invalid")
                if row["pid"] is not None and (
                    type(row["pid"]) is not int
                    or row["pid"] <= 0
                    or type(row["start_ticks"]) is not int
                    or not 0 < row["start_ticks"] <= 2**63 - 1
                ):
                    raise ProvisionError("provision_state_invalid")
                if row["status"] == "confirmed":
                    receipt = ReceiptResult.model_validate_json(row["result_json"])
                    if not receipt.verified:
                        raise ProvisionError("provision_state_invalid")
            return {row["operation"]: dict(row) for row in rows}
        except sqlite3.Error, ValueError, TypeError, KeyError, ValidationError:
            raise ProvisionError("provision_state_invalid") from None

    def prepare(self, operation: Operation):
        existing = self.operations()
        if operation not in OPERATIONS or any(
            row["status"] != "confirmed" for row in existing.values()
        ):
            raise ProvisionError("provision_reconciliation_required")
        position = len(existing)
        if position >= len(OPERATIONS) or OPERATIONS[position] != operation:
            raise ProvisionError("provision_operation_invalid")
        with self.transaction():
            self.connection.execute(
                "INSERT INTO operations VALUES(?,?,NULL,'prepared',NULL,NULL,NULL,?)",
                (operation, str(uuid4()), str(uuid4())),
            )
        return self.operations()[operation]

    def transition(
        self,
        operation: Operation,
        source: str,
        target: str,
        *,
        effect_request_id=None,
        pid=None,
        start_ticks=None,
        result=None,
    ):
        with self.transaction():
            count = self.connection.execute(
                "UPDATE operations SET status=?,effect_request_id=COALESCE(?,effect_request_id),"
                "pid=COALESCE(?,pid),start_ticks=COALESCE(?,start_ticks),"
                "result_json=COALESCE(?,result_json) WHERE operation=? AND status=?",
                (
                    target,
                    str(effect_request_id) if effect_request_id else None,
                    pid,
                    start_ticks,
                    result.model_dump_json() if isinstance(result, ReceiptResult) else None,
                    operation,
                    source,
                ),
            ).rowcount
            if count != 1:
                raise ProvisionError("provision_transition_invalid")

    def update_claim(self, claim: Claim):
        with self.transaction():
            self.connection.execute(
                "UPDATE binding SET claim_json=? WHERE id=1", (claim.model_dump_json(),)
            )
        self.claim = claim

    def unknown(self, operation: Operation):
        with self.transaction():
            self.connection.execute(
                "UPDATE operations SET status='unknown' WHERE operation=? AND status!='confirmed'",
                (operation,),
            )
