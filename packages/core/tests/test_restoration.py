"""Private staging restore: real DB/blob reopen, authority removal and atomic failure."""

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from test_portability import state as export_fixture

from bees_core.artifacts import ArtifactStore
from bees_core.models import (
    Action,
    Approval,
    ModelCall,
    PluginInstallation,
    Policy,
    Run,
    ToolGrant,
    UsageEntry,
    utc_now,
)
from bees_core.portability import COLUMNS, EXCLUDED, ExportService, Limits
from bees_core.restoration import RestorationError, RestoreService
from bees_core.routines import CreateRoutine, RoutineDefinition, RoutineService
from bees_core.safety import SafetyCommand, SafetyService
from bees_core.skills import EnableSkill, SkillService
from bees_core.storage.database import Database
from bees_core.storage.store import StateStore
from bees_core.tools import CATALOG_MANIFEST, digest


@pytest.fixture
def state(tmp_path):
    source = export_fixture.__wrapped__(tmp_path / "source")
    target = tmp_path / "inactive"
    restorer = RestoreService(source[-1], {"fresh": target})
    return source, target, restorer


def package(source):
    value = source[-1].create()
    return source[-1].download(value.id)


def records(db):
    with db.transaction(write=False) as connection:
        return {table: list(connection.execute(f'SELECT * FROM "{table}"')) for table in COLUMNS}


def test_reopen_preserves_records_blobs_sources_and_stops_destination(state):
    source, target, restorer = state
    db, agent, artifacts, artifact, skill, _ = source
    before = records(db)
    raw = package(source)
    report = restorer.restore("fresh", raw)
    assert report.status == "inactive" and report.blobs == 1
    assert report.package_sha256 == hashlib.sha256(raw).hexdigest()
    assert (target / "source.zip").read_bytes() == raw
    assert restorer.report("fresh") == report
    assert (
        str(target) not in report.model_dump_json() and str(db.path) not in report.model_dump_json()
    )
    destination = Database(target / "bees.sqlite3")
    destination.require_current_schema()
    assert (
        ArtifactStore(destination, target / "artifacts").read(agent.id, artifact.id)[1]
        == artifacts.read(agent.id, artifact.id)[1]
    )
    assert SkillService(destination).get(agent.id, skill.id).current == skill.current
    with destination.transaction(write=False) as connection:
        assert connection.execute("PRAGMA integrity_check").get == "ok"
        assert list(connection.execute("PRAGMA foreign_key_check")) == []
        for table in EXCLUDED:
            assert connection.execute(f'SELECT COUNT(*) FROM "{table}"').get == 0
        assert connection.execute("SELECT status FROM safety_control").get == "stopped"
        assert connection.execute("SELECT model_config_json,status FROM agents").get == (
            "{}",
            "paused",
        )
    assert records(db) == before
    assert restorer.destinations() == [
        {"id": "fresh", "available": False, "state": "inactive_only"}
    ]


def sensitive_state(source):
    db, agent, _, _, skill, _ = source
    with StateStore(db).transaction() as unit:
        task = unit.tasks.list(agent_id=agent.id)[0]
        run = unit.runs.create(
            Run(task_id=task.id, status="running", provider_config=agent.provider_config)
        )
        plugin = unit.plugins.create(
            PluginInstallation(
                manifest=CATALOG_MANIFEST.model_dump(mode="json"),
                manifest_hash=digest(CATALOG_MANIFEST.model_dump(mode="json")),
                enabled=True,
            )
        )
        grant = unit.tool_grants.create(
            ToolGrant(
                agent_id=agent.id, plugin_id=plugin.id, tool_name="text.normalize", enabled=True
            )
        )
        allow = unit.policies.create(Policy(agent_id=agent.id, name="Allow", effect="allow"))
        deny = unit.policies.create(Policy(agent_id=agent.id, name="Deny", effect="deny"))
        prepared = unit.actions.create(
            Action(run_id=run.id, tool_name="text.normalize", status="ready")
        )
        inflight = unit.actions.create(
            Action(run_id=run.id, tool_name="text.normalize", status="dispatch_started")
        )
        unknown = unit.actions.create(
            Action(
                run_id=run.id,
                tool_name="text.normalize",
                status="outcome_unknown",
                unknown_acknowledged_at=utc_now(),
            )
        )
        approvals = [
            unit.approvals.create(
                Approval(
                    action_id=prepared.id,
                    status=status,
                    decision="allow_once" if status == "approved" else None,
                    metadata={"consumed_at": utc_now().isoformat()} if consumed else {},
                )
            )
            for status, consumed in (
                ("approved", False),
                ("approved", True),
                ("revoked", False),
                ("denied", False),
            )
        ]
        calls = []
        for ordinal, status in enumerate(("prepared", "dispatch_started", "outcome_unknown"), 1):
            call = ModelCall(
                run_id=run.id,
                ordinal=ordinal,
                task_control_revision=0,
                agent_revision=1,
                lease_generation=1,
                provider_config=agent.provider_config,
                request={"messages": [{"role": "user", "content": "Synthetic test"}]},
                request_hash="a" * 64,
            )
            call = call.model_copy(
                update={
                    "request_hash": unit.model_calls.request_digest(
                        call.provider_config, call.request, call.snapshot
                    )
                }
            )
            call = unit.model_calls.create(call)
            if status != "prepared":
                call = unit.model_calls._change(call, status=status)
            calls.append(call)
        usage = unit.usage_entries.create(
            UsageEntry(
                agent_id=agent.id,
                source="task",
                model_call_id=calls[1].id,
                conversation_id=task.conversation_id,
                provider_kind="openai_compatible",
                model="test",
                reserved_tokens=128,
                estimate_method="test",
            )
        )
    SkillService(db).enable(
        agent.id,
        skill.id,
        EnableSkill(client_request_id=uuid4(), expected_revision=1, version=1, enabled=True),
    )
    routine_service = RoutineService(db, clock=lambda: datetime(2026, 1, 1, 8, tzinfo=UTC))
    routine = routine_service.create(
        agent.id,
        CreateRoutine(
            client_request_id=uuid4(),
            active=True,
            definition=RoutineDefinition(
                name="Daily",
                instructions="Review",
                schedule={"frequency": "daily", "time": "09:00"},
                timezone="UTC",
                skill_id=skill.id,
                skill_version=1,
                environment="none",
                destination={"conversation_id": task.conversation_id},
                notification="always",
            ),
        ),
    )
    routine_service.evaluate(
        agent.id,
        routine.id,
        expected_revision=routine.revision,
        now=datetime(2026, 1, 1, 9, tzinfo=UTC),
    )
    return (
        task,
        run,
        plugin,
        grant,
        allow,
        deny,
        prepared,
        inflight,
        unknown,
        approvals,
        calls,
        usage,
        routine,
    )


def test_authority_disabled_unknown_and_consumed_decisions_preserved_without_execution(state):
    source, target, restorer = state
    db, agent, _, _, skill, _ = source
    values = sensitive_state(source)
    (
        task,
        run,
        plugin,
        grant,
        allow,
        deny,
        prepared,
        inflight,
        unknown,
        approvals,
        calls,
        usage,
        routine,
    ) = values
    before = records(db)
    report = restorer.restore("fresh", package(source))
    destination = Database(target / "bees.sqlite3")
    with StateStore(destination).transaction() as unit:
        assert unit.tasks.get(task.id).status == "paused"
        assert unit.tasks.get(task.id).desired_state == "paused"
        assert unit.runs.get(run.id).status == "paused"
        assert (
            not unit.plugins.get(plugin.id).enabled and not unit.tool_grants.get(grant.id).enabled
        )
        assert (
            unit.policies.get(allow.id).status == "revoked" and unit.policies.get(deny.id) == deny
        )
        assert unit.actions.get(prepared.id).status == "cancelled"
        assert unit.actions.get(inflight.id).status == "outcome_unknown"
        assert unit.actions.get(unknown.id) == unknown
        assert [unit.approvals.get(value.id).status for value in approvals] == [
            "revoked",
            "approved",
            "revoked",
            "denied",
        ]
        assert unit.approvals.get(approvals[1].id) == approvals[1]
        assert [unit.model_calls.get(call.id).status for call in calls] == [
            "cancelled",
            "outcome_unknown",
            "outcome_unknown",
        ]
        preserved_call = unit.model_calls.get(calls[2].id)
        assert preserved_call.model_dump(exclude={"provider_config"}) == calls[2].model_dump(
            exclude={"provider_config"}
        )
        assert preserved_call.provider_config["secret_ref"] is None
        entry = unit.usage_entries.get(usage.id)
        assert entry.status == "unknown" and entry.counted_tokens() == usage.reserved_tokens
        assert unit.execution.claim_next(uuid4(), now=utc_now()) is None
    restored_routine = RoutineService(destination).get(agent.id, routine.id)
    assert restored_routine.status == "paused" and restored_routine.definition == routine.definition
    assert restored_routine.revision == routine.revision and restored_routine.next_run_at is None
    assert SkillService(destination).get(agent.id, skill.id).enabled_version is None
    with destination.transaction(write=False) as connection:
        assert connection.execute("SELECT status FROM routine_occurrences").get == "cancelled"
    assert "unknown_outcomes" in report.review_required
    SafetyService(destination).command(
        SafetyCommand(client_request_id=uuid4(), kind="resume", expected_revision=2)
    )
    with StateStore(destination).transaction() as unit:
        assert unit.execution.claim_next(uuid4(), now=utc_now()) is None
    assert records(db) == before


def test_deleted_artifact_parent_restores_immutable_lineage_without_disabling_triggers(state):
    source, target, restorer = state
    db, agent, artifacts, first, _, _ = source
    second = artifacts.create_version(agent.id, first.id, content=b"second-version")
    with db.transaction() as connection:
        connection.execute("UPDATE artifacts SET status='deleted' WHERE id=?", (str(first.id),))
    report = restorer.restore("fresh", package(source))
    assert report.blobs == 1
    destination = Database(target / "bees.sqlite3")
    with destination.transaction(write=False) as connection:
        assert (
            connection.execute("SELECT status FROM artifacts WHERE id=?", (str(first.id),)).get
            == "deleted"
        )
        assert connection.execute(
            "SELECT previous_id FROM artifacts WHERE id=?", (str(second.id),)
        ).get == str(first.id)
    assert (
        ArtifactStore(destination, target / "artifacts").read(agent.id, second.id)[1]
        == b"second-version"
    )


@pytest.mark.parametrize("where", ["database", "publication"])
def test_interruption_never_exposes_partial_destination(state, monkeypatch, where):
    source, target, restorer = state

    def crash(*args):
        raise KeyboardInterrupt()

    monkeypatch.setattr(restorer, "_load_database" if where == "database" else "_publish", crash)
    with pytest.raises(KeyboardInterrupt):
        restorer.restore("fresh", package(source))
    assert not target.exists()
    assert list(target.parent.glob(".bees-restore-*.staging/error.json"))
    with pytest.raises(RestorationError, match="restoration_not_found"):
        restorer.report("fresh")


def test_existing_empty_or_populated_destination_never_overwritten(state):
    source, target, restorer = state
    raw = package(source)
    target.mkdir()
    with pytest.raises(RestorationError, match="destination_exists"):
        restorer.restore("fresh", raw)
    (target / "user.txt").write_text("user content")
    with pytest.raises(RestorationError, match="destination_exists"):
        restorer.restore("fresh", raw)
    assert (target / "user.txt").read_text() == "user content"


def test_atomic_publication_refuses_concurrent_empty_directory(state, monkeypatch):
    source, target, restorer = state
    original = restorer._publish

    def race(stage, destination):
        destination.mkdir()
        return original(stage, destination)

    monkeypatch.setattr(restorer, "_publish", race)
    with pytest.raises(RestorationError, match="destination_exists"):
        restorer.restore("fresh", package(source))
    assert target.is_dir() and list(target.iterdir()) == []


def test_concurrent_import_has_one_published_installation(state):
    source, target, restorer = state
    raw = package(source)

    def restore():
        try:
            return restorer.restore("fresh", raw).restore_id
        except RestorationError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: restore(), range(2)))
    assert (
        sum(
            isinstance(value, str) and value == "restoration_destination_exists"
            for value in results
        )
        == 1
    )
    assert restorer.report("fresh").restore_id in results
    assert (target / "bees.sqlite3").is_file()


def test_untrusted_package_unknown_target_limits_and_active_overlap_are_rejected(state):
    source, target, restorer = state
    with pytest.raises(RestorationError):
        restorer.restore("fresh", b"truncated secret-content")
    assert not target.exists()
    with pytest.raises(RestorationError, match="destination_unknown"):
        restorer.restore("../../active", package(source))
    with pytest.raises(ValueError, match="overlaps active"):
        RestoreService(source[-1], {"bad": source[0].path.parent / "nested"})
    with pytest.raises(ValueError, match="overlap"):
        RestoreService(source[-1], {"a": target, "b": target / "nested"})
    verifier = ExportService(source[0], source[2], source[-1].root, limits=Limits(max_bytes=32))
    with pytest.raises(RestorationError, match="limit_exceeded"):
        RestoreService(verifier, {"fresh": target}).restore("fresh", b"x" * 33)


def test_symlink_target_and_report_tampering_fail_closed(state, tmp_path):
    source, target, restorer = state
    outside = tmp_path / "outside"
    outside.mkdir()
    target.symlink_to(outside, target_is_directory=True)
    with pytest.raises(RestorationError, match="destination_exists"):
        restorer.restore("fresh", package(source))
    assert list(outside.iterdir()) == []
    target.unlink()
    restorer.restore("fresh", package(source))
    (target / "restoration.json").write_text(json.dumps({"private_path": str(outside)}))
    with pytest.raises(RestorationError, match="invalid_report") as error:
        restorer.report("fresh")
    assert str(outside) not in str(error.value)


def test_report_rejects_changed_source_evidence_and_naive_time(state):
    source, target, restorer = state
    raw = package(source)
    restorer.restore("fresh", raw)
    (target / "source.zip").write_bytes(b"changed-private-evidence")
    with pytest.raises(RestorationError, match="invalid_report"):
        restorer.report("fresh")
    (target / "source.zip").write_bytes(raw)
    path = target / "restoration.json"
    report = json.loads(path.read_bytes())
    report["created_at"] = "2026-10-17T12:00:00"
    path.write_text(json.dumps(report))
    with pytest.raises(RestorationError, match="invalid_report"):
        restorer.report("fresh")


def test_restored_unknown_still_blocks_resume_without_human_acknowledgement(state):
    from bees_core.tasks import TaskCommandInput, TaskError, TaskService

    source, target, restorer = state
    task = sensitive_state(source)[0]
    restorer.restore("fresh", package(source))
    destination = Database(target / "bees.sqlite3")
    before = records(destination)
    with pytest.raises(TaskError, match="unknown_requires_ack"):
        TaskService(destination).command(
            source[1].id,
            task.id,
            TaskCommandInput(
                client_request_id=uuid4(), expected_revision=task.revision, kind="resume"
            ),
        )
    assert records(destination) == before
