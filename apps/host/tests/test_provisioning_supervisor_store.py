"""Ledger/ACL/SQLite/locks reais; nenhum efeito de hardware ou credencial real."""

import json
import os
import sqlite3
import subprocess
import sys
from contextlib import closing
from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from bees_host.provisioning.contracts import Claim, ProvisionError
from bees_host.provisioning.supervisor_store import SupervisorStore
from test_guest_bridge_tls import private_bridge_directory
from test_provisioning_runner import claim_fixture


def initialize(base, claim):
    return SupervisorStore.initialize(
        base / "supervisor", installation_id=claim.installation_id, host_id=claim.host_id
    )


def successor(claim, **changes):
    values = claim.model_dump(mode="json")
    values.update(changes)
    return Claim.model_validate_json(json.dumps(values))


def renewed(claim):
    return successor(
        claim,
        revision=claim.revision + 1,
        lease_expires_at=(claim.lease_expires_at + timedelta(seconds=30)).isoformat(),
    )


def test_requests_persist_before_network_and_confirm_monotonically():
    with private_bridge_directory() as base:
        claim = claim_fixture()
        store = initialize(base, claim)
        try:
            assert store.connection.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
            assert store.connection.execute("PRAGMA synchronous").fetchone()[0] == 2
            assert store.connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
            with store.lock():
                store.begin(claim)
                request_id = store.request(claim.claim_id, "renew")
                assert store.requests()[0]["request_id"] == str(request_id)
                assert store.requests()[0]["status"] == "pending"
                store.confirm(request_id, renewed(claim))
                assert store.requests()[0]["status"] == "confirmed"
                assert store.runs()[0]["revision"] == claim.revision + 1
                with pytest.raises(ProvisionError, match="provision_transition_invalid"):
                    store.confirm(request_id, renewed(claim))
                store.finish(claim.claim_id, "completed")
            directory = store.directory
            store.close()
            store = SupervisorStore.open(directory)
            assert store.runs()[0]["status"] == "completed"
            with store.lock():
                with pytest.raises(ProvisionError, match="provision_claim_reused"):
                    store.begin(claim)
                store.begin(successor(claim, claim_id=str(uuid4())))
        finally:
            store.close()


@pytest.mark.parametrize("status", ["running", "stopped", "unknown"])
def test_any_noncompleted_run_blocks_another_plan_or_owner(status):
    with private_bridge_directory() as base:
        claim = claim_fixture()
        store = initialize(base, claim)
        try:
            with store.lock():
                store.begin(claim)
                if status != "running":
                    store.finish(claim.claim_id, status)
            store.close()
            store = SupervisorStore.open(base / "supervisor")
            with store.lock():
                next_claim = successor(
                    claim, claim_id=str(uuid4()), owner_id=str(uuid4()), generation=2
                )
                with pytest.raises(ProvisionError, match="provision_reconciliation_required"):
                    store.begin(next_claim)
            assert len(store.runs()) == 1
        finally:
            store.close()


@pytest.mark.parametrize("operation", ["begin", "request", "confirm", "finish"])
def test_mutations_require_whole_run_lock(operation):
    with private_bridge_directory() as base:
        claim = claim_fixture()
        store = initialize(base, claim)
        try:
            with store.lock():
                store.begin(claim)
                request_id = store.request(claim.claim_id, "renew")
            methods = {
                "begin": lambda: store.begin(claim),
                "request": lambda: store.request(claim.claim_id, "unknown"),
                "confirm": lambda: store.confirm(request_id, renewed(claim)),
                "finish": lambda: store.finish(claim.claim_id, "unknown"),
            }
            with pytest.raises(ProvisionError, match="provision_lock_required"):
                methods[operation]()
            assert store.requests()[0]["status"] == "pending"
        finally:
            store.close()


def test_pending_request_cannot_be_replaced_or_hidden_by_completion():
    with private_bridge_directory() as base:
        claim = claim_fixture()
        store = initialize(base, claim)
        try:
            with store.lock():
                store.begin(claim)
                request_id = store.request(claim.claim_id, "renew")
                with pytest.raises(ProvisionError, match="provision_reconciliation_required"):
                    store.request(claim.claim_id, "renew")
                with pytest.raises(ProvisionError, match="provision_reconciliation_required"):
                    store.finish(claim.claim_id, "completed")
                store.finish(claim.claim_id, "unknown")
            assert store.requests()[0]["request_id"] == str(request_id)
            assert store.requests()[0]["status"] == "pending"
        finally:
            store.close()


@pytest.mark.parametrize(
    "change",
    [
        {"owner_id": str(uuid4())},
        {"generation": 2},
        {"provisioner_id": str(uuid4())},
        {"claim_id": str(uuid4())},
        {"revision": 1, "status": "dispatch_started"},
        {"revision": 2, "status": "aborted"},
    ],
)
def test_confirm_rejects_binding_fencing_status_and_same_revision_changes(change):
    with private_bridge_directory() as base:
        claim = claim_fixture()
        store = initialize(base, claim)
        try:
            with store.lock():
                store.begin(claim)
                request_id = store.request(claim.claim_id, "renew")
                with pytest.raises(ProvisionError):
                    store.confirm(request_id, successor(claim, **change))
                assert store.requests()[0]["status"] == "pending"
                assert store.runs()[0]["revision"] == 1
        finally:
            store.close()


def test_unknown_confirmation_has_own_binding_and_does_not_release_host():
    with private_bridge_directory() as base:
        claim = claim_fixture()
        store = initialize(base, claim)
        try:
            with store.lock():
                store.begin(claim)
                request_id = store.request(claim.claim_id, "unknown")
                with pytest.raises(ProvisionError, match="provision_claim_stale"):
                    store.confirm(request_id, renewed(claim))
                store.confirm(request_id, successor(claim, revision=2, status="outcome_unknown"))
                store.finish(claim.claim_id, "unknown")
                with pytest.raises(ProvisionError, match="provision_reconciliation_required"):
                    store.begin(successor(claim, claim_id=str(uuid4())))
            assert store.requests()[0]["status"] == "confirmed"
        finally:
            store.close()


def test_lease_must_not_go_backwards_and_revision_must_not_regress():
    with private_bridge_directory() as base:
        claim = claim_fixture()
        store = initialize(base, claim)
        try:
            with store.lock():
                store.begin(renewed(claim))
                request_id = store.request(claim.claim_id, "renew")
                with pytest.raises(ProvisionError, match="provision_claim_stale"):
                    store.confirm(request_id, claim)
                rollback = successor(claim, revision=3)
                with pytest.raises(ProvisionError, match="provision_claim_stale"):
                    store.confirm(request_id, rollback)
        finally:
            store.close()


def test_quarantine_precedes_unknown_network_request_and_confirmation_preserves_it():
    with private_bridge_directory() as base:
        claim = claim_fixture()
        store = initialize(base, claim)
        try:
            with store.lock():
                store.begin(claim)
                store.finish(claim.claim_id, "unknown")
                request_id = store.request(claim.claim_id, "unknown")
                store.confirm(request_id, successor(claim, revision=2, status="outcome_unknown"))
                assert store.runs()[0]["status"] == "unknown"
                with pytest.raises(ProvisionError, match="provision_transition_invalid"):
                    store.request(claim.claim_id, "renew")
                with pytest.raises(ProvisionError, match="provision_reconciliation_required"):
                    store.finish(claim.claim_id, "completed")
            store.close()
            store = SupervisorStore.open(base / "supervisor")
            assert store.runs()[0]["status"] == "unknown"
            assert store.requests()[0]["status"] == "confirmed"
        finally:
            store.close()


def test_lost_renew_can_report_unknown_without_erasing_pending_evidence():
    with private_bridge_directory() as base:
        claim = claim_fixture()
        store = initialize(base, claim)
        try:
            with store.lock():
                store.begin(claim)
                renew_id = store.request(claim.claim_id, "renew")
                store.finish(claim.claim_id, "unknown")
                unknown_id = store.request(claim.claim_id, "unknown")
                store.confirm(unknown_id, successor(claim, revision=3, status="outcome_unknown"))
            store.close()
            store = SupervisorStore.open(base / "supervisor")
            requests = {row["request_id"]: row for row in store.requests()}
            assert requests[str(renew_id)]["status"] == "pending"
            assert requests[str(unknown_id)]["status"] == "confirmed"
            assert store.runs()[0]["status"] == "unknown"
            with store.lock():
                with pytest.raises(ProvisionError, match="provision_transition_invalid"):
                    store.confirm(renew_id, renewed(claim))
                with pytest.raises(ProvisionError, match="provision_reconciliation_required"):
                    store.begin(successor(claim, claim_id=str(uuid4())))
        finally:
            store.close()


def test_pending_unknown_blocks_further_renew_requests_even_before_finish():
    with private_bridge_directory() as base:
        claim = claim_fixture()
        store = initialize(base, claim)
        try:
            with store.lock():
                store.begin(claim)
                store.request(claim.claim_id, "unknown")
                with pytest.raises(ProvisionError, match="provision_reconciliation_required"):
                    store.request(claim.claim_id, "renew")
                with pytest.raises(ProvisionError, match="provision_reconciliation_required"):
                    store.request(claim.claim_id, "unknown")
        finally:
            store.close()


def test_wrong_host_installation_and_plan_are_refused():
    with private_bridge_directory() as base:
        claim = claim_fixture()
        store = initialize(base, claim)
        try:
            with store.lock():
                with pytest.raises(ProvisionError, match="provision_supervisor_binding_invalid"):
                    store.begin(claim_fixture())
                store.begin(claim)
                request_id = store.request(claim.claim_id, "renew")
                changed = claim.model_dump(mode="json")
                changed["plan"]["cpu_count"] = 4
                from bees_host.provisioning.contracts import Plan

                changed["plan_hash"] = Plan.model_validate_json(
                    json.dumps(changed["plan"])
                ).digest()
                with pytest.raises(ProvisionError, match="provision_claim_stale"):
                    store.confirm(request_id, Claim.model_validate_json(json.dumps(changed)))
        finally:
            store.close()


def test_native_lock_between_real_processes_and_crash_preserves_running_request():
    with private_bridge_directory() as base:
        claim = claim_fixture()
        store = initialize(base, claim)
        try:
            code = """
import sys
from pathlib import Path
from bees_host.provisioning.supervisor_store import SupervisorStore
from bees_host.provisioning.contracts import ProvisionError
s=SupervisorStore.open(Path(sys.argv[1]))
try:
 with s.lock(): sys.exit(9)
except ProvisionError as e:
 sys.exit(0 if str(e)=='provision_owner_running' else 10)
"""
            with store.lock():
                result = subprocess.run(
                    [sys.executable, "-c", code, str(store.directory)],
                    capture_output=True,
                    timeout=10,
                    check=False,
                )
                assert result.returncode == 0 and not result.stdout and not result.stderr
            code = """
import os,sys
from pathlib import Path
from bees_host.provisioning.supervisor_store import SupervisorStore
from bees_host.provisioning.contracts import Claim
s=SupervisorStore.open(Path(sys.argv[1]))
claim=Claim.model_validate_json(sys.stdin.read())
with s.lock():
 s.begin(claim)
 s.request(claim.claim_id,'renew')
 os._exit(31)
"""
            result = subprocess.run(
                [sys.executable, "-c", code, str(store.directory)],
                input=claim.model_dump_json(),
                text=True,
                capture_output=True,
                timeout=10,
                check=False,
            )
            assert result.returncode == 31 and not result.stdout and not result.stderr
            with store.lock():
                assert store.runs()[0]["status"] == "running"
                assert store.requests()[0]["status"] == "pending"
                with pytest.raises(ProvisionError, match="provision_reconciliation_required"):
                    store.begin(successor(claim, claim_id=str(uuid4())))
        finally:
            store.close()


@pytest.mark.parametrize("name", ["identity.json", "owner.lock", "supervisor.sqlite3"])
def test_loss_never_recreates_and_initialize_refuses_existing_directory(name):
    with private_bridge_directory() as base:
        claim = claim_fixture()
        store = initialize(base, claim)
        directory = store.directory
        store.close()
        (directory / name).unlink()
        with pytest.raises(ProvisionError, match="provision_state_missing"):
            SupervisorStore.open(directory)
        assert not (directory / name).exists()
        with pytest.raises(ProvisionError, match="provision_state_already_present"):
            initialize(base, claim)


@pytest.mark.parametrize("content", [b"", b"private corrupt bytes"])
def test_corruption_is_not_reinitialized(content):
    with private_bridge_directory() as base:
        claim = claim_fixture()
        store = initialize(base, claim)
        directory = store.directory
        store.close()
        (directory / "supervisor.sqlite3").write_bytes(content)
        with pytest.raises(ProvisionError, match="^provision_state_invalid$"):
            SupervisorStore.open(directory)
        assert (directory / "supervisor.sqlite3").read_bytes() == content


def test_initialization_crash_keeps_identity_and_refuses_retry(monkeypatch):
    import bees_host.provisioning.supervisor_store as module

    original = module.create

    def fail(path, content):
        if path.name == "supervisor.sqlite3":
            raise OSError("private crash")
        original(path, content)

    with private_bridge_directory() as base:
        claim = claim_fixture()
        monkeypatch.setattr(module, "create", fail)
        with pytest.raises(ProvisionError, match="provision_state_unavailable"):
            initialize(base, claim)
        assert (base / "supervisor" / "identity.json").exists()
        with pytest.raises(ProvisionError, match="provision_state_missing"):
            SupervisorStore.open(base / "supervisor")
        with pytest.raises(ProvisionError, match="provision_state_already_present"):
            initialize(base, claim)


@pytest.mark.parametrize(
    "sql",
    [
        "PRAGMA application_id=1",
        "PRAGMA user_version=9",
        "UPDATE binding SET host_id='00000000-0000-0000-0000-000000000000'",
        "UPDATE runs SET owner_id='bad'",
        "UPDATE runs SET plan_hash='bad'",
        "UPDATE runs SET lease_expires_at='2026-10-07'",
        "UPDATE runs SET generation=1.5",
        "UPDATE runs SET revision=0",
        "UPDATE requests SET owner_id='00000000-0000-0000-0000-000000000001'",
        "UPDATE requests SET status='confirmed'",
        "UPDATE requests SET revision_after=2",
        "DROP INDEX one_pending_request",
    ],
)
def test_schema_and_persisted_row_incoherence_fail_closed(sql):
    with private_bridge_directory() as base:
        claim = claim_fixture()
        store = initialize(base, claim)
        with store.lock():
            store.begin(claim)
            store.request(claim.claim_id, "renew")
        directory = store.directory
        store.close()
        with closing(sqlite3.connect(directory / "supervisor.sqlite3")) as connection:
            connection.execute("PRAGMA ignore_check_constraints=ON")
            connection.execute(sql)
            connection.commit()
        with pytest.raises(ProvisionError, match="^provision_state_invalid$"):
            SupervisorStore.open(directory)


def test_private_files_hardlinks_extra_files_and_marker_duplicates_are_refused():
    from test_guest_bridge_private import allow_everyone

    with private_bridge_directory() as base:
        claim = claim_fixture()
        store = initialize(base, claim)
        directory = store.directory
        store.close()
        os.link(directory / "supervisor.sqlite3", directory / "copy")
        with pytest.raises(ProvisionError, match="provision_private_required"):
            SupervisorStore.open(directory)
        (directory / "copy").unlink()
        marker = directory / "identity.json"
        original = marker.read_bytes()
        marker.write_bytes(original[:-1] + b',"format":1}')
        with pytest.raises(ProvisionError, match="provision_state_invalid"):
            SupervisorStore.open(directory)
        marker.write_bytes(original)
        allow_everyone(directory / "owner.lock")
        with pytest.raises(ProvisionError, match="provision_private_required"):
            SupervisorStore.open(directory)


def test_snapshot_is_bounded_and_contains_no_paths_or_credentials():
    with private_bridge_directory() as base:
        claim = claim_fixture()
        store = initialize(base, claim)
        try:
            with store.lock():
                store.begin(claim)
            snapshot = json.dumps(store.runs())
            assert str(base) not in snapshot and 'plan"' not in snapshot and "bp_" not in snapshot
            for limit in (0, -1, 1001, True, "10"):
                with pytest.raises(ProvisionError, match="provision_snapshot_limit_invalid"):
                    store.runs(limit=limit)
            with pytest.raises(ProvisionError, match="provision_supervisor_binding_invalid"):
                SupervisorStore.initialize(
                    base / "other", installation_id=UUID(int=0), host_id=uuid4()
                )
        finally:
            store.close()
