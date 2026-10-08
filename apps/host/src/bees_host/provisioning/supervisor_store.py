"""Ledger privado do supervisor: dono exclusivo do host e pedidos antes da rede.

Estado local independente do core e do Journal por plano. Um término ambíguo ou
interrompido bloqueia novas corridas; abrir não recupera, repete ou apaga evidência.
O diretório é escolhido pela composição confiável, nunca por um plano remoto.
"""

import json
import os
import re
import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid4

from bees_host.errors import HostError
from bees_host.provisioning.contracts import Claim, ProvisionError, canonical
from bees_host.provisioning.enrollment import _origin
from bees_host.provisioning.journal import create, private, sync_directory
from bees_host.security import check_private

APPLICATION_ID = 0x42505331
MAX_BYTES = 8 * 1024**2
SCHEMA = (
    "CREATE TABLE binding(id INTEGER PRIMARY KEY CHECK(id=1),store_id TEXT NOT NULL,"
    "installation_id TEXT NOT NULL,host_id TEXT NOT NULL)",
    "CREATE TABLE runs(claim_id TEXT PRIMARY KEY,provisioner_id TEXT NOT NULL,"
    "plan_id TEXT NOT NULL,plan_hash TEXT NOT NULL,owner_id TEXT NOT NULL,"
    "generation INTEGER NOT NULL CHECK(generation>0),revision INTEGER NOT NULL CHECK(revision>0),"
    "lease_expires_at TEXT NOT NULL,claim_status TEXT NOT NULL CHECK(claim_status IN "
    "('claimed','dispatch_started','confirmed','outcome_unknown','aborted')),"
    "status TEXT NOT NULL CHECK(status IN ('running','completed','stopped','unknown')),"
    "created_at TEXT NOT NULL,updated_at TEXT NOT NULL)",
    "CREATE TABLE requests(request_id TEXT PRIMARY KEY,claim_id TEXT NOT NULL REFERENCES "
    "runs(claim_id),owner_id TEXT NOT NULL,generation INTEGER NOT NULL CHECK(generation>0),"
    "kind TEXT NOT NULL CHECK(kind IN ('renew','unknown')),status TEXT NOT NULL "
    "CHECK(status IN ('pending','confirmed')),revision_before INTEGER NOT NULL "
    "CHECK(revision_before>0),revision_after INTEGER,created_at TEXT NOT NULL,confirmed_at TEXT)",
    "CREATE UNIQUE INDEX one_pending_request ON requests(claim_id,kind) WHERE status='pending'",
    "CREATE INDEX requests_by_claim ON requests(claim_id,created_at,request_id)",
)
ACQUISITION_SCHEMA = (
    "CREATE TABLE acquisitions(request_id TEXT PRIMARY KEY,owner_id TEXT NOT NULL UNIQUE,"
    "plan_id TEXT NOT NULL UNIQUE,plan_hash TEXT NOT NULL,enrollment_store_id TEXT NOT NULL,"
    "issue_request_id TEXT NOT NULL,provisioner_id TEXT NOT NULL,origin TEXT NOT NULL,"
    "revision INTEGER NOT NULL CHECK(revision>0),status TEXT NOT NULL CHECK(status IN "
    "('prepared','accepted','running','completed','stopped','unknown')),"
    "claim_id TEXT UNIQUE,generation INTEGER,claim_revision INTEGER,lease_expires_at TEXT,"
    "claim_status TEXT,created_at TEXT NOT NULL,updated_at TEXT NOT NULL)",
    "CREATE UNIQUE INDEX one_open_acquisition ON acquisitions((1)) WHERE status!='completed'",
)


@dataclass(frozen=True)
class AcquisitionTicket:
    """Capacidade efêmera da composição confiável, sem segredo ou plano integral."""

    request_id: UUID
    owner_id: UUID
    plan_id: UUID
    plan_hash: str
    enrollment_store_id: UUID
    issue_request_id: UUID
    provisioner_id: UUID
    origin: str
    _token: object = field(repr=False, compare=False)


def _uuid(value):
    if type(value) is not str or not UUID(value).int or str(UUID(value)) != value:
        raise ValueError("invalid")
    return value


def _date(value):
    if type(value) is not str:
        raise ValueError("invalid")
    result = datetime.fromisoformat(value)
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("invalid")
    return result


def _positive(value):
    if type(value) is not int or not 0 < value <= 2**63 - 1:
        raise ValueError("invalid")


def _unique_json(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("invalid")
        result[key] = value
    return result


class SupervisorStore:
    @classmethod
    def initialize(cls, directory: Path, *, installation_id: UUID, host_id: UUID):
        return cls._initialize(
            directory, installation_id=installation_id, host_id=host_id, version=1
        )

    @classmethod
    def initialize_for_acquisition(cls, directory: Path, *, installation_id: UUID, host_id: UUID):
        return cls._initialize(
            directory, installation_id=installation_id, host_id=host_id, version=2
        )

    @classmethod
    def _initialize(cls, directory: Path, *, installation_id: UUID, host_id: UUID, version: int):
        if any(type(value) is not UUID or not value.int for value in (installation_id, host_id)):
            raise ProvisionError("provision_supervisor_binding_invalid")
        directory = Path(directory).absolute()
        private(directory.parent, directory=True)
        if directory.exists() or directory.is_symlink():
            raise ProvisionError("provision_state_already_present")
        directory.mkdir(mode=0o700)
        check_private(directory, directory=True, protect=True)
        marker = {
            "format": 1,
            "store_id": str(uuid4()),
            "installation_id": str(installation_id),
            "host_id": str(host_id),
        }
        connection = None
        try:
            create(directory / "identity.json", canonical(marker))
            sync_directory(directory)
            create(directory / "owner.lock", b"0")
            path = directory / "supervisor.sqlite3"
            create(path, b"")
            connection = sqlite3.connect(path, timeout=2, isolation_level=None)
            connection.execute("PRAGMA journal_mode=DELETE")
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("BEGIN IMMEDIATE")
            for statement in SCHEMA + (ACQUISITION_SCHEMA if version == 2 else ()):
                connection.execute(statement)
            connection.execute(
                "INSERT INTO binding VALUES(1,?,?,?)",
                (marker["store_id"], marker["installation_id"], marker["host_id"]),
            )
            connection.execute(f"PRAGMA application_id={APPLICATION_ID}")
            connection.execute(f"PRAGMA user_version={version}")
            connection.execute("COMMIT")
            sync_directory(directory)
        except OSError, sqlite3.Error, HostError:
            raise ProvisionError("provision_state_unavailable") from None
        finally:
            if connection is not None:
                connection.close()
        return cls.open(directory)

    @classmethod
    def open(cls, directory: Path):
        return cls(directory)

    @classmethod
    def open_read_only(cls, directory: Path):
        """Check-only interno: sem criação, migração ou recuperação de journal."""
        return cls(directory, _read_only=True)

    def __init__(self, directory: Path, *, _read_only: bool = False):
        self.directory = Path(directory).absolute()
        self.path = self.directory / "supervisor.sqlite3"
        self.connection = None
        self._locked = False
        self._owner_thread = None
        self._lock_epoch = None
        self._tickets = {}
        self._read_only = _read_only
        self._files()
        try:
            marker_path = self.directory / "identity.json"
            if marker_path.stat().st_size > 1024:
                raise ValueError("invalid")
            marker = json.loads(marker_path.read_bytes(), object_pairs_hook=_unique_json)
            if (
                set(marker) != {"format", "store_id", "installation_id", "host_id"}
                or type(marker["format"]) is not int
                or marker["format"] != 1
            ):
                raise ValueError("invalid")
            for name in ("store_id", "installation_id", "host_id"):
                _uuid(marker[name])
            self.connection = sqlite3.connect(
                self.path.as_uri() + ("?mode=ro" if self._read_only else "?mode=rw"),
                uri=True,
                timeout=2,
                isolation_level=None,
            )
            if self._read_only:
                self._files()
            self.connection.row_factory = sqlite3.Row
            self.version = self.connection.execute("PRAGMA user_version").fetchone()[0]
            if (
                self.connection.execute("PRAGMA application_id").fetchone()[0] != APPLICATION_ID
                or self.version not in {1, 2}
                or self.connection.execute("PRAGMA journal_mode").fetchone()[0] != "delete"
            ):
                raise ValueError("invalid")
            self.connection.execute("PRAGMA synchronous=FULL")
            self.connection.execute("PRAGMA foreign_keys=ON")
            self._marker = marker
            self.store_id = UUID(marker["store_id"])
            self.installation_id = UUID(marker["installation_id"])
            self.host_id = UUID(marker["host_id"])
            self._validate()
        except OSError, sqlite3.Error, ValueError, TypeError, KeyError:
            if self.connection is not None:
                self.connection.close()
            raise ProvisionError("provision_state_invalid") from None
        except ProvisionError:
            if self.connection is not None:
                self.connection.close()
            raise
        except BaseException:
            if self.connection is not None:
                self.connection.close()
            raise

    def _files(self):
        if not self.directory.exists():
            raise ProvisionError("provision_state_missing")
        private(self.directory, directory=True)
        for name in ("identity.json", "owner.lock", "supervisor.sqlite3"):
            path = self.directory / name
            if not path.exists():
                raise ProvisionError("provision_state_missing")
            private(path)
        if (
            self.path.stat().st_size > MAX_BYTES
            or (self.directory / "owner.lock").stat().st_size != 1
        ):
            raise ProvisionError("provision_state_invalid")
        allowed = {
            "identity.json",
            "owner.lock",
            "supervisor.sqlite3",
            "supervisor.sqlite3-journal",
        }
        if self._read_only:
            # mode=ro pode criar sidecars ao ler um banco WAL cujo diretório é
            # gravável. Recusar o formato WAL antes de chamar SQLite evita isso.
            try:
                with self.path.open("rb") as handle:
                    header = handle.read(100)
            except OSError:
                raise ProvisionError("provision_state_invalid") from None
            if (
                len(header) != 100
                or header[:16] != b"SQLite format 3\x00"
                or header[18:20] != b"\x01\x01"
            ):
                raise ProvisionError("provision_state_invalid")
            allowed.remove("supervisor.sqlite3-journal")
        for path in self.directory.iterdir():
            if path.name not in allowed:
                raise ProvisionError("provision_state_invalid")
            private(path)

    def _validate(self):
        if self._read_only:
            self._files()
            marker = self.directory / "identity.json"
            if (
                marker.stat().st_size > 1024
                or json.loads(marker.read_bytes(), object_pairs_hook=_unique_json) != self._marker
            ):
                raise ValueError("invalid")
            if (
                self.connection.execute("PRAGMA application_id").fetchone()[0] != APPLICATION_ID
                or self.connection.execute("PRAGMA journal_mode").fetchone()[0] != "delete"
            ):
                raise ValueError("invalid")
        if (
            self.connection.execute("PRAGMA integrity_check").fetchall()[0][0] != "ok"
            or self.connection.execute("PRAGMA foreign_key_check").fetchall()
        ):
            raise ValueError("invalid")
        schema = self.connection.execute(
            "SELECT sql FROM sqlite_master WHERE sql IS NOT NULL ORDER BY sql"
        ).fetchall()
        if self.connection.execute("PRAGMA user_version").fetchone()[0] != self.version or [
            row[0] for row in schema
        ] != sorted(SCHEMA + (ACQUISITION_SCHEMA if self.version == 2 else ())):
            raise ValueError("invalid")
        binding = self.connection.execute("SELECT * FROM binding").fetchall()
        if len(binding) != 1 or dict(binding[0]) != {
            "id": 1,
            **{key: value for key, value in self._marker.items() if key != "format"},
        }:
            raise ValueError("invalid")
        runs = {}
        for row in self.connection.execute("SELECT * FROM runs"):
            for name in ("claim_id", "provisioner_id", "plan_id", "owner_id"):
                _uuid(row[name])
            for name in ("generation", "revision"):
                _positive(row[name])
            if not re.fullmatch(r"[0-9a-f]{64}", row["plan_hash"]):
                raise ValueError("invalid")
            _date(row["lease_expires_at"])
            if _date(row["updated_at"]) < _date(row["created_at"]):
                raise ValueError("invalid")
            if row["status"] not in {"running", "completed", "stopped", "unknown"} or row[
                "claim_status"
            ] not in {"claimed", "dispatch_started", "confirmed", "outcome_unknown", "aborted"}:
                raise ValueError("invalid")
            if row["status"] == "completed" and row["claim_status"] in {
                "outcome_unknown",
                "aborted",
            }:
                raise ValueError("invalid")
            runs[row["claim_id"]] = row
        if sum(row["status"] != "completed" for row in runs.values()) > 1:
            raise ValueError("invalid")
        pending = set()
        for row in self.connection.execute("SELECT * FROM requests"):
            _uuid(row["request_id"])
            _uuid(row["claim_id"])
            _uuid(row["owner_id"])
            _positive(row["generation"])
            _positive(row["revision_before"])
            run = runs[row["claim_id"]]
            if (
                row["owner_id"] != run["owner_id"]
                or row["generation"] != run["generation"]
                or row["revision_before"] > run["revision"]
                or row["kind"] not in {"renew", "unknown"}
                or _date(row["created_at"]) < _date(run["created_at"])
            ):
                raise ValueError("invalid")
            if row["status"] == "pending":
                if (
                    row["revision_after"] is not None
                    or row["confirmed_at"] is not None
                    or run["status"] == "completed"
                    or (row["claim_id"], row["kind"]) in pending
                ):
                    raise ValueError("invalid")
                pending.add((row["claim_id"], row["kind"]))
            elif row["status"] == "confirmed":
                _positive(row["revision_after"])
                if row["kind"] == "unknown" and run["claim_status"] != "outcome_unknown":
                    raise ValueError("invalid")
                if not row["revision_before"] <= row["revision_after"] <= run["revision"] or _date(
                    row["confirmed_at"]
                ) < _date(row["created_at"]):
                    raise ValueError("invalid")
            else:
                raise ValueError("invalid")
        if self.version == 2:
            self._validate_acquisitions(runs)
        if self._read_only:
            self._files()

    def _validate_acquisitions(self, runs):
        linked = set()
        open_count = 0
        plans = {}
        for claim_id, run in runs.items():
            plans.setdefault(run["plan_id"], set()).add(claim_id)
        for row in self.connection.execute("SELECT * FROM acquisitions"):
            for name in (
                "request_id",
                "owner_id",
                "plan_id",
                "enrollment_store_id",
                "issue_request_id",
                "provisioner_id",
            ):
                _uuid(row[name])
            _positive(row["revision"])
            if type(row["origin"]) is not str or len(row["origin"]) > 128:
                raise ValueError("invalid")
            _origin(row["origin"])
            if (
                not re.fullmatch(r"[0-9a-f]{64}", row["plan_hash"])
                or _date(row["updated_at"]) < _date(row["created_at"])
                or row["status"]
                not in {"prepared", "accepted", "running", "completed", "stopped", "unknown"}
            ):
                raise ValueError("invalid")
            open_count += row["status"] != "completed"
            metadata = (
                "claim_id",
                "generation",
                "claim_revision",
                "lease_expires_at",
                "claim_status",
            )
            absent = all(row[name] is None for name in metadata)
            present = all(row[name] is not None for name in metadata)
            if not absent and not present:
                raise ValueError("invalid")
            run = None
            if present:
                _uuid(row["claim_id"])
                _positive(row["generation"])
                _positive(row["claim_revision"])
                _date(row["lease_expires_at"])
                if row["claim_status"] != "claimed":
                    raise ValueError("invalid")
                run = runs.get(row["claim_id"])
            if row["status"] == "prepared" and (not absent or row["revision"] != 1):
                raise ValueError("invalid")
            if row["status"] == "accepted" and (not present or run is not None):
                raise ValueError("invalid")
            if row["status"] in {"running", "completed"} and run is None:
                raise ValueError("invalid")
            expected_revision = {
                "prepared": 1,
                "accepted": 2,
                "running": 3,
                "completed": 4,
            }.get(row["status"], 4 if run is not None else (3 if present else 2))
            if row["revision"] != expected_revision:
                raise ValueError("invalid")
            if run is not None:
                if (
                    row["status"] != run["status"]
                    or any(
                        row[name] != run[name]
                        for name in (
                            "owner_id",
                            "plan_id",
                            "plan_hash",
                            "provisioner_id",
                            "generation",
                        )
                    )
                    or row["claim_revision"] > run["revision"]
                    or _date(row["lease_expires_at"]) > _date(run["lease_expires_at"])
                    or _date(run["created_at"]) < _date(row["created_at"])
                ):
                    raise ValueError("invalid")
                linked.add(row["claim_id"])
            elif row["status"] not in {"prepared", "accepted", "unknown", "stopped"}:
                raise ValueError("invalid")
            if plans.get(row["plan_id"], set()) - {row["claim_id"]}:
                raise ValueError("invalid")
        if open_count > 1 or any(
            run["status"] != "completed" and claim_id not in linked
            for claim_id, run in runs.items()
        ):
            raise ValueError("invalid")

    def migrate_for_acquisition(self):
        """Migration explícita: somente ledger quiescido, preservando o histórico v1."""
        self._assert_writable()
        self.assert_locked()
        if self.version != 1:
            raise ProvisionError("provision_transition_invalid")
        self._validate()
        with self._transaction():
            if (
                self.connection.execute("SELECT 1 FROM runs WHERE status!='completed'").fetchone()
                or self.connection.execute(
                    "SELECT 1 FROM requests WHERE status='pending'"
                ).fetchone()
            ):
                raise ProvisionError("provision_reconciliation_required")
            for statement in ACQUISITION_SCHEMA:
                self.connection.execute(statement)
            self.connection.execute("PRAGMA user_version=2")
        self.version = 2
        self._validate()

    def prepare_acquisition(
        self,
        *,
        enrollment_store_id: UUID,
        issue_request_id: UUID,
        provisioner_id: UUID,
        origin: str,
        plan_id: UUID,
        plan_hash: str,
    ) -> AcquisitionTicket:
        self._assert_writable()
        self.assert_locked()
        if self.version != 2:
            raise ProvisionError("provision_acquisition_required")
        try:
            if (
                any(
                    type(value) is not UUID or not value.int
                    for value in (enrollment_store_id, issue_request_id, provisioner_id, plan_id)
                )
                or type(plan_hash) is not str
                or not re.fullmatch(r"[0-9a-f]{64}", plan_hash)
                or type(origin) is not str
                or len(origin) > 128
            ):
                raise ValueError("invalid")
            _origin(origin)
        except TypeError, ValueError:
            raise ProvisionError("provision_supervisor_binding_invalid") from None
        ticket = AcquisitionTicket(
            uuid4(),
            uuid4(),
            plan_id,
            plan_hash,
            enrollment_store_id,
            issue_request_id,
            provisioner_id,
            origin,
            object(),
        )
        with self._transaction():
            self.assert_acquisition_ready()
            if (
                self.connection.execute(
                    "SELECT 1 FROM acquisitions WHERE plan_id=?", (str(plan_id),)
                ).fetchone()
                or self.connection.execute(
                    "SELECT 1 FROM runs WHERE plan_id=?", (str(plan_id),)
                ).fetchone()
            ):
                raise ProvisionError("provision_claim_reused")
            now = datetime.now(UTC).isoformat()
            self.connection.execute(
                "INSERT INTO acquisitions VALUES(?,?,?,?,?,?,?,?,1,'prepared',"
                "NULL,NULL,NULL,NULL,NULL,?,?)",
                (
                    str(ticket.request_id),
                    str(ticket.owner_id),
                    str(plan_id),
                    plan_hash,
                    str(enrollment_store_id),
                    str(issue_request_id),
                    str(provisioner_id),
                    origin,
                    now,
                    now,
                ),
            )
        self._tickets[ticket._token] = (ticket, self._lock_epoch, 1, "prepared")
        return ticket

    def _ticket(self, ticket, status):
        self.assert_locked()
        if type(ticket) is not AcquisitionTicket:
            raise ProvisionError("provision_acquisition_required")
        state = self._tickets.get(ticket._token)
        if (
            state is None
            or state[0] is not ticket
            or state[1] is not self._lock_epoch
            or state[3] != status
        ):
            raise ProvisionError("provision_reconciliation_required")
        row = self.connection.execute(
            "SELECT * FROM acquisitions WHERE request_id=?", (str(ticket.request_id),)
        ).fetchone()
        fields = (
            "request_id",
            "owner_id",
            "plan_id",
            "plan_hash",
            "enrollment_store_id",
            "issue_request_id",
            "provisioner_id",
            "origin",
        )
        if (
            row is None
            or row["revision"] != state[2]
            or row["status"] != status
            or any(str(getattr(ticket, name)) != row[name] for name in fields)
        ):
            raise ProvisionError("provision_acquisition_stale")
        return row

    def _claim_matches(self, row, claim):
        try:
            if (
                type(claim) is not Claim
                or Claim.model_validate_json(claim.model_dump_json()) != claim
                or claim.installation_id != self.installation_id
                or claim.host_id != self.host_id
                or any(
                    str(getattr(claim, name)) != row[name]
                    for name in ("provisioner_id", "owner_id", "plan_id", "plan_hash")
                )
                or claim.status != "claimed"
                or claim.lease_expires_at <= datetime.now(UTC)
            ):
                raise ValueError("invalid")
            _positive(claim.revision)
            _positive(claim.generation)
        except TypeError, ValueError:
            raise ProvisionError("provision_claim_stale") from None

    def _acquisition_transition(self, ticket, before, after, now):
        row = self._ticket(ticket, before)
        if (
            self.connection.execute(
                "UPDATE acquisitions SET status=?,revision=revision+1,updated_at=? "
                "WHERE request_id=? AND revision=? AND status=?",
                (after, now, row["request_id"], row["revision"], before),
            ).rowcount
            != 1
        ):
            raise ProvisionError("provision_acquisition_stale")
        self._tickets[ticket._token] = (ticket, self._lock_epoch, row["revision"] + 1, after)

    def _live_claim(self, claim_id: UUID, statuses):
        """Um UUID persistido não recupera a capacidade da instância que morreu."""
        self.assert_locked()
        for ticket, _, _, status in self._tickets.values():
            if status in statuses:
                row = self._ticket(ticket, status)
                if row["claim_id"] == str(claim_id):
                    return ticket, row
        raise ProvisionError("provision_reconciliation_required")

    def accept_acquisition(self, ticket: AcquisitionTicket, claim: Claim):
        with self._transaction():
            row = self._ticket(ticket, "prepared")
            try:
                self._claim_matches(row, claim)
            except ValueError:
                raise ProvisionError("provision_claim_stale") from None
            if self.connection.execute(
                "SELECT 1 FROM runs WHERE claim_id=?", (str(claim.claim_id),)
            ).fetchone():
                raise ProvisionError("provision_claim_reused")
            self._acquisition_transition(
                ticket, "prepared", "accepted", datetime.now(UTC).isoformat()
            )
            self.connection.execute(
                "UPDATE acquisitions SET claim_id=?,generation=?,claim_revision=?,"
                "lease_expires_at=?,claim_status=? WHERE request_id=?",
                (
                    str(claim.claim_id),
                    claim.generation,
                    claim.revision,
                    claim.lease_expires_at.isoformat(),
                    claim.status,
                    str(ticket.request_id),
                ),
            )

    def fail_acquisition(
        self, ticket: AcquisitionTicket, status: Literal["unknown", "stopped"] = "unknown"
    ):
        if status not in {"unknown", "stopped"}:
            raise ProvisionError("provision_transition_invalid")
        with self._transaction():
            state = self._tickets.get(ticket._token) if type(ticket) is AcquisitionTicket else None
            if state is None or state[3] not in {"prepared", "accepted"}:
                raise ProvisionError("provision_reconciliation_required")
            self._acquisition_transition(ticket, state[3], status, datetime.now(UTC).isoformat())

    def acquisitions(self, *, limit: int = 100):
        if self.version != 2:
            raise ProvisionError("provision_acquisition_required")
        return self._snapshots("acquisitions", limit)

    def assert_acquisition_ready(self):
        """Guarda somente leitura antes de preflight/rede, sem gerar identidade."""
        self.assert_locked()
        if self.version != 2:
            raise ProvisionError("provision_acquisition_required")
        if (
            self.connection.execute(
                "SELECT 1 FROM acquisitions WHERE status!='completed'"
            ).fetchone()
            or self.connection.execute("SELECT 1 FROM runs WHERE status!='completed'").fetchone()
            or self.connection.execute("SELECT 1 FROM requests WHERE status='pending'").fetchone()
        ):
            raise ProvisionError("provision_reconciliation_required")

    def execution_blocked_local(self) -> bool:
        """Estado global do ledger, sem inferir conexão, VM pronta ou autorização."""
        self.assert_locked()
        try:
            self._validate()
            blocked = bool(
                (
                    self.version == 2
                    and self.connection.execute(
                        "SELECT 1 FROM acquisitions WHERE status!='completed'"
                    ).fetchone()
                )
                or self.connection.execute(
                    "SELECT 1 FROM runs WHERE status!='completed'"
                ).fetchone()
                or self.connection.execute(
                    "SELECT 1 FROM requests WHERE status='pending'"
                ).fetchone()
            )
            if self._read_only:
                self._files()
            return blocked
        except OSError, sqlite3.Error, ValueError, TypeError, KeyError:
            raise ProvisionError("provision_state_invalid") from None

    @contextmanager
    def lock(self):
        if self._locked:
            raise ProvisionError("provision_lock_required")
        self._files()
        descriptor = os.open(
            self.directory / "owner.lock", os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
        )
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
            self._lock_epoch = object()
            try:
                self._validate()
                yield
            except sqlite3.Error, ValueError, TypeError, KeyError:
                raise ProvisionError("provision_state_invalid") from None
            finally:
                self._locked = False
                self._owner_thread = None
                self._lock_epoch = None
                self._tickets.clear()
                if os.name == "nt":
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    @contextmanager
    def _transaction(self):
        self._assert_writable()
        self.assert_locked()
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            yield
            self.connection.execute("COMMIT")
        except BaseException as error:
            if not isinstance(error, ProvisionError):
                # Uma falha no COMMIT não pode conservar a capacidade RAM atualizada
                # dentro da transação; o registro persistido continuará bloqueando.
                self._tickets.clear()
            if self.connection.in_transaction:
                self.connection.execute("ROLLBACK")
            if isinstance(error, sqlite3.Error):
                raise ProvisionError("provision_state_unavailable") from None
            raise

    def assert_locked(self):
        if not self._locked or self._owner_thread != threading.get_ident():
            raise ProvisionError("provision_lock_required")

    def _assert_writable(self):
        if self._read_only:
            raise ProvisionError("provision_read_only")

    def begin(self, claim: Claim, *, acquisition: AcquisitionTicket | None = None):
        if not isinstance(claim, Claim) or (
            claim.installation_id != self.installation_id
            or claim.host_id != self.host_id
            or claim.status not in {"claimed", "dispatch_started"}
        ):
            raise ProvisionError("provision_supervisor_binding_invalid")
        with self._transaction():
            acquired = None
            if self.version == 2:
                acquired = self._ticket(acquisition, "accepted")
                self._claim_matches(acquired, claim)
                if (
                    str(claim.claim_id) != acquired["claim_id"]
                    or claim.generation != acquired["generation"]
                    or claim.revision != acquired["claim_revision"]
                    or claim.lease_expires_at != _date(acquired["lease_expires_at"])
                    or claim.status != acquired["claim_status"]
                ):
                    raise ProvisionError("provision_claim_stale")
            elif acquisition is not None:
                raise ProvisionError("provision_acquisition_required")
            if self.connection.execute("SELECT 1 FROM runs WHERE status!='completed'").fetchone():
                raise ProvisionError("provision_reconciliation_required")
            if self.connection.execute(
                "SELECT 1 FROM runs WHERE claim_id=?", (str(claim.claim_id),)
            ).fetchone():
                raise ProvisionError("provision_claim_reused")
            now = datetime.now(UTC).isoformat()
            self.connection.execute(
                "INSERT INTO runs VALUES(?,?,?,?,?,?,?,?,?,'running',?,?)",
                (
                    str(claim.claim_id),
                    str(claim.provisioner_id),
                    str(claim.plan_id),
                    claim.plan_hash,
                    str(claim.owner_id),
                    claim.generation,
                    claim.revision,
                    claim.lease_expires_at.isoformat(),
                    claim.status,
                    now,
                    now,
                ),
            )
            if acquired is not None:
                self._acquisition_transition(acquisition, "accepted", "running", now)

    def finish(self, claim_id: UUID, status: Literal["completed", "stopped", "unknown"]):
        if type(claim_id) is not UUID or status not in {"completed", "stopped", "unknown"}:
            raise ProvisionError("provision_transition_invalid")
        with self._transaction():
            acquired = self._live_claim(claim_id, {"running"}) if self.version == 2 else None
            run = self.connection.execute(
                "SELECT claim_status FROM runs WHERE claim_id=?", (str(claim_id),)
            ).fetchone()
            if (
                status == "completed"
                and run is not None
                and run[0] in {"outcome_unknown", "aborted"}
            ):
                raise ProvisionError("provision_reconciliation_required")
            if (
                status == "completed"
                and self.connection.execute(
                    "SELECT 1 FROM requests WHERE claim_id=? AND status='pending'", (str(claim_id),)
                ).fetchone()
            ):
                raise ProvisionError("provision_reconciliation_required")
            count = self.connection.execute(
                "UPDATE runs SET status=?,updated_at=? WHERE claim_id=? AND status='running'",
                (status, datetime.now(UTC).isoformat(), str(claim_id)),
            ).rowcount
            if count != 1:
                raise ProvisionError("provision_transition_invalid")
            if self.version == 2:
                count = self.connection.execute(
                    "UPDATE acquisitions SET status=?,revision=revision+1,updated_at=? "
                    "WHERE claim_id=? AND status='running'",
                    (status, datetime.now(UTC).isoformat(), str(claim_id)),
                ).rowcount
                if count != 1:
                    raise ProvisionError("provision_transition_invalid")
                ticket, row = acquired
                self._tickets[ticket._token] = (
                    ticket,
                    self._lock_epoch,
                    row["revision"] + 1,
                    status,
                )

    def request(self, claim_id: UUID, kind: Literal["renew", "unknown"]) -> UUID:
        self._assert_writable()
        if type(claim_id) is not UUID or kind not in {"renew", "unknown"}:
            raise ProvisionError("provision_transition_invalid")
        request_id = uuid4()
        with self._transaction():
            if self.version == 2:
                self._live_claim(claim_id, {"running", "unknown"})
            run = self.connection.execute(
                "SELECT * FROM runs WHERE claim_id=?", (str(claim_id),)
            ).fetchone()
            if run is None or (
                (kind == "unknown" and run["status"] not in {"running", "unknown"})
                or (
                    kind == "renew"
                    and (
                        run["status"] != "running"
                        or run["claim_status"] not in {"claimed", "dispatch_started"}
                    )
                )
            ):
                raise ProvisionError("provision_transition_invalid")
            if self.connection.execute(
                "SELECT 1 FROM requests WHERE claim_id=? AND kind=? AND status='pending'",
                (str(claim_id), kind),
            ).fetchone():
                raise ProvisionError("provision_reconciliation_required")
            if (
                kind == "renew"
                and self.connection.execute(
                    "SELECT 1 FROM requests WHERE claim_id=? AND kind='unknown'", (str(claim_id),)
                ).fetchone()
            ):
                raise ProvisionError("provision_reconciliation_required")
            self.connection.execute(
                "INSERT INTO requests VALUES(?,?,?,?,?,'pending',?,NULL,?,NULL)",
                (
                    str(request_id),
                    str(claim_id),
                    run["owner_id"],
                    run["generation"],
                    kind,
                    run["revision"],
                    datetime.now(UTC).isoformat(),
                ),
            )
        return request_id

    def confirm(self, request_id: UUID, claim: Claim):
        if type(request_id) is not UUID or not isinstance(claim, Claim):
            raise ProvisionError("provision_supervisor_binding_invalid")
        with self._transaction():
            if self.version == 2:
                self._live_claim(claim.claim_id, {"running", "unknown"})
            request = self.connection.execute(
                "SELECT * FROM requests WHERE request_id=? AND status='pending'", (str(request_id),)
            ).fetchone()
            run = self.connection.execute(
                "SELECT * FROM runs WHERE claim_id=?", (str(claim.claim_id),)
            ).fetchone()
            if request is None or run is None or request["claim_id"] != str(claim.claim_id):
                raise ProvisionError("provision_transition_invalid")
            if (request["kind"] == "unknown" and run["status"] not in {"running", "unknown"}) or (
                request["kind"] == "renew" and run["status"] != "running"
            ):
                raise ProvisionError("provision_transition_invalid")
            fixed = ("claim_id", "provisioner_id", "plan_id", "plan_hash", "owner_id", "generation")
            if (
                claim.installation_id != self.installation_id
                or claim.host_id != self.host_id
                or any(str(getattr(claim, name)) != str(run[name]) for name in fixed)
                or claim.revision < run["revision"]
                or claim.lease_expires_at < _date(run["lease_expires_at"])
                or (
                    request["kind"] == "renew"
                    and claim.status not in {"claimed", "dispatch_started"}
                )
                or (request["kind"] == "unknown" and claim.status != "outcome_unknown")
                or (
                    claim.revision == run["revision"]
                    and (
                        claim.status != run["claim_status"]
                        or claim.lease_expires_at != _date(run["lease_expires_at"])
                    )
                )
            ):
                raise ProvisionError("provision_claim_stale")
            now = datetime.now(UTC).isoformat()
            self.connection.execute(
                "UPDATE runs SET revision=?,lease_expires_at=?,claim_status=?,updated_at=? "
                "WHERE claim_id=?",
                (
                    claim.revision,
                    claim.lease_expires_at.isoformat(),
                    claim.status,
                    now,
                    str(claim.claim_id),
                ),
            )
            self.connection.execute(
                "UPDATE requests SET status='confirmed',revision_after=?,confirmed_at=? "
                "WHERE request_id=?",
                (claim.revision, now, str(request_id)),
            )

    def runs(self, *, limit: int = 100):
        return self._snapshots("runs", limit)

    def requests(self, *, limit: int = 100):
        return self._snapshots("requests", limit)

    def _snapshots(self, table: str, limit: int):
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ProvisionError("provision_snapshot_limit_invalid")
        if self._read_only:
            try:
                self._validate()
            except OSError, sqlite3.Error, ValueError, TypeError, KeyError:
                raise ProvisionError("provision_state_invalid") from None
        rows = [
            dict(row)
            for row in self.connection.execute(
                f"SELECT * FROM {table} ORDER BY created_at DESC,rowid DESC LIMIT ?", (limit,)
            )
        ]
        if self._read_only:
            self._files()
        return rows

    def close(self):
        if self._locked:
            raise ProvisionError("provision_lock_required")
        if self.connection is not None:
            self.connection.close()
