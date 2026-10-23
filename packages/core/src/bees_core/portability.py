"""Logical portability v2; reads the closed v1 inventory without inventing provenance."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import struct
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

from bees_core.artifacts import ArtifactStore
from bees_core.storage.database import Database

FORMAT = "bees.logical"
VERSION = 2
MIN_DATABASE_SCHEMA = 18
CONTENT_POLICY = "private-human-data-no-operational-credentials"
# Explicit contract. Unknown tables/columns fail closed until reviewed.
COLUMNS = {
    "actions": (
        "id",
        "run_id",
        "tool_name",
        "parameters_json",
        "status",
        "result_json",
        "idempotency_key",
        "attempt",
        "policy_revision",
        "lease_generation",
        "created_at",
        "updated_at",
        "revision",
        "metadata_json",
        "execution_binding_json",
        "unknown_acknowledged_at",
    ),
    "agents": (
        "id",
        "name",
        "purpose",
        "instructions",
        "model_config_json",
        "status",
        "created_at",
        "updated_at",
        "revision",
        "metadata_json",
    ),
    "approvals": (
        "id",
        "action_id",
        "policy_id",
        "status",
        "decision",
        "scope_json",
        "actor",
        "reason",
        "decided_at",
        "created_at",
        "updated_at",
        "revision",
        "metadata_json",
    ),
    "artifact_commands": (
        "client_request_id",
        "agent_id",
        "artifact_id",
        "kind",
        "request_hash",
        "created_at",
        "actor",
    ),
    "artifacts": (
        "id",
        "agent_id",
        "conversation_id",
        "task_id",
        "source",
        "run_id",
        "name",
        "media_type",
        "storage_key",
        "sha256",
        "size_bytes",
        "version",
        "status",
        "created_at",
        "updated_at",
        "revision",
        "metadata_json",
        "series_id",
        "previous_id",
    ),
    "attention_commands": (
        "client_request_id",
        "agent_id",
        "target",
        "request_hash",
        "created_at",
        "actor",
    ),
    "attention_items": (
        "seq",
        "id",
        "agent_id",
        "task_id",
        "approval_id",
        "kind",
        "entity_type",
        "entity_id",
        "source_event_id",
        "created_at",
        "updated_at",
        "revision",
        "is_read",
    ),
    "attention_preferences": (
        "agent_id",
        "kind",
        "enabled",
        "revision",
        "created_at",
        "updated_at",
    ),
    "budget_limits": (
        "id",
        "agent_id",
        "token_limit",
        "window_seconds",
        "output_allowance",
        "status",
        "created_at",
        "updated_at",
        "revision",
        "metadata_json",
    ),
    "conversations": (
        "id",
        "agent_id",
        "title",
        "status",
        "created_at",
        "updated_at",
        "revision",
        "metadata_json",
    ),
    "domain_events": (
        "seq",
        "id",
        "created_at",
        "entity_type",
        "entity_id",
        "event_type",
        "payload_json",
        "actor",
        "source",
        "correlation_id",
    ),
    "environments": (
        "id",
        "created_at",
        "updated_at",
        "revision",
        "metadata_json",
        "agent_id",
        "name",
        "template_id",
        "cpu_count",
        "memory_mib",
        "disk_gib",
        "status",
        "reason_code",
        "client_request_id",
        "request_hash",
    ),
    "memories": (
        "id",
        "agent_id",
        "task_id",
        "scope",
        "content",
        "source",
        "status",
        "deleted_at",
        "created_at",
        "updated_at",
        "revision",
        "metadata_json",
    ),
    "messages": (
        "id",
        "conversation_id",
        "role",
        "content",
        "source",
        "created_at",
        "updated_at",
        "revision",
        "metadata_json",
    ),
    "model_calls": (
        "id",
        "run_id",
        "ordinal",
        "phase",
        "status",
        "task_control_revision",
        "agent_revision",
        "lease_generation",
        "model_config_json",
        "request_json",
        "snapshot_json",
        "request_hash",
        "response_json",
        "output_message_id",
        "error_code",
        "started_at",
        "finished_at",
        "unknown_acknowledged_at",
        "created_at",
        "updated_at",
        "revision",
        "metadata_json",
    ),
    "plugins": (
        "id",
        "created_at",
        "updated_at",
        "revision",
        "metadata_json",
        "manifest_json",
        "manifest_hash",
        "enabled",
    ),
    "policies": (
        "id",
        "agent_id",
        "name",
        "effect",
        "scope_json",
        "status",
        "source",
        "reason",
        "created_at",
        "updated_at",
        "revision",
        "metadata_json",
    ),
    "routines": (
        "id",
        "agent_id",
        "name",
        "instructions",
        "schedule_json",
        "timezone",
        "next_run_at",
        "status",
        "misfire_policy",
        "overlap_policy",
        "created_at",
        "updated_at",
        "revision",
        "metadata_json",
    ),
    "runs": (
        "id",
        "task_id",
        "status",
        "checkpoint_json",
        "provider",
        "model",
        "environment_id",
        "started_at",
        "finished_at",
        "error",
        "created_at",
        "updated_at",
        "revision",
        "metadata_json",
        "model_config_json",
    ),
    "safety_commands": (
        "id",
        "client_request_id",
        "kind",
        "expected_revision",
        "resulting_revision",
        "actor",
        "reason",
        "created_at",
    ),
    "safety_control": ("id", "status", "generation", "revision", "changed_at", "reason"),
    "schema_migrations": ("version", "name", "checksum", "applied_at"),
    "skill_commands": (
        "client_request_id",
        "agent_id",
        "target",
        "request_hash",
        "response_json",
        "created_at",
    ),
    "skill_events": (
        "seq",
        "skill_id",
        "revision",
        "version",
        "kind",
        "client_request_id",
        "created_at",
        "actor",
    ),
    "skill_revisions": (
        "skill_id",
        "version",
        "definition_json",
        "content_hash",
        "origin",
        "created_at",
    ),
    "skill_process_origins": (
        "skill_id",
        "version",
        "agent_id",
        "task_id",
        "run_id",
        "message_id",
        "result_sha256",
        "review_json",
        "created_at",
        "actor",
    ),
    "skills": (
        "id",
        "agent_id",
        "revision",
        "current_version",
        "enabled_version",
        "created_at",
        "updated_at",
    ),
    "task_commands": (
        "id",
        "task_id",
        "client_request_id",
        "kind",
        "expected_revision",
        "payload_json",
        "created_at",
        "updated_at",
        "revision",
        "metadata_json",
    ),
    "tasks": (
        "id",
        "agent_id",
        "conversation_id",
        "routine_id",
        "title",
        "objective",
        "expected_result",
        "status",
        "created_at",
        "updated_at",
        "revision",
        "metadata_json",
        "submission_key",
        "desired_state",
        "control_revision",
        "max_calls",
        "max_active_seconds",
        "calls_started",
        "active_milliseconds",
    ),
    "tool_grants": (
        "id",
        "created_at",
        "updated_at",
        "revision",
        "metadata_json",
        "agent_id",
        "plugin_id",
        "tool_name",
        "enabled",
    ),
    "usage_entries": (
        "id",
        "agent_id",
        "source",
        "conversation_id",
        "model_call_id",
        "provider_kind",
        "model",
        "status",
        "reserved_tokens",
        "estimate_method",
        "usage_kind",
        "input_tokens",
        "output_tokens",
        "total_tokens",
        "settled_at",
        "created_at",
        "updated_at",
        "revision",
        "metadata_json",
    ),
}
EXCLUDED = frozenset(
    [
        "cache_entries",
        "channel_events",
        "channel_links",
        "computers",
        "execution_leases",
        "executor_commands",
        "executor_grants",
        "executor_invites",
        "executors",
        "host_jobs",
        "host_link_commands",
        "host_link_installation",
        "host_link_invites",
        "host_link_report_receipts",
        "host_links",
        "identity_bootstrap",
        "identity_rate_limits",
        "identity_sessions",
        "identity_users",
        "provisioning_authorizations",
        "provisioning_claims",
        "provisioning_commands",
        "provisioning_credentials",
        "provisioning_effects",
        "provisioning_hosts",
        "provisioning_plans",
        "provisioning_rejections",
        "provisioning_vm_bindings",
    ]
)
PRIMARY = {
    "actions": ("id",),
    "agents": ("id",),
    "approvals": ("id",),
    "artifact_commands": ("client_request_id",),
    "artifacts": ("id",),
    "attention_commands": ("client_request_id",),
    "attention_items": ("seq",),
    "attention_preferences": ("agent_id", "kind"),
    "budget_limits": ("id",),
    "conversations": ("id",),
    "domain_events": ("seq",),
    "environments": ("id",),
    "memories": ("id",),
    "messages": ("id",),
    "model_calls": ("id",),
    "plugins": ("id",),
    "policies": ("id",),
    "routines": ("id",),
    "runs": ("id",),
    "safety_commands": ("id",),
    "safety_control": ("id",),
    "schema_migrations": ("version",),
    "skill_commands": ("client_request_id",),
    "skill_events": ("seq",),
    "skill_revisions": ("skill_id", "version"),
    "skill_process_origins": ("skill_id", "version"),
    "skills": ("id",),
    "task_commands": ("id",),
    "tasks": ("id",),
    "tool_grants": ("id",),
    "usage_entries": ("id",),
}
LINKS = {
    "actions": [("runs", ("run_id",), ("id",))],
    "agents": [],
    "approvals": [("policies", ("policy_id",), ("id",)), ("actions", ("action_id",), ("id",))],
    "artifact_commands": [
        ("artifacts", ("artifact_id",), ("id",)),
        ("agents", ("agent_id",), ("id",)),
    ],
    "artifacts": [
        ("runs", ("run_id", "task_id"), ("id", "task_id")),
        ("conversations", ("conversation_id", "agent_id"), ("id", "agent_id")),
        ("tasks", ("task_id", "agent_id"), ("id", "agent_id")),
        ("artifacts", ("previous_id",), ("id",)),
        ("agents", ("agent_id",), ("id",)),
    ],
    "attention_commands": [("agents", ("agent_id",), ("id",))],
    "attention_items": [
        ("domain_events", ("source_event_id",), ("id",)),
        ("approvals", ("approval_id",), ("id",)),
        ("tasks", ("task_id",), ("id",)),
        ("agents", ("agent_id",), ("id",)),
    ],
    "attention_preferences": [("agents", ("agent_id",), ("id",))],
    "budget_limits": [("agents", ("agent_id",), ("id",))],
    "conversations": [("agents", ("agent_id",), ("id",))],
    "domain_events": [],
    "environments": [("agents", ("agent_id",), ("id",))],
    "memories": [
        ("tasks", ("task_id", "agent_id"), ("id", "agent_id")),
        ("agents", ("agent_id",), ("id",)),
    ],
    "messages": [("conversations", ("conversation_id",), ("id",))],
    "model_calls": [("messages", ("output_message_id",), ("id",)), ("runs", ("run_id",), ("id",))],
    "plugins": [],
    "policies": [("agents", ("agent_id",), ("id",))],
    "routines": [("agents", ("agent_id",), ("id",))],
    "runs": [("tasks", ("task_id",), ("id",))],
    "safety_commands": [],
    "safety_control": [],
    "schema_migrations": [],
    "skill_commands": [("agents", ("agent_id",), ("id",))],
    "skill_events": [
        ("skill_commands", ("client_request_id",), ("client_request_id",)),
        ("skills", ("skill_id",), ("id",)),
    ],
    "skill_revisions": [("skills", ("skill_id",), ("id",))],
    "skill_process_origins": [
        ("skill_revisions", ("skill_id", "version"), ("skill_id", "version")),
        ("skills", ("skill_id", "agent_id"), ("id", "agent_id")),
        ("tasks", ("task_id", "agent_id"), ("id", "agent_id")),
        ("runs", ("run_id", "task_id"), ("id", "task_id")),
        ("messages", ("message_id",), ("id",)),
    ],
    "skills": [
        ("skill_revisions", ("id", "current_version"), ("skill_id", "version")),
        ("agents", ("agent_id",), ("id",)),
    ],
    "task_commands": [("tasks", ("task_id",), ("id",))],
    "tasks": [
        ("routines", ("routine_id", "agent_id"), ("id", "agent_id")),
        ("conversations", ("conversation_id", "agent_id"), ("id", "agent_id")),
        ("agents", ("agent_id",), ("id",)),
    ],
    "tool_grants": [("plugins", ("plugin_id",), ("id",)), ("agents", ("agent_id",), ("id",))],
    "usage_entries": [
        ("model_calls", ("model_call_id",), ("id",)),
        ("conversations", ("conversation_id",), ("id",)),
        ("agents", ("agent_id",), ("id",)),
    ],
}

# BEES-021 introduces append-only definitions and occurrence history in schema 15.
COLUMNS.update(
    {
        "routine_definitions": (
            "routine_id",
            "revision",
            "definition_json",
            "content_hash",
            "origin",
            "created_at",
        ),
        "routine_cursors": ("routine_id", "routine_revision", "next_run_at", "evaluated_through"),
        "routine_commands": (
            "client_request_id",
            "agent_id",
            "target",
            "request_hash",
            "response_json",
            "created_at",
        ),
        "routine_occurrences": (
            "id",
            "routine_id",
            "routine_revision",
            "scheduled_at",
            "window_end",
            "status",
            "reason",
            "task_id",
            "revision",
            "created_at",
            "updated_at",
        ),
    }
)
PRIMARY.update(
    {
        "routine_definitions": ("routine_id", "revision"),
        "routine_cursors": ("routine_id",),
        "routine_commands": ("client_request_id",),
        "routine_occurrences": ("id",),
    }
)
LINKS.update(
    {
        "routine_definitions": [("routines", ("routine_id",), ("id",))],
        "routine_cursors": [
            ("routines", ("routine_id",), ("id",)),
            ("routine_definitions", ("routine_id", "routine_revision"), ("routine_id", "revision")),
        ],
        "routine_commands": [("agents", ("agent_id",), ("id",))],
        "routine_occurrences": [
            ("routines", ("routine_id",), ("id",)),
            ("routine_definitions", ("routine_id", "routine_revision"), ("routine_id", "revision")),
            ("tasks", ("task_id",), ("id",)),
        ],
    }
)


INTEGER_COLUMNS = {
    "actions": ("attempt", "policy_revision", "lease_generation", "revision"),
    "agents": ("revision",),
    "approvals": ("revision",),
    "artifact_commands": (),
    "artifacts": ("size_bytes", "version", "revision"),
    "attention_commands": (),
    "attention_items": ("seq", "revision", "is_read"),
    "attention_preferences": ("enabled", "revision"),
    "budget_limits": ("token_limit", "window_seconds", "output_allowance", "revision"),
    "conversations": ("revision",),
    "domain_events": ("seq",),
    "environments": ("revision", "cpu_count", "memory_mib", "disk_gib"),
    "memories": ("revision",),
    "messages": ("revision",),
    "model_calls": (
        "ordinal",
        "task_control_revision",
        "agent_revision",
        "lease_generation",
        "revision",
    ),
    "plugins": ("revision", "enabled"),
    "policies": ("revision",),
    "routine_commands": (),
    "routine_cursors": ("routine_revision",),
    "routine_definitions": ("revision",),
    "routine_occurrences": ("routine_revision", "revision"),
    "routines": ("revision",),
    "runs": ("revision",),
    "safety_commands": ("expected_revision", "resulting_revision"),
    "safety_control": ("id", "generation", "revision"),
    "schema_migrations": ("version",),
    "skill_commands": (),
    "skill_events": ("seq", "revision", "version"),
    "skill_revisions": ("version",),
    "skill_process_origins": ("version",),
    "skills": ("revision", "current_version", "enabled_version"),
    "task_commands": ("expected_revision", "revision"),
    "tasks": (
        "revision",
        "control_revision",
        "max_calls",
        "max_active_seconds",
        "calls_started",
        "active_milliseconds",
    ),
    "tool_grants": ("revision", "enabled"),
    "usage_entries": (
        "reserved_tokens",
        "input_tokens",
        "output_tokens",
        "total_tokens",
        "revision",
    ),
}
NULLABLE_COLUMNS = {
    "actions": (
        "result_json",
        "idempotency_key",
        "policy_revision",
        "lease_generation",
        "execution_binding_json",
        "unknown_acknowledged_at",
    ),
    "agents": (),
    "approvals": ("policy_id", "decision", "decided_at"),
    "artifact_commands": (),
    "artifacts": (
        "conversation_id",
        "task_id",
        "run_id",
        "storage_key",
        "sha256",
        "series_id",
        "previous_id",
    ),
    "attention_commands": (),
    "attention_items": ("approval_id",),
    "attention_preferences": (),
    "budget_limits": (),
    "conversations": (),
    "domain_events": ("correlation_id",),
    "environments": (),
    "memories": ("agent_id", "task_id", "deleted_at"),
    "messages": (),
    "model_calls": (
        "response_json",
        "output_message_id",
        "error_code",
        "started_at",
        "finished_at",
        "unknown_acknowledged_at",
    ),
    "plugins": (),
    "policies": ("agent_id",),
    "routine_commands": (),
    "routine_cursors": ("next_run_at",),
    "routine_definitions": (),
    "routine_occurrences": ("task_id",),
    "routines": ("next_run_at",),
    "runs": ("environment_id", "started_at", "finished_at", "error"),
    "safety_commands": ("reason",),
    "safety_control": ("reason",),
    "schema_migrations": (),
    "skill_commands": (),
    "skill_events": (),
    "skill_revisions": (),
    "skill_process_origins": (),
    "skills": ("enabled_version",),
    "task_commands": (),
    "tasks": ("conversation_id", "routine_id", "submission_key"),
    "tool_grants": (),
    "usage_entries": (
        "conversation_id",
        "model_call_id",
        "usage_kind",
        "input_tokens",
        "output_tokens",
        "total_tokens",
        "settled_at",
    ),
}


class PortabilityError(RuntimeError):
    """Only a stable code, never raw data or local paths."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class Limits:
    max_bytes: int = 256 * 1024 * 1024
    max_files: int = 10000
    max_rows: int = 200000
    max_depth: int = 32

    def __post_init__(self):
        if any(type(value) is not int or value < 1 for value in vars(self).values()):
            raise ValueError("Invalid portability limits")


@dataclass(frozen=True)
class Export:
    id: UUID
    sha256: str
    size_bytes: int
    rows: int
    blobs: int
    status: str = "ready"


_SECRET_KEY = re.compile(
    r"^(?:password|passwd|secret|secret_ref|access_token|refresh_token|token|credential|"
    r"credential_ref|cookies?|authorization|api.?key|private.?key|csrf|bootstrap|"
    r"execution_binding|session|session_id|session_token)"
    r"(?:_hash|_value|_ref|_id|_token|_key|s)?$",
    re.I,
)
_SECRET_VALUE = re.compile(
    r"\b(?:b[phx]_[A-Za-z0-9_-]{20,}|sk-[A-Za-z0-9_-]{16,}|"
    r"gh[pousr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,}|"
    r"Bearer\s+\S+|-----BEGIN [A-Z ]*PRIVATE KEY-----)",
    re.I,
)
_SECRET_ASSIGNMENT = re.compile(
    r"\b(?:password|passwd|api[_-]?key|access[_-]?token|cookie|authorization)"
    r"[\"']?\s*[=:]\s*[\"']?[^\s,;]+",
    re.I,
)


def _json(value) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def _pairs(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise PortabilityError("portability_invalid_package")
        value[key] = item
    return value


def _constant(_):
    raise PortabilityError("portability_invalid_package")


def _load(raw):
    return json.loads(raw, object_pairs_hook=_pairs, parse_constant=_constant)


def _sanitize(value, depth: int, limit: int, redactions: list[int]):
    if depth > limit:
        raise PortabilityError("portability_limit_exceeded")
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            # Keys remain literal bytes in JSON too; never rename leaked secrets.
            if _SECRET_VALUE.search(key) or _SECRET_ASSIGNMENT.search(key):
                raise PortabilityError("portability_sensitive_content")
            if _SECRET_KEY.search(key):
                result[key] = None
                redactions[0] += 1
            else:
                result[key] = _sanitize(item, depth + 1, limit, redactions)
        return result
    if isinstance(value, list):
        return [_sanitize(item, depth + 1, limit, redactions) for item in value]
    if isinstance(value, str) and (_SECRET_VALUE.search(value) or _SECRET_ASSIGNMENT.search(value)):
        # Text, blobs and hashed skill definitions cannot be silently rewritten.
        raise PortabilityError("portability_sensitive_content")
    return value


_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_UTC_STAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|\+00:00)\Z")


def _integer(value, minimum: int = 0) -> bool:
    return type(value) is int and minimum <= value <= 2**63 - 1


def _uuid(value) -> bool:
    if not isinstance(value, str):
        return False
    try:
        return str(UUID(value)) == value
    except ValueError:
        return False


def _timestamp(value) -> bool:
    if not isinstance(value, str) or not _UTC_STAMP.fullmatch(value):
        return False
    try:
        return datetime.fromisoformat(value).utcoffset().total_seconds() == 0
    except ValueError, AttributeError:
        return False


def _inventory(value, *, rows: bool) -> bool:
    fields = {"bytes", "sha256", "rows"} if rows else {"bytes", "sha256"}
    return (
        type(value) is dict
        and set(value) == fields
        and _integer(value["bytes"])
        and isinstance(value["sha256"], str)
        and bool(_DIGEST.fullmatch(value["sha256"]))
        and (not rows or _integer(value["rows"]))
    )


def _record_contract(table: str, record: dict) -> None:
    if type(record) is not dict or set(record) != set(COLUMNS[table]):
        raise PortabilityError("portability_unsupported_schema")
    for key, value in record.items():
        if value is None:
            if key not in NULLABLE_COLUMNS[table]:
                raise PortabilityError("portability_invalid_package")
            continue
        if key in INTEGER_COLUMNS[table]:
            minimum = (
                1
                if key
                in {
                    "id",
                    "seq",
                    "version",
                    "current_version",
                    "enabled_version",
                    "ordinal",
                    "attempt",
                }
                or (
                    key.endswith("revision")
                    and key not in {"control_revision", "task_control_revision"}
                )
                else 0
            )
            if not _integer(value, minimum):
                raise PortabilityError("portability_invalid_package")
            continue
        if type(value) is not str:
            raise PortabilityError("portability_invalid_package")
        if (
            (key == "id" or key.endswith("_id"))
            and key not in {"template_id", "correlation_id"}
            and not (key == "environment_id" and table == "runs")
            and not _uuid(value)
        ):
            raise PortabilityError("portability_invalid_graph")
        if (key.endswith("_at") or key in {"evaluated_through", "window_end"}) and not _timestamp(
            value
        ):
            raise PortabilityError("portability_invalid_package")
        if key in {
            "sha256",
            "content_hash",
            "request_hash",
            "manifest_hash",
            "result_sha256",
            "checksum",
        } and not _DIGEST.fullmatch(value):
            raise PortabilityError("portability_integrity_failed")
        if key.endswith("_json") and type(_load(value)) is not dict:
            raise PortabilityError("portability_invalid_package")


def _manifest_contract(manifest, limits: Limits) -> None:
    fields = {
        "format",
        "version",
        "database_schema",
        "id",
        "created_at",
        "tables",
        "blobs",
        "excluded_tables",
        "reconnect",
        "sanitized_fields",
        "content_policy",
    }
    if type(manifest) is not dict or set(manifest) != fields:
        raise PortabilityError("portability_unsupported_format")
    # Scan every key/string before accepting the public inventory as metadata.
    if _sanitize(manifest, 0, limits.max_depth, [0]) != manifest:
        raise PortabilityError("portability_sensitive_content")
    if (
        manifest["format"] != FORMAT
        or type(manifest["version"]) is not int
        or manifest["version"] not in (1, VERSION)
        or manifest["content_policy"] != CONTENT_POLICY
        or not _integer(manifest["database_schema"], 17)
        or (manifest["version"] == 1 and manifest["database_schema"] != 17)
        or (manifest["version"] == VERSION and manifest["database_schema"] < MIN_DATABASE_SCHEMA)
        or not _integer(manifest["sanitized_fields"])
        or not _uuid(manifest["id"])
        or not _timestamp(manifest["created_at"])
        or manifest["excluded_tables"] != sorted(EXCLUDED)
        or manifest["reconnect"] is not True
        or type(manifest["tables"]) is not dict
        or set(manifest["tables"])
        != (set(COLUMNS) - {"skill_process_origins"} if manifest["version"] == 1 else set(COLUMNS))
        or type(manifest["blobs"]) is not dict
        or any(not _inventory(item, rows=True) for item in manifest["tables"].values())
        or any(
            not _uuid(identifier) or not _inventory(item, rows=False)
            for identifier, item in manifest["blobs"].items()
        )
    ):
        raise PortabilityError("portability_unsupported_format")


def _private_dir(path: Path) -> None:
    for ancestor in [*reversed(path.parents), path]:
        try:
            info = ancestor.lstat()
        except FileNotFoundError:
            ancestor.mkdir(mode=0o700)
            info = ancestor.lstat()
        if ArtifactStore._is_link(info) or not stat.S_ISDIR(info.st_mode):
            raise PortabilityError("portability_storage_unavailable")
    if os.name != "nt":
        path.chmod(0o700)


def _write(path: Path, content: bytes):
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptor = os.open(path, flags, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())


def _read(path: Path, limit: int) -> bytes:
    for ancestor in [*reversed(path.parents), path.parent]:
        if ArtifactStore._is_link(ancestor.lstat()):
            raise PortabilityError("portability_storage_unavailable")
    original = path.lstat()
    if ArtifactStore._is_link(original) or not stat.S_ISREG(original.st_mode):
        raise PortabilityError("portability_storage_unavailable")
    descriptor = os.open(
        path, os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    )
    with os.fdopen(descriptor, "rb") as stream:
        info = os.fstat(stream.fileno())
        if (info.st_dev, info.st_ino) != (original.st_dev, original.st_ino):
            raise PortabilityError("portability_storage_unavailable")
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > limit:
            raise PortabilityError("portability_limit_exceeded")
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise PortabilityError("portability_limit_exceeded")
    return raw


def _graph(tables: dict[str, list[dict]]) -> None:
    indices = {}
    for table, records in tables.items():
        for record in records:
            _record_contract(table, record)
            for key in PRIMARY[table]:
                if key == "id" and table == "safety_control":
                    continue
                if key == "id" or key.endswith("_id"):
                    try:
                        if str(UUID(record[key])) != record[key]:
                            raise ValueError
                    except ValueError, TypeError, AttributeError:
                        raise PortabilityError("portability_invalid_graph") from None
            if table in {"skill_revisions", "routine_definitions"}:
                if (
                    hashlib.sha256(record["definition_json"].encode()).hexdigest()
                    != record["content_hash"]
                ):
                    raise PortabilityError("portability_integrity_failed")
        keys = PRIMARY[table]
        identities = [tuple(record[column] for column in keys) for record in records]
        if any(None in identity for identity in identities) or len(set(identities)) != len(records):
            raise PortabilityError("portability_invalid_graph")
        for parent, _, target in LINKS[table]:
            indices.setdefault(
                (parent, target),
                {tuple(record[column] for column in target) for record in tables[parent]},
            )
    for table, records in tables.items():
        for parent, source, target in LINKS[table]:
            for record in records:
                key = tuple(record[column] for column in source)
                if None not in key and key not in indices[parent, target]:
                    raise PortabilityError("portability_invalid_graph")
    if tables.get("skill_process_origins"):
        from bees_core.skills import ProcessReview

        messages = {record["id"]: record for record in tables["messages"]}
        tasks = {record["id"]: record for record in tables["tasks"]}
        runs = {record["id"]: record for record in tables["runs"]}
        for origin in tables["skill_process_origins"]:
            message, task, run = (
                messages[origin["message_id"]],
                tasks[origin["task_id"]],
                runs[origin["run_id"]],
            )
            try:
                review = ProcessReview.model_validate_json(origin["review_json"])
                checkpoint = _load(run["checkpoint_json"])
                deliverables = checkpoint.get("deliverables", {})
                if (
                    origin["actor"] != "user"
                    or run["status"] != "completed"
                    or message["role"] != "assistant"
                    or message["conversation_id"] != task["conversation_id"]
                    or checkpoint.get("result_message_id") != message["id"]
                    or not isinstance(deliverables, dict)
                    or deliverables.get("partial", False) is not False
                    or hashlib.sha256(message["content"].encode("utf-8")).hexdigest()
                    != origin["result_sha256"]
                    or any(url not in message["content"] for url in review.sources)
                ):
                    raise ValueError
            except ValueError, TypeError:
                raise PortabilityError("portability_invalid_graph") from None
    # Polymorphic domain events retain identifiers to excluded operational facts;
    # they are evidence, never credentials or authority.


class ExportService:
    def __init__(
        self,
        database: Database,
        artifacts: ArtifactStore,
        root: Path,
        *,
        limits: Limits | None = None,
    ):
        self.database = database
        self.artifacts = artifacts
        self.root = Path(root).absolute()
        self.limits = limits or Limits()

    def _schema(self, connection):
        names = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_schema WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        }
        if names != set(COLUMNS) | EXCLUDED:
            raise PortabilityError("portability_unsupported_schema")
        for table, expected in COLUMNS.items():
            actual = tuple(row[1] for row in connection.execute(f'PRAGMA table_info("{table}")'))
            if actual != expected:
                raise PortabilityError("portability_unsupported_schema")
        Database._validate_history(
            self.database._applied_migrations(connection), self.database._migrations()
        )
        if list(connection.execute("PRAGMA foreign_key_check")):
            raise PortabilityError("portability_invalid_graph")

    def create(self) -> Export:
        identifier = uuid4()
        stage = self.root / "staging" / str(identifier)
        ready = self.root / "ready"
        try:
            _private_dir(self.root)
            _private_dir(stage.parent)
            _private_dir(stage)
            _private_dir(ready)
            self._build(stage / "package.part", identifier)
            manifest = self.verify(stage / "package.part")
            raw = _read(stage / "package.part", self.limits.max_bytes)
            final = ready / f"{identifier}.zip"
            # UUID is generated internally; publication follows complete verification.
            os.rename(stage / "package.part", final)
            ArtifactStore._sync_directory(ready)
            ArtifactStore._sync_directory(stage)
            return Export(
                identifier,
                hashlib.sha256(raw).hexdigest(),
                len(raw),
                sum(item["rows"] for item in manifest["tables"].values()),
                len(manifest["blobs"]),
            )
        except BaseException as error:
            # A crash leaves only a private staging directory; it is never downloadable.
            try:
                _private_dir(stage)
                _write(
                    stage / "error.json",
                    _json(
                        {
                            "status": "failed",
                            "code": error.code
                            if isinstance(error, PortabilityError)
                            else "portability_interrupted",
                        }
                    ),
                )
            except OSError, PortabilityError:
                pass
            if not isinstance(error, Exception) or isinstance(error, PortabilityError):
                raise
            raise PortabilityError("portability_export_failed") from None

    def _build(self, path: Path, identifier: UUID):
        redactions = [0]
        tables = {}
        inventory = {}
        blobs = {}
        total = 0
        rows = 0
        with self.database.transaction(write=False) as connection:
            self._schema(connection)
            with zipfile.ZipFile(path, "x", compression=zipfile.ZIP_STORED) as archive:
                if os.name != "nt":
                    path.chmod(0o600)
                for table, columns in COLUMNS.items():
                    records = []
                    with archive.open(f"data/{table}.jsonl", "w") as member:
                        digest = hashlib.sha256()
                        size = 0
                        for values in connection.execute(
                            f'SELECT * FROM "{table}" ORDER BY ' + ",".join(PRIMARY[table])
                        ):
                            record = dict(zip(columns, values, strict=True))
                            for key, value in record.items():
                                if key == "execution_binding_json":
                                    record[key] = None
                                    redactions[0] += value is not None
                                elif key.endswith("_json") and value is not None:
                                    parsed = _load(value)
                                    clean = _sanitize(parsed, 0, self.limits.max_depth, redactions)
                                    if (
                                        table in {"skill_revisions", "routine_definitions"}
                                        and clean != parsed
                                    ):
                                        raise PortabilityError("portability_sensitive_content")
                                    record[key] = (
                                        value if clean == parsed else _json(clean).decode()
                                    )
                                else:
                                    record[key] = _sanitize(
                                        value, 0, self.limits.max_depth, redactions
                                    )
                            payload = _json(record) + b"\n"
                            total += len(payload)
                            rows += 1
                            self._limits(total, rows, len(COLUMNS) + len(blobs) + 1)
                            digest.update(payload)
                            size += len(payload)
                            member.write(payload)
                            records.append(record)
                    tables[table] = records
                    inventory[table] = {
                        "rows": len(records),
                        "bytes": size,
                        "sha256": digest.hexdigest(),
                    }
                _graph(tables)
                # Artifact contents are immutable after ready. Read files while the
                # original SQLite read transaction holds the exact metadata snapshot.
                for record in tables["artifacts"]:
                    if record["status"] != "ready":
                        continue
                    aid = str(UUID(record["id"]))
                    if aid != record["id"] or record["storage_key"] != "blob/v1/" + aid:
                        raise PortabilityError("portability_invalid_graph")
                    if record["size_bytes"] > self.limits.max_bytes - total:
                        raise PortabilityError("portability_limit_exceeded")
                    try:
                        content = _read(
                            self.artifacts.root / "blobs" / aid, self.limits.max_bytes - total
                        )
                    except FileNotFoundError:
                        raise PortabilityError("portability_blob_missing") from None
                    if (
                        len(content) != record["size_bytes"]
                        or hashlib.sha256(content).hexdigest() != record["sha256"]
                    ):
                        raise PortabilityError("portability_blob_integrity_failed")
                    # Supported artifact types are UTF-8; never inspect the vault.
                    _sanitize(content.decode("utf-8"), 0, self.limits.max_depth, redactions)
                    total += len(content)
                    blobs[aid] = {"bytes": len(content), "sha256": record["sha256"]}
                    self._limits(total, rows, len(COLUMNS) + len(blobs) + 1)
                    archive.writestr(f"blobs/{aid}", content)
                manifest = {
                    "format": FORMAT,
                    "version": VERSION,
                    "database_schema": max(row["version"] for row in tables["schema_migrations"]),
                    "id": str(identifier),
                    "created_at": datetime.now(UTC).isoformat(),
                    "tables": inventory,
                    "blobs": blobs,
                    "excluded_tables": sorted(EXCLUDED),
                    "reconnect": True,
                    "sanitized_fields": redactions[0],
                    "content_policy": CONTENT_POLICY,
                }
                archive.writestr("manifest.json", _json(manifest))
        if path.stat().st_size > self.limits.max_bytes:
            raise PortabilityError("portability_limit_exceeded")
        # O flush nativo do Windows exige handle gravável. Não reescreve bytes.
        with path.open("r+b") as stream:
            os.fsync(stream.fileno())

    def _limits(self, size, rows, files):
        if (
            size > self.limits.max_bytes
            or rows > self.limits.max_rows
            or files > self.limits.max_files
        ):
            raise PortabilityError("portability_limit_exceeded")

    def verify(self, path: Path) -> dict:
        """Reopen without extraction, generation or state changes; fail closed."""
        try:
            return self._verify(path)
        except PortabilityError:
            raise
        except (
            OSError,
            ValueError,
            KeyError,
            TypeError,
            AttributeError,
            RecursionError,
            zipfile.BadZipFile,
        ):
            raise PortabilityError("portability_invalid_package") from None

    def _verify(self, path: Path) -> dict:
        raw = _read(path, self.limits.max_bytes)
        return self._verify_bytes(raw)

    def _verify_bytes(self, raw: bytes) -> dict:
        # Archive is bounded before zip parsing. No extraction or name-derived paths.
        import io

        # v1 uses single-disk ZIP32 without comments. Bound central-directory
        # parsing before zipfile allocates a Python object for every entry.
        if len(raw) < 22 or raw[-22:-18] != b"PK\x05\x06":
            raise PortabilityError("portability_invalid_package")
        _, disk, start_disk, disk_files, file_count, central_size, central_offset, comment = (
            struct.unpack_from("<4s4H2LH", raw, len(raw) - 22)
        )
        if (
            disk
            or start_disk
            or disk_files != file_count
            or comment
            or central_offset + central_size != len(raw) - 22
            or file_count > self.limits.max_files
            or file_count < 1
        ):
            raise PortabilityError("portability_invalid_package")
        offset = central_offset
        for _ in range(file_count):
            if offset + 46 > len(raw) - 22 or raw[offset : offset + 4] != b"PK\x01\x02":
                raise PortabilityError("portability_invalid_package")
            name_size, extra_size, comment_size = struct.unpack_from("<3H", raw, offset + 28)
            offset += 46 + name_size + extra_size + comment_size
        if offset != len(raw) - 22:
            raise PortabilityError("portability_invalid_package")
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            members = archive.infolist()
            names = [member.filename for member in members]
            if (
                len(set(names)) != len(names)
                or "manifest.json" not in names
                or any(
                    member.compress_type != zipfile.ZIP_STORED
                    or member.flag_bits & 1
                    or stat.S_ISLNK(member.external_attr >> 16)
                    for member in members
                )
            ):
                raise PortabilityError("portability_invalid_package")
            self._limits(sum(member.file_size for member in members), 0, len(members))
            manifest = _load(archive.read("manifest.json"))
            _manifest_contract(manifest, self.limits)
            columns_for_version = {
                table: columns for table, columns in COLUMNS.items() if table in manifest["tables"]
            }
            expected = {"manifest.json"} | {f"data/{table}.jsonl" for table in columns_for_version}
            expected |= {f"blobs/{identifier}" for identifier in manifest["blobs"]}
            if set(names) != expected:
                raise PortabilityError("portability_invalid_package")
            tables = {}
            row_count = 0
            for table, columns in columns_for_version.items():
                data = archive.read(f"data/{table}.jsonl")
                item = manifest["tables"][table]
                if (
                    set(item) != {"rows", "bytes", "sha256"}
                    or item["bytes"] != len(data)
                    or hashlib.sha256(data).hexdigest() != item["sha256"]
                ):
                    raise PortabilityError("portability_integrity_failed")
                records = []
                for line in data.splitlines():
                    record = _load(line)
                    if set(record) != set(columns):
                        raise PortabilityError("portability_unsupported_schema")
                    # Validate even a self-consistent attacker-generated package.
                    for key, value in record.items():
                        clean = _sanitize(
                            _load(value) if key.endswith("_json") and value is not None else value,
                            0,
                            self.limits.max_depth,
                            [0],
                        )
                        if key == "execution_binding_json" and value is not None:
                            raise PortabilityError("portability_sensitive_content")
                        if key.endswith("_json") and value is not None and clean != _load(value):
                            raise PortabilityError("portability_sensitive_content")
                    records.append(record)
                    row_count += 1
                    self._limits(len(raw), row_count, len(members))
                if item["rows"] != len(records):
                    raise PortabilityError("portability_integrity_failed")
                tables[table] = records
            _graph(tables)
            try:
                Database._validate_history(
                    [
                        (row["version"], row["name"], row["checksum"])
                        for row in sorted(
                            tables["schema_migrations"], key=lambda row: row["version"]
                        )
                    ],
                    self.database._migrations(),
                )
            except RuntimeError:
                raise PortabilityError("portability_unsupported_schema") from None
            if type(manifest["database_schema"]) is not int or manifest["database_schema"] != max(
                record["version"] for record in tables["schema_migrations"]
            ):
                raise PortabilityError("portability_unsupported_schema")
            accepted = {
                record["id"]: record
                for record in tables["artifacts"]
                if record["status"] == "ready"
            }
            if set(accepted) != set(manifest["blobs"]):
                raise PortabilityError("portability_invalid_graph")
            for identifier, item in manifest["blobs"].items():
                if str(UUID(identifier)) != identifier or set(item) != {"bytes", "sha256"}:
                    raise PortabilityError("portability_invalid_package")
                data = archive.read(f"blobs/{identifier}")
                artifact = accepted[identifier]
                if (
                    item["bytes"] != len(data)
                    or item["sha256"] != hashlib.sha256(data).hexdigest()
                    or artifact["size_bytes"] != len(data)
                    or artifact["sha256"] != item["sha256"]
                    or artifact["storage_key"] != "blob/v1/" + identifier
                ):
                    raise PortabilityError("portability_blob_integrity_failed")
                _sanitize(data.decode("utf-8"), 0, self.limits.max_depth, [0])
            return manifest

    def download(self, identifier: UUID) -> bytes:
        path = self.root / "ready" / f"{identifier}.zip"
        try:
            raw = _read(path, self.limits.max_bytes)
        except FileNotFoundError:
            raise PortabilityError("portability_not_found") from None
        except OSError:
            raise PortabilityError("portability_storage_unavailable") from None
        try:
            self._verify_bytes(raw)
        except PortabilityError:
            raise
        except (
            OSError,
            ValueError,
            KeyError,
            TypeError,
            AttributeError,
            RecursionError,
            zipfile.BadZipFile,
        ):
            raise PortabilityError("portability_invalid_package") from None
        return raw
