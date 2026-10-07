"""Receipts locais de diagnóstico. Não é o journal canônico de Task/Action.

SQLite stdlib, DELETE+FULL, transações curtas e fencing da sessão autenticada.
Nenhuma consulta ou recuperação repete diagnóstico iniciado de resultado incerto.
"""

import hashlib
import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from bees_guest import security
from bees_guest.errors import GuestError
from bees_guest.protocol import (
    canonical_json,
    canonical_uuid,
    decode,
    exact,
    health_request,
    status,
)

APPLICATION_ID = 0x42454731
MAX_RECEIPTS = 10000


class Journal:
    @staticmethod
    def marker_path(path):
        return Path(str(path) + ".identity.json")

    @staticmethod
    def _binding_hash(identity):
        return hashlib.sha256(
            canonical_json(
                identity.binding.fields()
                | {
                    "guest_id": identity.guest_id,
                    "relay_cert_sha256": identity.relay_cert_sha256,
                }
            )
        ).hexdigest()

    @classmethod
    def initialize(cls, path, identity):
        """Provisionamento explícito por operador; nunca chamado pela CLI de conexão."""
        path = Path(path)
        marker_path = cls.marker_path(path)
        security.check_private(path.parent, directory=True)
        for entry in (
            path,
            marker_path,
            *(Path(str(path) + s) for s in ("-journal", "-wal", "-shm")),
        ):
            if entry.exists() or entry.is_symlink():
                raise GuestError("journal_already_present")
        marker = {
            "format": 1,
            "journal_id": str(uuid4()),
            "binding_hash": cls._binding_hash(identity),
        }
        connection = None
        try:
            # O marcador é persistido primeiro. Qualquer instalação parcial falha fechada.
            fd = os.open(marker_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(fd, "wb") as target:
                target.write(canonical_json(marker))
                target.flush()
                os.fsync(target.fileno())
            if os.name == "posix":
                fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(fd)
                finally:
                    os.close(fd)
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(fd)
            connection = sqlite3.connect(
                path.as_uri() + "?mode=rw", uri=True, timeout=2, isolation_level=None
            )
            connection.execute("PRAGMA journal_mode=DELETE")
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "CREATE TABLE identity(id INTEGER PRIMARY KEY CHECK(id=1),"
                "binding_hash TEXT NOT NULL,journal_id TEXT NOT NULL,"
                "session_nonce TEXT,sequence INTEGER NOT NULL)"
            )
            connection.execute("CREATE TABLE sessions(nonce TEXT PRIMARY KEY)")
            connection.execute(
                "CREATE TABLE receipts(request_id TEXT PRIMARY KEY,request_hash TEXT NOT NULL,"
                "nonce TEXT NOT NULL UNIQUE,status TEXT NOT NULL CHECK(status IN "
                "('prepared','completed','unknown')),response TEXT)"
            )
            connection.execute(
                "INSERT INTO identity VALUES(1,?,?,NULL,0)",
                (marker["binding_hash"], marker["journal_id"]),
            )
            connection.execute(f"PRAGMA application_id={APPLICATION_ID}")
            connection.execute("PRAGMA user_version=1")
            connection.execute("COMMIT")
        except (OSError, sqlite3.Error):
            raise GuestError("journal_unavailable") from None
        finally:
            if connection is not None:
                connection.close()
        return cls(path, identity)

    def __init__(self, path, identity):
        self.path = Path(path)
        self.identity = identity
        security.check_private(self.path.parent, directory=True)
        marker_path = self.marker_path(self.path)
        if not self.path.exists() or not marker_path.exists():
            raise GuestError("journal_missing")
        security.check_private(self.path)
        security.check_private(marker_path)
        try:
            with marker_path.open("rb") as source:
                data = source.read(1025)
            if len(data) > 1024:
                raise GuestError("journal_invalid")
            marker = exact(decode(data), {"format", "journal_id", "binding_hash"})
            canonical_uuid(marker["journal_id"])
            if type(marker["format"]) is not int or marker["format"] != 1:
                raise GuestError("journal_invalid")
            if marker["binding_hash"] != self.binding_hash():
                raise GuestError("binding_mismatch")
        except OSError:
            raise GuestError("journal_invalid") from None
        for suffix in ("-journal", "-wal", "-shm"):
            extra = Path(str(self.path) + suffix)
            if extra.exists() or extra.is_symlink():
                security.check_private(extra)
        try:
            self.connection = sqlite3.connect(
                self.path.as_uri() + "?mode=rw", uri=True, timeout=2, isolation_level=None
            )
        except sqlite3.Error:
            raise GuestError("journal_unavailable") from None
        try:
            version = self.connection.execute("PRAGMA user_version").fetchone()[0]
            application_id = self.connection.execute("PRAGMA application_id").fetchone()[0]
            if version != 1 or application_id != APPLICATION_ID:
                raise GuestError("journal_invalid")
            current = self.connection.execute(
                "SELECT binding_hash,journal_id FROM identity WHERE id=1"
            ).fetchone()
            if current != (self.binding_hash(), marker["journal_id"]):
                raise GuestError("binding_mismatch")
            self.connection.execute("PRAGMA journal_mode=DELETE")
            self.connection.execute("PRAGMA synchronous=FULL")
            with self.transaction():
                self.connection.execute(
                    "UPDATE receipts SET status='unknown' WHERE status='prepared'"
                )
        except BaseException as error:
            self.connection.close()
            if isinstance(error, sqlite3.Error):
                raise GuestError("journal_invalid") from None
            raise

    def close(self):
        self.connection.close()

    def binding_hash(self):
        return self._binding_hash(self.identity)

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
                raise GuestError("journal_unavailable") from None
            raise

    def activate(self, session_nonce):
        canonical_uuid(session_nonce)
        with self.transaction():
            if self.connection.execute(
                "SELECT 1 FROM sessions WHERE nonce=?", (session_nonce,)
            ).fetchone():
                raise GuestError("session_fenced")
            if (
                self.connection.execute("SELECT count(*) FROM sessions").fetchone()[0]
                >= MAX_RECEIPTS
            ):
                raise GuestError("journal_limit")
            self.connection.execute("INSERT INTO sessions VALUES(?)", (session_nonce,))
            self.connection.execute(
                "UPDATE identity SET session_nonce=? WHERE id=1", (session_nonce,)
            )
            self.connection.execute("UPDATE receipts SET status='unknown' WHERE status='prepared'")

    def _session(self, nonce):
        return self.connection.execute(
            "SELECT session_nonce FROM identity WHERE id=1"
        ).fetchone() == (nonce,)

    @staticmethod
    def error(request, code):
        return request | {"type": "error", "code": code}

    def health(self, request, *, session_nonce, probe):
        health_request(request, self.identity.binding, self.identity.guest_id)
        if request["session_nonce"] != session_nonce:
            return self.error(request, "session_fenced")
        request_hash = hashlib.sha256(canonical_json(request)).hexdigest()
        with self.transaction():
            if not self._session(session_nonce):
                return self.error(request, "session_fenced")
            previous = self.connection.execute(
                "SELECT request_hash,status,response FROM receipts WHERE request_id=?",
                (request["request_id"],),
            ).fetchone()
            if previous:
                if previous[0] != request_hash:
                    return self.error(request, "request_conflict")
                if previous[1] != "completed":
                    return self.error(request, "request_unknown")
                return json.loads(previous[2]) | {"cached": True}
            if (
                self.connection.execute("SELECT count(*) FROM receipts").fetchone()[0]
                >= MAX_RECEIPTS
                or self.connection.execute(
                    "SELECT 1 FROM receipts WHERE nonce=?", (request["nonce"],)
                ).fetchone()
            ):
                return self.error(request, "request_conflict")
            self.connection.execute(
                "INSERT INTO receipts VALUES(?,?,?,'prepared',NULL)",
                (request["request_id"], request_hash, request["nonce"]),
            )
        try:
            observed = status(probe())
        except Exception:
            # Saída parcial e exceção de probe não são conservadas nem publicadas.
            observed = None
        with self.transaction():
            previous = self.connection.execute(
                "SELECT status FROM receipts WHERE request_id=?", (request["request_id"],)
            ).fetchone()
            code = (
                "session_fenced"
                if not self._session(session_nonce)
                else "request_unknown"
                if observed is None or previous != ("prepared",)
                else None
            )
            if code:
                self.connection.execute(
                    "UPDATE receipts SET status='unknown' WHERE request_id=? AND status='prepared'",
                    (request["request_id"],),
                )
                return self.error(request, code)
            sequence = (
                self.connection.execute("SELECT sequence FROM identity WHERE id=1").fetchone()[0]
                + 1
            )
            if sequence >= 2**63:
                raise GuestError("journal_limit")
            response = request | {
                "type": "health",
                "sequence": sequence,
                "status": observed,
                "cached": False,
            }
            self.connection.execute("UPDATE identity SET sequence=? WHERE id=1", (sequence,))
            self.connection.execute(
                "UPDATE receipts SET status='completed',response=? WHERE request_id=?",
                (canonical_json(response).decode(), request["request_id"]),
            )
            return response
