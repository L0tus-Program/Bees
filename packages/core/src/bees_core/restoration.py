"""Restore a verified logical package into a new, private, inactive installation."""

from __future__ import annotations

import copy
import ctypes
import errno
import hashlib
import io
import os
import re
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid4

import apsw
from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    ValidationError,
)

from bees_core.artifacts import ArtifactStore
from bees_core.portability import (
    COLUMNS,
    EXCLUDED,
    ExportService,
    PortabilityError,
    _json,
    _load,
    _private_dir,
    _read,
    _write,
)
from bees_core.storage.database import Database

# Parents precede children for SQL triggers, not merely FK checking. The skills ->
# revision cycle already has a DEFERRABLE FK; no trigger/FK is disabled for restore.
_ORDER = (
    "agents",
    "conversations",
    "messages",
    "routines",
    "environments",
    "routine_definitions",
    "routine_cursors",
    "tasks",
    "runs",
    "plugins",
    "tool_grants",
    "policies",
    "actions",
    "approvals",
    "memories",
    "model_calls",
    "artifacts",
    "artifact_commands",
    "domain_events",
    "task_commands",
    "budget_limits",
    "usage_entries",
    "skills",
    "skill_revisions",
    "skill_commands",
    "skill_events",
    "skill_process_origins",
    "routine_commands",
    "routine_occurrences",
    "attention_items",
    "attention_preferences",
    "attention_commands",
    "safety_commands",
)
assert set(_ORDER) == set(COLUMNS) - {"schema_migrations", "safety_control"}
_TERMINAL = {"completed", "failed", "cancelled"}
_ALIAS = re.compile(r"[a-z][a-z0-9-]{0,62}\Z")


class RestorationError(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)


class StateSnapshot(Contract):
    status: (
        Literal[
            "active",
            "paused",
            "archived",
            "queued",
            "running",
            "waiting_approval",
            "waiting_resource",
            "completed",
            "failed",
            "cancelled",
            "prepared",
            "awaiting_approval",
            "ready",
            "dispatch_started",
            "confirmed",
            "failed_no_effect",
            "outcome_unknown",
            "pending",
            "approved",
            "denied",
            "revoked",
            "reserved",
            "unknown",
            "released",
            "stopped",
            "disabled",
            "awaiting_host",
            "provisioning",
        ]
        | None
    ) = None
    desired_state: Literal["running", "paused", "cancelled"] | None = None
    enabled: StrictBool | None = None
    configured: StrictBool | None = None
    scheduled: StrictBool | None = None
    enabled_version: StrictInt | None = Field(default=None, ge=1)
    generation: StrictInt | None = Field(default=None, ge=0)
    revision: StrictInt | None = Field(default=None, ge=1)


class StateChange(Contract):
    table: Literal[
        "agents",
        "tasks",
        "runs",
        "routines",
        "routine_cursors",
        "routine_occurrences",
        "plugins",
        "tool_grants",
        "skills",
        "policies",
        "approvals",
        "actions",
        "model_calls",
        "usage_entries",
        "safety_control",
        "environments",
    ]
    entity_id: UUID | None
    reason: Literal[
        "reconnect_agent",
        "pause_task",
        "pause_run",
        "pause_routine",
        "clear_schedule",
        "disable_plugin",
        "disable_grant",
        "disable_skill",
        "review_allow_policy",
        "revoke_unconsumed_approval",
        "cancel_prepared_action",
        "cancel_prepared_call",
        "inflight_unknown",
        "unsettled_usage_unknown",
        "cancel_pending_occurrence",
        "local_global_stop",
        "host_review",
    ]
    source: StateSnapshot
    destination: StateSnapshot


class RestorationReport(Contract):
    restore_id: UUID
    source_id: UUID
    destination: str = Field(pattern=r"^[a-z][a-z0-9-]{0,62}$")
    status: Literal["inactive"] = "inactive"
    created_at: AwareDatetime
    package_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_schema: StrictInt = Field(ge=17)
    destination_schema: StrictInt = Field(ge=17)
    records: dict[str, StrictInt]
    blobs: StrictInt = Field(ge=0)
    changes: list[StateChange]
    review_required: list[
        Literal[
            "new_identity",
            "provider_reconnection",
            "host_reconnection",
            "policy_and_grant_review",
            "routine_review",
            "task_review",
            "unknown_outcomes",
            "explicit_global_resume",
            "provisioner_state_not_restored",
            "connections_not_restored",
        ]
    ]
    source_evidence: Literal["source.zip"] = "source.zip"
    revision_policy: Literal["new-installation-baseline-original-revisions"] = (
        "new-installation-baseline-original-revisions"
    )


class RestoreService:
    """Targets are trusted composition, never paths accepted from HTTP or a package."""

    def __init__(self, verifier: ExportService, destinations: dict[str, Path]):
        self.verifier = verifier
        active = verifier.database.path.parent
        targets = {}
        for alias, value in destinations.items():
            if not _ALIAS.fullmatch(alias) or not Path(value).is_absolute():
                raise ValueError("Invalid restoration destination")
            path = Path(os.path.abspath(value))
            if path.is_relative_to(active) or active.is_relative_to(path):
                raise ValueError("Restoration destination overlaps active installation")
            for existing in targets.values():
                if path.is_relative_to(existing) or existing.is_relative_to(path):
                    raise ValueError("Restoration destinations overlap")
            targets[alias] = path
        self._targets = targets

    def destinations(self) -> list[dict]:
        return [
            {
                "id": alias,
                "available": not path.exists() and not path.is_symlink(),
                "state": "inactive_only",
            }
            for alias, path in sorted(self._targets.items())
        ]

    def _target(self, alias: str) -> Path:
        try:
            return self._targets[alias]
        except KeyError:
            raise RestorationError("restoration_destination_unknown") from None

    def _verified(self, raw: bytes) -> dict:
        if type(raw) is not bytes or len(raw) > self.verifier.limits.max_bytes:
            raise RestorationError("restoration_limit_exceeded")
        try:
            # Validate the exact immutable bytes later parsed/copied, not a path
            # re-opened after verification. Reuse the full closed v1 verifier.
            return self.verifier._verify_bytes(raw)
        except PortabilityError as error:
            raise RestorationError(error.code) from None
        except Exception:
            raise RestorationError("restoration_invalid_package") from None

    @staticmethod
    def _change(changes, table, row, reason, before, after):
        identifier = row.get("id", row.get("routine_id"))
        changes.append(
            StateChange(
                table=table,
                entity_id=identifier if table != "safety_control" else None,
                reason=reason,
                source=StateSnapshot(**before),
                destination=StateSnapshot(**after),
            )
        )

    def _transform(self, original: dict[str, list[dict]], stamp: str):
        tables = copy.deepcopy(original)
        changes = []
        for row in tables["agents"]:
            before = {"status": row["status"], "configured": bool(_load(row["model_config_json"]))}
            if row["status"] == "active":
                row["status"] = "paused"
            row["model_config_json"] = "{}"
            self._change(
                changes,
                "agents",
                row,
                "reconnect_agent",
                before,
                {"status": row["status"], "configured": False},
            )
        for table, reason in (("tasks", "pause_task"), ("runs", "pause_run")):
            for row in tables[table]:
                if row["status"] in _TERMINAL:
                    continue
                before = {"status": row["status"]}
                after = {"status": "paused"}
                if table == "tasks":
                    before["desired_state"] = row["desired_state"]
                    # Cancellation intent remains cancellation, never becomes runnable.
                    after["desired_state"] = (
                        "cancelled" if row["desired_state"] == "cancelled" else "paused"
                    )
                row.update(after)
                self._change(changes, table, row, reason, before, after)
        for row in tables["routines"]:
            before = {"status": row["status"], "scheduled": row["next_run_at"] is not None}
            if row["status"] == "active":
                row["status"] = "paused"
            row["next_run_at"] = None
            self._change(
                changes,
                "routines",
                row,
                "pause_routine",
                before,
                {"status": row["status"], "scheduled": False},
            )
        for row in tables["routine_cursors"]:
            before = {"scheduled": row["next_run_at"] is not None}
            row["next_run_at"] = None
            self._change(
                changes, "routine_cursors", row, "clear_schedule", before, {"scheduled": False}
            )
        for table, reason in (("plugins", "disable_plugin"), ("tool_grants", "disable_grant")):
            for row in tables[table]:
                if row["enabled"]:
                    self._change(changes, table, row, reason, {"enabled": True}, {"enabled": False})
                row["enabled"] = 0
        for row in tables["skills"]:
            if row["enabled_version"] is not None:
                self._change(
                    changes,
                    "skills",
                    row,
                    "disable_skill",
                    {"enabled_version": row["enabled_version"]},
                    {"enabled_version": None},
                )
            row["enabled_version"] = None
        for row in tables["policies"]:
            if row["effect"] == "allow" and row["status"] == "active":
                self._change(
                    changes,
                    "policies",
                    row,
                    "review_allow_policy",
                    {"status": "active"},
                    {"status": "revoked"},
                )
                row["status"] = "revoked"
        for row in tables["approvals"]:
            if row["status"] in {"pending", "approved"} and not _load(row["metadata_json"]).get(
                "consumed_at"
            ):
                self._change(
                    changes,
                    "approvals",
                    row,
                    "revoke_unconsumed_approval",
                    {"status": row["status"]},
                    {"status": "revoked"},
                )
                row["status"] = "revoked"
        for table in ("actions", "model_calls"):
            prepared = (
                {"prepared", "awaiting_approval", "ready"} if table == "actions" else {"prepared"}
            )
            for row in tables[table]:
                status = row["status"]
                if status == "dispatch_started":
                    row["status"] = "outcome_unknown"
                    reason = "inflight_unknown"
                elif status in prepared:
                    row["status"] = "cancelled"
                    reason = (
                        "cancel_prepared_action" if table == "actions" else "cancel_prepared_call"
                    )
                else:
                    continue
                self._change(
                    changes, table, row, reason, {"status": status}, {"status": row["status"]}
                )
        for row in tables["usage_entries"]:
            if row["status"] == "reserved":
                row["status"], row["settled_at"] = "unknown", stamp
                self._change(
                    changes,
                    "usage_entries",
                    row,
                    "unsettled_usage_unknown",
                    {"status": "reserved"},
                    {"status": "unknown"},
                )
        for row in tables["routine_occurrences"]:
            if row["status"] in {"pending", "running"}:
                status = row["status"]
                row["status"] = "cancelled" if status == "pending" else "outcome_unknown"
                reason = "cancel_pending_occurrence" if status == "pending" else "inflight_unknown"
                # Retain the original reason/evidence; the import reason belongs in the report.
                self._change(
                    changes,
                    "routine_occurrences",
                    row,
                    reason,
                    {"status": status},
                    {"status": row["status"]},
                )
        for row in tables["environments"]:
            if row["status"] == "provisioning":
                self._change(
                    changes,
                    "environments",
                    row,
                    "host_review",
                    {"status": "provisioning"},
                    {"status": "outcome_unknown"},
                )
                row["status"] = "outcome_unknown"
        # safety_control is local. The immutable source state/commands stay in source.zip;
        # the destination uses its own legitimate stop transition from the fresh seed.
        source = tables["safety_control"]
        if len(source) != 1 or source[0]["id"] != 1:
            raise RestorationError("restoration_invalid_safety_state")
        self._change(
            changes,
            "safety_control",
            source[0],
            "local_global_stop",
            {key: source[0][key] for key in ("status", "revision", "generation")},
            {"status": "stopped", "revision": 2, "generation": 1},
        )
        return tables, changes

    @staticmethod
    def _load_database(database: Database, tables, stamp):
        database.initialize()
        with database.transaction() as connection:
            deleted_parents = []
            for table in _ORDER:
                rows = tables[table]
                if table == "artifacts":
                    rows = sorted(rows, key=lambda row: (row["version"], row["id"]))
                columns = COLUMNS[table]
                sql = (
                    f'INSERT INTO "{table}" ('
                    + ",".join(columns)
                    + ") VALUES ("
                    + ",".join("?" for _ in columns)
                    + ")"
                )
                children = {row["previous_id"] for row in rows} if table == "artifacts" else set()
                for original in rows:
                    row = original
                    if (
                        table == "artifacts"
                        and original["status"] == "deleted"
                        and original["id"] in children
                    ):
                        # The parent was ready when its child was accepted, then tombstoned.
                        # Reconstruct that legitimate transition inside the unpublished
                        # transaction; final records remain byte-for-byte tombstones.
                        row = original | {"status": "ready"}
                        deleted_parents.append(row["id"])
                    connection.execute(sql, tuple(row[column] for column in columns))
            for identifier in deleted_parents:
                connection.execute(
                    "UPDATE artifacts SET status='deleted' WHERE id=?", (identifier,)
                )
            connection.execute(
                "UPDATE safety_control SET status='stopped',generation=1,revision=2,"
                "changed_at=?,reason='restoration_requires_review' WHERE id=1",
                (stamp,),
            )
            if list(connection.execute("PRAGMA foreign_key_check")):
                raise RestorationError("restoration_invalid_graph")
        connection = apsw.Connection(str(database.path))
        try:
            if connection.execute("PRAGMA integrity_check").get != "ok":
                raise RestorationError("restoration_integrity_failed")
            if connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").get[0] != 0:
                raise RestorationError("restoration_storage_unavailable")
            if connection.execute("PRAGMA journal_mode=DELETE").get != "delete":
                raise RestorationError("restoration_storage_unavailable")
        finally:
            connection.close()
        database.require_current_schema()
        with database.path.open("r+b") as stream:
            os.fsync(stream.fileno())

    @staticmethod
    def _publish(stage: Path, target: Path):
        if os.name == "nt":
            try:
                os.rename(stage, target)  # Windows refuses an existing destination.
            except FileExistsError:
                raise RestorationError("restoration_destination_exists") from None
        elif os.name == "posix":
            # Plain POSIX rename can replace an existing empty directory. Linux's
            # NOREPLACE primitive is required; never fall back to unsafe overwrite.
            runtime = ctypes.CDLL(None, use_errno=True)
            rename = getattr(runtime, "renameat2", None)
            if rename is None:
                raise RestorationError("restoration_atomic_publish_unavailable")
            rename.argtypes = [
                ctypes.c_int,
                ctypes.c_char_p,
                ctypes.c_int,
                ctypes.c_char_p,
                ctypes.c_uint,
            ]
            rename.restype = ctypes.c_int
            if rename(-100, os.fsencode(stage), -100, os.fsencode(target), 1):
                code = ctypes.get_errno()
                if code in {errno.EEXIST, errno.ENOTEMPTY}:
                    raise RestorationError("restoration_destination_exists")
                raise RestorationError("restoration_atomic_publish_unavailable")
        else:
            raise RestorationError("restoration_atomic_publish_unavailable")
        ArtifactStore._sync_directory(target.parent)

    def restore(self, alias: str, raw: bytes) -> RestorationReport:
        target = self._target(alias)
        manifest = self._verified(raw)
        if target.exists() or target.is_symlink():
            raise RestorationError("restoration_destination_exists")
        identifier = uuid4()
        stage = target.with_name(f".bees-restore-{identifier}.staging")
        try:
            _private_dir(stage)
            _write(stage / "source.zip", raw)
            with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                original = {
                    table: [
                        _load(line)
                        for line in (
                            archive.read(f"data/{table}.jsonl").splitlines()
                            if table in manifest["tables"]
                            else []
                        )
                    ]
                    for table in COLUMNS
                }
                stamp = datetime.now(UTC).isoformat()
                tables, changes = self._transform(original, stamp)
                root = stage / "artifacts"
                _private_dir(root / "blobs")
                _private_dir(root / "staging")
                for aid in manifest["blobs"]:
                    _write(root / "blobs" / aid, archive.read(f"blobs/{aid}"))
            database = Database(stage / "bees.sqlite3")
            self._load_database(database, tables, stamp)
            # Reopen independently and check every blob again before publication.
            artifacts = ArtifactStore(database, root)
            for row in tables["artifacts"]:
                if row["status"] == "ready":
                    artifacts.read(UUID(row["agent_id"]), UUID(row["id"]))
            with database.transaction(write=False) as connection:
                if (
                    connection.execute("SELECT status FROM safety_control WHERE id=1").get
                    != "stopped"
                ):
                    raise RestorationError("restoration_not_stopped")
                for table in EXCLUDED:
                    if connection.execute(f'SELECT COUNT(*) FROM "{table}"').get:
                        raise RestorationError("restoration_authority_imported")
            report = RestorationReport(
                restore_id=identifier,
                source_id=manifest["id"],
                destination=alias,
                created_at=datetime.fromisoformat(stamp),
                package_sha256=hashlib.sha256(raw).hexdigest(),
                source_schema=manifest["database_schema"],
                destination_schema=database.schema_version(),
                records={table: len(rows) for table, rows in tables.items()},
                blobs=len(manifest["blobs"]),
                changes=changes,
                review_required=[
                    "new_identity",
                    "provider_reconnection",
                    "host_reconnection",
                    "policy_and_grant_review",
                    "routine_review",
                    "task_review",
                    "unknown_outcomes",
                    "explicit_global_resume",
                    "provisioner_state_not_restored",
                    "connections_not_restored",
                ],
            )
            _write(stage / "restoration.json", report.model_dump_json().encode())
            ArtifactStore._sync_directory(root / "blobs")
            ArtifactStore._sync_directory(root)
            ArtifactStore._sync_directory(stage)
            self._publish(stage, target)
            return report
        except BaseException as error:
            if stage.is_dir() and not stage.is_symlink():
                try:
                    _write(
                        stage / "error.json",
                        _json(
                            {
                                "status": "failed",
                                "code": error.code
                                if isinstance(error, (RestorationError, PortabilityError))
                                else "restoration_interrupted",
                            }
                        ),
                    )
                except OSError:
                    pass
            if not isinstance(error, Exception) or isinstance(error, RestorationError):
                raise
            raise RestorationError("restoration_failed") from None

    def report(self, alias: str) -> RestorationReport:
        target = self._target(alias)
        try:
            raw = _read(target / "restoration.json", self.verifier.limits.max_bytes)
            report = RestorationReport.model_validate_json(raw)
            if (
                report.destination != alias
                or set(report.records) != set(COLUMNS)
                or any(value < 0 for value in report.records.values())
            ):
                raise RestorationError("restoration_invalid_report")
            evidence = _read(target / "source.zip", self.verifier.limits.max_bytes)
            if hashlib.sha256(evidence).hexdigest() != report.package_sha256:
                raise RestorationError("restoration_invalid_report")
            return report
        except FileNotFoundError:
            raise RestorationError("restoration_not_found") from None
        except OSError, ValueError, ValidationError, PortabilityError:
            raise RestorationError("restoration_invalid_report") from None
