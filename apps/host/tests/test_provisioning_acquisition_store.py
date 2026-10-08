"""Aquisição durável/locks/crash reais; sem rede, inscrição ou efeito de hardware."""

import os
import sqlite3
import subprocess
import sys
import threading
from contextlib import closing
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from bees_host.provisioning.contracts import ProvisionError
from bees_host.provisioning.supervisor_store import SupervisorStore
from test_guest_bridge_tls import private_bridge_directory
from test_provisioning_runner import claim_fixture
from test_provisioning_supervisor_store import renewed, successor


def prepare(store, claim, **changes):
    values = {
        "enrollment_store_id": uuid4(),
        "issue_request_id": uuid4(),
        "provisioner_id": claim.provisioner_id,
        "origin": "http://127.0.0.1:8080",
        "plan_id": claim.plan_id,
        "plan_hash": claim.plan_hash,
    }
    values.update(changes)
    return store.prepare_acquisition(**values)


@pytest.fixture
def fixture():
    with private_bridge_directory() as base:
        claim = claim_fixture()
        store = SupervisorStore.initialize_for_acquisition(
            base / "host", installation_id=claim.installation_id, host_id=claim.host_id
        )
        try:
            yield store, claim
        finally:
            store.close()


def acquire(store, claim):
    ticket = prepare(store, claim)
    claim = successor(claim, owner_id=str(ticket.owner_id))
    store.accept_acquisition(ticket, claim)
    return ticket, claim


def test_uuid_and_owner_durable_before_claim_without_secret_or_plan(fixture):
    store, claim = fixture
    with store.lock():
        store.assert_acquisition_ready()
        ticket = prepare(store, claim)
        assert not store.connection.in_transaction
        with closing(sqlite3.connect(store.path)) as independent:
            row = independent.execute(
                "SELECT request_id,owner_id,status FROM acquisitions"
            ).fetchone()
            assert row == (str(ticket.request_id), str(ticket.owner_id), "prepared")
        snapshot = store.acquisitions()[0]
        assert snapshot["claim_id"] is None and snapshot["revision"] == 1
        assert not {"plan", "credential", "path", "token"}.intersection(snapshot)
        assert store.runs() == [] and store.requests() == []
        with pytest.raises(ProvisionError, match="provision_reconciliation_required"):
            prepare(store, claim, plan_id=uuid4())
        with pytest.raises(ProvisionError, match="provision_reconciliation_required"):
            store.assert_acquisition_ready()
        assert len(store.acquisitions()) == 1


def test_accept_begin_finish_linked_atomically_and_next_plan_only(fixture):
    store, claim = fixture
    with store.lock():
        ticket, claim = acquire(store, claim)
        assert store.acquisitions()[0]["status"] == "accepted" and store.runs() == []
        store.begin(claim, acquisition=ticket)
        assert store.acquisitions()[0]["status"] == store.runs()[0]["status"] == "running"
        request_id = store.request(claim.claim_id, "renew")
        store.confirm(request_id, renewed(claim))
        store.finish(claim.claim_id, "completed")
        assert store.acquisitions()[0]["revision"] == 4
        assert store.acquisitions()[0]["claim_revision"] == 1
        assert store.runs()[0]["revision"] == 2
        with pytest.raises(ProvisionError, match="provision_claim_reused"):
            prepare(store, claim)
        next_ticket = prepare(store, claim, plan_id=uuid4())
        assert (
            next_ticket.owner_id != ticket.owner_id and next_ticket.request_id != ticket.request_id
        )
        store.fail_acquisition(next_ticket)
    with store.lock():
        assert len(store.acquisitions()) == 2


@pytest.mark.parametrize("phase", ["prepared", "accepted", "running"])
@pytest.mark.parametrize("status", ["unknown", "stopped"])
def test_failure_keeps_evidence_and_blocks_every_next_owner(fixture, phase, status):
    store, claim = fixture
    with store.lock():
        ticket = prepare(store, claim)
        claim = successor(claim, owner_id=str(ticket.owner_id))
        if phase != "prepared":
            store.accept_acquisition(ticket, claim)
        if phase == "running":
            store.begin(claim, acquisition=ticket)
            store.finish(claim.claim_id, status)
        else:
            store.fail_acquisition(ticket, status)
        row = store.acquisitions()[0]
        assert row["status"] == status and row["request_id"] == str(ticket.request_id)
        assert (row["claim_id"] is not None) == (phase != "prepared")
        with pytest.raises(ProvisionError, match="provision_reconciliation_required"):
            prepare(store, claim, plan_id=uuid4())
        with pytest.raises(ProvisionError):
            store.begin(claim, acquisition=ticket)
    with store.lock():
        assert store.acquisitions()[0] == row


@pytest.mark.parametrize(
    "changes",
    [
        {"owner_id": uuid4()},
        {"installation_id": uuid4()},
        {"host_id": uuid4()},
        {"provisioner_id": uuid4()},
        {"plan_id": uuid4()},
        {"plan_hash": "a" * 64},
        {"status": "dispatch_started"},
        {"status": "confirmed"},
        {"generation": True},
        {"revision": 2**63},
        {"lease_expires_at": datetime.now(UTC) - timedelta(seconds=1)},
    ],
)
def test_accept_rejects_changed_binding_status_expiry_and_unvalidated_claim(fixture, changes):
    store, claim = fixture
    with store.lock():
        ticket = prepare(store, claim)
        claim = successor(claim, owner_id=str(ticket.owner_id))
        with pytest.raises(ProvisionError, match="provision_claim_stale"):
            store.accept_acquisition(ticket, claim.model_copy(update=changes))
        assert store.acquisitions()[0]["status"] == "prepared" and store.runs() == []
        store.accept_acquisition(ticket, claim)
        with pytest.raises(ProvisionError):
            store.accept_acquisition(ticket, claim)


@pytest.mark.parametrize(
    "changes",
    [
        {"origin": "https://example.com"},
        {"origin": [1]},
        {"plan_hash": "bad"},
        {"plan_id": UUID(int=0)},
        {"enrollment_store_id": "private"},
        {"issue_request_id": None},
    ],
)
def test_prepare_rejects_untrusted_context_without_creating_evidence(fixture, changes):
    store, claim = fixture
    with store.lock():
        with pytest.raises(ProvisionError, match="provision_supervisor_binding_invalid"):
            prepare(store, claim, **changes)
        assert store.acquisitions() == []


@pytest.mark.parametrize("kind", ["copy", "other_instance", "new_lock", "missing"])
def test_ticket_must_be_live_same_instance_identity_and_lock_epoch(fixture, kind):
    store, claim = fixture
    with store.lock():
        ticket, claim = acquire(store, claim)
        if kind == "copy":
            with pytest.raises(ProvisionError):
                store.begin(claim, acquisition=replace(ticket))
        elif kind == "other_instance":
            other = SupervisorStore.open(store.directory)
            try:
                with pytest.raises(ProvisionError):
                    other.begin(claim, acquisition=ticket)
            finally:
                other.close()
        elif kind == "missing":
            with pytest.raises(ProvisionError, match="provision_acquisition_required"):
                store.begin(claim)
        assert store.runs() == []
    with store.lock():
        with pytest.raises(ProvisionError, match="provision_reconciliation_required"):
            store.begin(claim, acquisition=ticket)
        assert store.acquisitions()[0]["status"] == "accepted"


@pytest.mark.parametrize("field,value", [("revision", 4), ("origin", "http://localhost:8080")])
def test_live_ticket_checks_persisted_cas_and_immutable_context(fixture, field, value):
    store, claim = fixture
    with store.lock():
        ticket = prepare(store, claim)
        claim = successor(claim, owner_id=str(ticket.owner_id))
        store.connection.execute(f"UPDATE acquisitions SET {field}=?", (value,))
        with pytest.raises(ProvisionError, match="provision_acquisition_stale"):
            store.accept_acquisition(ticket, claim)
        assert store.runs() == []


@pytest.mark.parametrize("operation", ["begin", "completed", "unknown"])
def test_run_and_acquisition_transition_rollback_together(fixture, operation):
    store, claim = fixture
    with store.lock():
        ticket, claim = acquire(store, claim)
        if operation != "begin":
            store.begin(claim, acquisition=ticket)

        def deny_acquisition_update(action, table, *_):
            return (
                sqlite3.SQLITE_DENY
                if action == sqlite3.SQLITE_UPDATE and table == "acquisitions"
                else sqlite3.SQLITE_OK
            )

        store.connection.set_authorizer(deny_acquisition_update)
        try:
            with pytest.raises(ProvisionError, match="provision_state_unavailable"):
                if operation == "begin":
                    store.begin(claim, acquisition=ticket)
                else:
                    store.finish(claim.claim_id, operation)
        finally:
            store.connection.set_authorizer(None)
        assert not store.connection.in_transaction
        if operation == "begin":
            assert store.runs() == [] and store.acquisitions()[0]["status"] == "accepted"
        else:
            assert store.runs()[0]["status"] == store.acquisitions()[0]["status"] == "running"


@pytest.mark.parametrize("version", [1, 2])
def test_all_mutations_reject_foreign_thread_before_sqlite_or_network(fixture, version):
    initial, claim = fixture
    if version == 1:
        store = SupervisorStore.initialize(
            initial.directory.parent / "legacy",
            installation_id=claim.installation_id,
            host_id=claim.host_id,
        )
    else:
        store = initial
    try:
        with store.lock():
            if version == 2:
                ticket, claim = acquire(store, claim)
                store.begin(claim, acquisition=ticket)
            else:
                store.begin(claim)
            request_id = store.request(claim.claim_id, "renew")
            calls = [
                store.assert_locked,
                lambda: store.begin(claim),
                lambda: store.request(claim.claim_id, "renew"),
                lambda: store.confirm(request_id, renewed(claim)),
                lambda: store.finish(claim.claim_id, "unknown"),
                store.migrate_for_acquisition,
            ]
            if version == 2:
                calls += [
                    lambda: prepare(store, claim),
                    lambda: store.accept_acquisition(ticket, claim),
                    lambda: store.fail_acquisition(ticket),
                    store.assert_acquisition_ready,
                ]
            results = []

            def foreign():
                for call in calls:
                    try:
                        call()
                    except BaseException as error:
                        results.append(error)

            thread = threading.Thread(target=foreign)
            thread.start()
            thread.join(timeout=5)
            assert not thread.is_alive() and len(results) == len(calls)
            assert all(
                type(error) is ProvisionError and str(error) == "provision_lock_required"
                for error in results
            )
            assert store.runs()[0]["status"] == "running"
    finally:
        if version == 1:
            store.close()


@pytest.mark.parametrize("phase", ["prepared", "accepted", "running"])
def test_process_crash_never_reacquires_or_reconstructs_ticket(fixture, phase):
    store, claim = fixture
    code = """
import os,sys
from pathlib import Path
from uuid import uuid4
from bees_host.provisioning.supervisor_store import SupervisorStore
from bees_host.provisioning.contracts import Claim
store=SupervisorStore.open(Path(sys.argv[1]))
claim=Claim.model_validate_json(sys.stdin.read())
with store.lock():
 ticket=store.prepare_acquisition(enrollment_store_id=uuid4(),issue_request_id=uuid4(),
  provisioner_id=claim.provisioner_id,origin='http://127.0.0.1:8080',plan_id=claim.plan_id,plan_hash=claim.plan_hash)
 if sys.argv[2]!='prepared':
  claim=claim.model_copy(update={'owner_id':ticket.owner_id})
  store.accept_acquisition(ticket,claim)
 if sys.argv[2]=='running':store.begin(claim,acquisition=ticket)
 os._exit(31)
"""
    result = subprocess.run(
        [sys.executable, "-c", code, str(store.directory), phase],
        input=claim.model_dump_json(),
        text=True,
        capture_output=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 31 and not result.stdout and not result.stderr
    with store.lock():
        assert store.acquisitions()[0]["status"] == phase
        with pytest.raises(ProvisionError, match="provision_reconciliation_required"):
            prepare(store, claim, plan_id=uuid4())
        with pytest.raises(ProvisionError, match="provision_acquisition_required"):
            store.begin(claim)
        if phase == "running":
            with pytest.raises(ProvisionError, match="provision_reconciliation_required"):
                store.finish(claim.claim_id, "completed")
            with pytest.raises(ProvisionError, match="provision_reconciliation_required"):
                store.request(claim.claim_id, "renew")
            assert store.runs()[0]["status"] == "running" and store.requests() == []


@pytest.mark.parametrize("completed", [False, True])
def test_migration_is_explicit_quiesced_preserves_every_legacy_row(fixture, completed):
    initial, claim = fixture
    legacy = SupervisorStore.initialize(
        initial.directory.parent / "legacy",
        installation_id=claim.installation_id,
        host_id=claim.host_id,
    )
    try:
        assert legacy.version == 1
        with pytest.raises(ProvisionError, match="provision_lock_required"):
            legacy.migrate_for_acquisition()
        with legacy.lock():
            with pytest.raises(ProvisionError, match="provision_acquisition_required"):
                legacy.assert_acquisition_ready()
            if completed:
                legacy.begin(claim)
                request_id = legacy.request(claim.claim_id, "renew")
                legacy.confirm(request_id, renewed(claim))
                legacy.finish(claim.claim_id, "completed")
            before = (
                legacy.runs(),
                legacy.requests(),
                (legacy.directory / "identity.json").read_bytes(),
            )
            legacy.migrate_for_acquisition()
            assert legacy.version == 2 and legacy.acquisitions() == []
            assert before == (
                legacy.runs(),
                legacy.requests(),
                (legacy.directory / "identity.json").read_bytes(),
            )
            if completed:
                with pytest.raises(ProvisionError, match="provision_claim_reused"):
                    prepare(legacy, claim)
        reopened = SupervisorStore.open(legacy.directory)
        try:
            assert reopened.version == 2 and reopened.acquisitions() == []
        finally:
            reopened.close()
    finally:
        legacy.close()


@pytest.mark.parametrize("status", ["running", "unknown", "stopped"])
@pytest.mark.parametrize("pending", [False, True])
def test_migration_refuses_uncertain_legacy_and_keeps_exact_state(fixture, status, pending):
    initial, claim = fixture
    legacy = SupervisorStore.initialize(
        initial.directory.parent / "legacy",
        installation_id=claim.installation_id,
        host_id=claim.host_id,
    )
    try:
        with legacy.lock():
            legacy.begin(claim)
            if pending:
                legacy.request(claim.claim_id, "renew")
            if status != "running":
                legacy.finish(claim.claim_id, status)
            before = legacy.runs(), legacy.requests()
            with pytest.raises(ProvisionError, match="provision_reconciliation_required"):
                legacy.migrate_for_acquisition()
            assert legacy.version == 1 and before == (legacy.runs(), legacy.requests())
            assert not legacy.connection.execute(
                "SELECT 1 FROM sqlite_master WHERE name='acquisitions'"
            ).fetchone()
    finally:
        legacy.close()


def test_schema_failure_rolls_back_entire_migration(fixture):
    initial, claim = fixture
    legacy = SupervisorStore.initialize(
        initial.directory.parent / "legacy",
        installation_id=claim.installation_id,
        host_id=claim.host_id,
    )
    try:
        with legacy.lock():
            legacy.connection.set_authorizer(
                lambda action, *_: (
                    sqlite3.SQLITE_DENY
                    if action == sqlite3.SQLITE_CREATE_INDEX
                    else sqlite3.SQLITE_OK
                )
            )
            try:
                with pytest.raises(ProvisionError, match="provision_state_unavailable"):
                    legacy.migrate_for_acquisition()
            finally:
                legacy.connection.set_authorizer(None)
            assert (
                legacy.version
                == legacy.connection.execute("PRAGMA user_version").fetchone()[0]
                == 1
            )
            assert not legacy.connection.execute(
                "SELECT 1 FROM sqlite_master WHERE name='acquisitions'"
            ).fetchone()
            legacy.migrate_for_acquisition()
            assert legacy.version == 2
    finally:
        legacy.close()


@pytest.mark.parametrize("phase", ["before_commit", "after_commit"])
def test_real_migration_crash_preserves_old_rows_and_atomic_version(fixture, phase):
    initial, claim = fixture
    legacy = SupervisorStore.initialize(
        initial.directory.parent / "legacy",
        installation_id=claim.installation_id,
        host_id=claim.host_id,
    )
    with legacy.lock():
        legacy.begin(claim)
        request_id = legacy.request(claim.claim_id, "renew")
        legacy.confirm(request_id, renewed(claim))
        legacy.finish(claim.claim_id, "completed")
    before = legacy.runs(), legacy.requests(), (legacy.directory / "identity.json").read_bytes()
    directory = legacy.directory
    legacy.close()
    code = """
import os,sys,sqlite3
from pathlib import Path
from bees_host.provisioning.supervisor_store import SupervisorStore
store=SupervisorStore.open(Path(sys.argv[1]))
def authorize(action,argument,*args):
 if sys.argv[2]=='before_commit' and action==sqlite3.SQLITE_TRANSACTION and argument=='COMMIT':
  os._exit(41)
 return sqlite3.SQLITE_OK
with store.lock():
 store.connection.set_authorizer(authorize)
 store.migrate_for_acquisition()
 os._exit(42)
"""
    result = subprocess.run(
        [sys.executable, "-c", code, str(directory), phase],
        capture_output=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == (41 if phase == "before_commit" else 42)
    assert not result.stdout and not result.stderr
    if phase == "before_commit" and os.name == "nt":
        # O journal criado por SQLite não possui a DACL protected exigida pelo
        # componente. Crash conserva evidência e bloqueia abrir, sem auto-reparo.
        journal = directory / "supervisor.sqlite3-journal"
        evidence = journal.read_bytes(), (directory / "supervisor.sqlite3").read_bytes()
        with pytest.raises(ProvisionError, match="provision_private_required"):
            SupervisorStore.open(directory)
        assert evidence == (journal.read_bytes(), (directory / "supervisor.sqlite3").read_bytes())
        assert (directory / "identity.json").read_bytes() == before[2]
        return
    reopened = SupervisorStore.open(directory)
    try:
        with reopened.lock():
            assert reopened.version == (1 if phase == "before_commit" else 2)
            assert before == (
                reopened.runs(),
                reopened.requests(),
                (directory / "identity.json").read_bytes(),
            )
            if phase == "after_commit":
                assert reopened.acquisitions() == []
            else:
                assert not reopened.connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE name='acquisitions'"
                ).fetchone()
    finally:
        reopened.close()


@pytest.mark.parametrize("operation", ["accept", "begin", "finish"])
def test_commit_failure_invalidates_ram_ticket_and_keeps_durable_quarantine(fixture, operation):
    store, claim = fixture
    with store.lock():
        ticket = prepare(store, claim)
        claim = successor(claim, owner_id=str(ticket.owner_id))
        if operation != "accept":
            store.accept_acquisition(ticket, claim)
        if operation == "finish":
            store.begin(claim, acquisition=ticket)
        before = store.acquisitions(), store.runs()

        def deny_commit(action, argument, *_):
            return (
                sqlite3.SQLITE_DENY
                if action == sqlite3.SQLITE_TRANSACTION and argument == "COMMIT"
                else sqlite3.SQLITE_OK
            )

        store.connection.set_authorizer(deny_commit)
        try:
            with pytest.raises(ProvisionError, match="provision_state_unavailable"):
                if operation == "accept":
                    store.accept_acquisition(ticket, claim)
                elif operation == "begin":
                    store.begin(claim, acquisition=ticket)
                else:
                    store.finish(claim.claim_id, "completed")
        finally:
            store.connection.set_authorizer(None)
        assert before == (store.acquisitions(), store.runs())
        assert not store.connection.in_transaction
        with pytest.raises(ProvisionError, match="provision_reconciliation_required"):
            store.begin(claim, acquisition=ticket)
        with pytest.raises(ProvisionError, match="provision_reconciliation_required"):
            store.assert_acquisition_ready()


def test_unknown_notice_stays_live_without_releasing_pending_renewal(fixture):
    store, claim = fixture
    with store.lock():
        ticket, claim = acquire(store, claim)
        store.begin(claim, acquisition=ticket)
        renewal = store.request(claim.claim_id, "renew")
        with pytest.raises(ProvisionError, match="provision_reconciliation_required"):
            store.finish(claim.claim_id, "completed")
        store.finish(claim.claim_id, "unknown")
        unknown = store.request(claim.claim_id, "unknown")
        store.confirm(unknown, successor(claim, revision=2, status="outcome_unknown"))
        requests = {row["request_id"]: row for row in store.requests()}
        assert requests[str(renewal)]["status"] == "pending"
        assert requests[str(unknown)]["status"] == "confirmed"
        with pytest.raises(ProvisionError):
            store.request(claim.claim_id, "renew")
        with pytest.raises(ProvisionError):
            store.finish(claim.claim_id, "completed")
    with store.lock():
        with pytest.raises(ProvisionError, match="provision_reconciliation_required"):
            store.confirm(renewal, renewed(claim))
        assert store.acquisitions()[0]["status"] == store.runs()[0]["status"] == "unknown"


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE acquisitions SET origin='https://example.com'",
        "UPDATE acquisitions SET owner_id='bad'",
        "UPDATE acquisitions SET generation=NULL",
        "UPDATE acquisitions SET revision=99",
        "UPDATE acquisitions SET claim_revision=99",
        "UPDATE acquisitions SET status='accepted'",
        "UPDATE acquisitions SET status='completed'",
        "UPDATE acquisitions SET claim_status='confirmed'",
        "UPDATE acquisitions SET lease_expires_at='2026-10-08'",
        "UPDATE acquisitions SET plan_hash='bad'",
        "DELETE FROM acquisitions",
        "DROP INDEX one_open_acquisition",
        "PRAGMA user_version=1",
    ],
)
def test_v2_schema_binding_lifecycle_and_rows_fail_closed(fixture, sql):
    store, claim = fixture
    with store.lock():
        ticket, claim = acquire(store, claim)
        store.begin(claim, acquisition=ticket)
    with closing(sqlite3.connect(store.path)) as independent:
        independent.execute("PRAGMA ignore_check_constraints=ON")
        independent.execute(sql)
        independent.commit()
    with pytest.raises(ProvisionError, match="provision_state_invalid"):
        SupervisorStore.open(store.directory)
