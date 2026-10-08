"""Journal local DELETE/FULL, inicialização explícita e lock nativo por plano.

Não é o banco do core nem o estado bh_ do diagnóstico. Perda/partial nunca inicializa
automaticamente. Operação ambígua conserva a evidência, sem repetir comando Windows.
"""

import json
import os
import re
import sqlite3
import threading
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
SCHEMA = (
    "CREATE TABLE binding(id INTEGER PRIMARY KEY CHECK(id=1),"
    "journal_id TEXT NOT NULL,claim_json TEXT NOT NULL)",
    "CREATE TABLE operations(operation TEXT PRIMARY KEY,"
    "request_id TEXT NOT NULL UNIQUE,effect_request_id TEXT,"
    "status TEXT NOT NULL CHECK(status IN ('prepared','authority_started','authorized',"
    "'dispatch_started','result_observed','confirmed','unknown')),"
    "pid INTEGER,start_ticks INTEGER,"
    "result_json TEXT,publish_id TEXT NOT NULL UNIQUE)",
)


def _unique_json(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("invalid")
        result[key] = value
    return result


def _uuid(value):
    if type(value) is not str or not UUID(value).int or str(UUID(value)) != value:
        raise ValueError("invalid")


def _positive(value):
    if type(value) is not int or not 0 < value <= 2**63 - 1:
        raise ValueError("invalid")


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
            for statement in SCHEMA:
                connection.execute(statement)
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

    @classmethod
    def open_read_only(cls, directory: Path):
        """Leitor interno offline; nunca recupera, repete ou altera uma operação."""
        return cls(directory, _read_only=True)

    def __init__(self, directory: Path, *, _read_only: bool = False):
        self._read_only = _read_only
        self.directory = Path(directory).absolute() if _read_only else Path(directory)
        private(self.directory, directory=True)
        self.path = self.directory / "journal.sqlite3"
        self._locked = False
        self._owner_thread = None
        self.connection = None
        if self._read_only:
            self._files_read_only()
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
        try:
            marker_path = self.directory / "identity.json"
            if marker_path.stat().st_size > 1024:
                raise ProvisionError("provision_state_invalid")
            marker = json.loads(
                marker_path.read_bytes(),
                **({"object_pairs_hook": _unique_json} if self._read_only else {}),
            )
            if (
                set(marker) != {"format", "journal_id", "plan_hash", "plan_id"}
                or type(marker["format"]) is not int
                or marker["format"] != 1
            ):
                raise ProvisionError("provision_state_invalid")
            UUID(marker["journal_id"])
            if self._read_only:
                for name in ("journal_id", "plan_id"):
                    _uuid(marker[name])
                if type(marker["plan_hash"]) is not str or not re.fullmatch(
                    r"[0-9a-f]{64}", marker["plan_hash"]
                ):
                    raise ValueError("invalid")
            self._marker = marker
            self.journal_id = UUID(marker["journal_id"])
            self.connection = sqlite3.connect(
                self.path.as_uri() + ("?mode=ro" if self._read_only else "?mode=rw"),
                uri=True,
                timeout=2,
                isolation_level=None,
            )
            if self._read_only:
                self._files_read_only()
                self._validate_read_only()
                return
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
        except (
            sqlite3.Error,
            OSError,
            TypeError,
            ValueError,
            KeyError,
            ValidationError,
            RecursionError,
        ):
            if self.connection is not None:
                self.connection.close()
            raise ProvisionError("provision_state_invalid") from None
        except BaseException:
            if self.connection is not None:
                self.connection.close()
            raise

    def close(self):
        if self._read_only and self._locked:
            raise ProvisionError("provision_lock_required")
        self.connection.close()

    def _assert_writable(self):
        if self._read_only:
            raise ProvisionError("provision_read_only")

    def assert_locked(self):
        if not self._locked or self._owner_thread != threading.get_ident():
            raise ProvisionError("provision_lock_required")

    def _files_read_only(self):
        try:
            if not self.directory.exists():
                raise ProvisionError("provision_state_missing")
            private(self.directory, directory=True)
            expected = {"identity.json", "owner.lock", "journal.sqlite3"}
            for name in expected:
                path = self.directory / name
                if not path.exists():
                    raise ProvisionError("provision_state_missing")
                private(path)
            if {path.name for path in self.directory.iterdir()} != expected:
                raise ProvisionError("provision_state_invalid")
            if (
                self.path.stat().st_size > 8 * 1024**2
                or (self.directory / "owner.lock").stat().st_size != 1
            ):
                raise ProvisionError("provision_state_invalid")
            with self.path.open("rb") as handle:
                header = handle.read(100)
            if (
                len(header) != 100
                or header[:16] != b"SQLite format 3\x00"
                or header[18:20] != b"\x01\x01"
            ):
                raise ProvisionError("provision_state_invalid")
        except OSError:
            raise ProvisionError("provision_state_invalid") from None

    def _validate_read_only(self):
        """Formato1 estrito, inclusive fases parciais legítimas e claim evolutivo."""
        try:
            self._files_read_only()
            marker = self.directory / "identity.json"
            if (
                marker.stat().st_size > 1024
                or json.loads(marker.read_bytes(), object_pairs_hook=_unique_json) != self._marker
            ):
                raise ValueError("invalid")
            if (
                self.connection.execute("PRAGMA application_id").fetchone()[0] != APPLICATION_ID
                or self.connection.execute("PRAGMA user_version").fetchone()[0] != 1
                or self.connection.execute("PRAGMA journal_mode").fetchone()[0] != "delete"
                or self.connection.execute("PRAGMA integrity_check").fetchall() != [("ok",)]
                or self.connection.execute("PRAGMA foreign_key_check").fetchall()
            ):
                raise ValueError("invalid")
            schema = self.connection.execute(
                "SELECT sql FROM sqlite_master WHERE sql IS NOT NULL ORDER BY sql"
            ).fetchall()
            if [row[0] for row in schema] != sorted(SCHEMA):
                raise ValueError("invalid")
            binding = self.connection.execute("SELECT * FROM binding").fetchall()
            if (
                len(binding) != 1
                or binding[0][0] != 1
                or binding[0][1] != self._marker["journal_id"]
            ):
                raise ValueError("invalid")
            json.loads(binding[0][2], object_pairs_hook=_unique_json)
            claim = Claim.model_validate_json(binding[0][2])
            _positive(claim.revision)
            _positive(claim.generation)
            if (
                claim.plan_hash != self._marker["plan_hash"]
                or str(claim.plan_id) != self._marker["plan_id"]
            ):
                raise ValueError("invalid")
            if hasattr(self, "claim"):
                fixed = (
                    "claim_id",
                    "installation_id",
                    "host_id",
                    "provisioner_id",
                    "plan_id",
                    "plan_hash",
                    "owner_id",
                    "generation",
                    "plan",
                )
                if (
                    any(getattr(claim, name) != getattr(self.claim, name) for name in fixed)
                    or claim.revision < self.claim.revision
                    or claim.lease_expires_at < self.claim.lease_expires_at
                ):
                    raise ValueError("invalid")
            cursor = self.connection.cursor()
            cursor.row_factory = sqlite3.Row
            try:
                rows = cursor.execute("SELECT * FROM operations").fetchall()
            finally:
                cursor.close()
            result = self._checked_read_only_operations(rows, claim)
            self._files_read_only()
            self.claim = claim
            return result
        except (
            sqlite3.Error,
            OSError,
            ValueError,
            TypeError,
            KeyError,
            ValidationError,
            RecursionError,
        ):
            raise ProvisionError("provision_state_invalid") from None

    def _checked_read_only_operations(self, rows, claim):
        result = {row["operation"]: dict(row) for row in rows}
        if len(rows) > len(OPERATIONS) or set(result) != set(OPERATIONS[: len(rows)]):
            raise ValueError("invalid")
        requests, publications, effects = set(), set(), set()
        vm_id = None
        for index, operation in enumerate(OPERATIONS[: len(rows)]):
            row = result[operation]
            status = row["status"]
            if status not in {
                "prepared",
                "authority_started",
                "authorized",
                "dispatch_started",
                "result_observed",
                "confirmed",
                "unknown",
            } or (index < len(rows) - 1 and status != "confirmed"):
                raise ValueError("invalid")
            _uuid(row["request_id"])
            _uuid(row["publish_id"])
            if row["request_id"] in requests or row["publish_id"] in publications:
                raise ValueError("invalid")
            requests.add(row["request_id"])
            publications.add(row["publish_id"])
            effect = row["effect_request_id"]
            if effect is not None:
                _uuid(effect)
                if effect in effects:
                    raise ValueError("invalid")
                effects.add(effect)
            has_pid = row["pid"] is not None
            if has_pid != (row["start_ticks"] is not None):
                raise ValueError("invalid")
            if has_pid:
                _positive(row["pid"])
                _positive(row["start_ticks"])
            has_result = row["result_json"] is not None
            if (has_pid and effect is None) or (has_result and not has_pid):
                raise ValueError("invalid")
            if status in {"prepared", "authority_started"} and (
                effect is not None or has_pid or has_result
            ):
                raise ValueError("invalid")
            if status == "authorized" and (effect is None or has_pid or has_result):
                raise ValueError("invalid")
            if status == "dispatch_started" and (not has_pid or has_result):
                raise ValueError("invalid")
            if status in {"result_observed", "confirmed"} and not has_result:
                raise ValueError("invalid")
            if has_result:
                json.loads(row["result_json"], object_pairs_hook=_unique_json)
                receipt = ReceiptResult.model_validate_json(row["result_json"])
                if not receipt.verified or (receipt.vm_id is not None and not receipt.vm_id.int):
                    raise ValueError("invalid")
                if operation == "create_vhd":
                    if receipt.vm_id is not None:
                        raise ValueError("invalid")
                else:
                    if receipt.vm_id is None or (vm_id is not None and receipt.vm_id != vm_id):
                        raise ValueError("invalid")
                    vm_id = receipt.vm_id
                if operation == "verify":
                    if (
                        receipt.cpu_count != claim.plan.cpu_count
                        or receipt.memory_bytes != claim.plan.memory_bytes
                        or receipt.disk_bytes != claim.plan.disk_bytes
                        or receipt.powered_off is not True
                        or receipt.network_none is not True
                        or receipt.image_iso_sha256 != claim.plan.image_iso_sha256
                    ):
                        raise ValueError("invalid")
                elif any(
                    getattr(receipt, name) is not None
                    for name in (
                        "cpu_count",
                        "memory_bytes",
                        "disk_bytes",
                        "powered_off",
                        "network_none",
                        "image_iso_sha256",
                    )
                ):
                    raise ValueError("invalid")
        if requests & publications or publications & effects:
            raise ValueError("invalid")
        for row in result.values():
            if row["effect_request_id"] in requests - {row["request_id"]}:
                raise ValueError("invalid")
        return result

    @contextmanager
    def lock(self):
        if self._locked:
            raise ProvisionError("provision_lock_required")
        if self._read_only:
            self._files_read_only()
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
            self._owner_thread = threading.get_ident()
            try:
                if self._read_only:
                    self._validate_read_only()
                yield
            finally:
                self._locked = False
                self._owner_thread = None
                if os.name == "nt":
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    @contextmanager
    def transaction(self):
        self._assert_writable()
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
        if self._read_only:
            return self._validate_read_only()
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
        self._assert_writable()
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
