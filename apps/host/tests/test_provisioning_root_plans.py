"""Inventário offline Root/ledger/journals reais; nenhum hardware ou rede."""

import hashlib
import json
import shutil
import socket
import subprocess
from datetime import datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from bees_host.provisioning.contracts import Claim, ProvisionError, canonical
from bees_host.provisioning.journal import Journal
from bees_host.provisioning.runner import Runner
from bees_host.provisioning.supervisor import Supervisor
from bees_host.provisioning.supervisor_store import SupervisorStore
from test_provisioning_root_store import initialize, reopen, snapshot
from test_provisioning_root_store import setup as root_setup  # noqa: F401
from test_provisioning_runner import claim_fixture
from test_provisioning_supervisor import Authority, Backend


@pytest.fixture
def fixture(request):
    setup = request.getfixturevalue("root_setup")
    store = initialize(setup)
    return setup, store


class RootAuthority(Authority):
    def begin_dispatch(self, claim, operation, client_request_id):
        self.claim = self.claim.model_copy(update={"revision": self.claim.revision + 1})
        permit = super().begin_dispatch(claim, operation, client_request_id)
        self.permit = permit.model_copy(update={"effect_request_id": client_request_id})
        return self.permit

    def record_receipt(self, claim, effect_request_id, client_request_id, result):
        self.claim = self.claim.model_copy(update={"revision": self.claim.revision + 1})
        return super().record_receipt(claim, effect_request_id, client_request_id, result)


def coherent_claim(binding):
    value = claim_fixture().model_dump(mode="json")
    plan = value["plan"]
    plan["installation_id"] = str(binding.installation_id)
    plan["host_id"] = str(binding.host_id)
    plan["vm_name"] = f"Bees-{binding.installation_id}-{plan['environment_id']}"
    value["installation_id"] = str(binding.installation_id)
    value["host_id"] = str(binding.host_id)
    value["provisioner_id"] = str(binding.provisioner_id)
    value["plan_hash"] = hashlib.sha256(canonical(plan)).hexdigest()
    return Claim.model_validate_json(canonical(value))


def add_plan(
    fixture,
    *,
    status="accepted",
    journal_exists=True,
    operations=False,
    unaccepted=False,
    initial_revision=1,
):
    setup, root_store = fixture
    root, binding, _, _ = setup
    enrollment_id = root_store.check_only().enrollment_store_id
    claim = coherent_claim(binding).model_copy(update={"revision": initial_revision})
    ledger = SupervisorStore.open(root / "state/ledger")
    journal = None
    try:
        with ledger.lock():
            ticket = ledger.prepare_acquisition(
                enrollment_store_id=enrollment_id,
                issue_request_id=binding.issue_request_id,
                provisioner_id=binding.provisioner_id,
                origin=binding.origin,
                plan_id=claim.plan_id,
                plan_hash=claim.plan_hash,
            )
            claim = claim.model_copy(update={"owner_id": ticket.owner_id})
            if status != "prepared" and not unaccepted:
                ledger.accept_acquisition(ticket, claim)
            if journal_exists:
                journal = Journal.initialize(root / "state/plans" / str(claim.plan_id), claim)
            if unaccepted:
                ledger.fail_acquisition(ticket, status)
            elif status == "completed":
                authority, backend = RootAuthority(claim), Backend(claim)
                template = SimpleNamespace(verify=lambda plan: root.parent / "fixture.iso")
                Supervisor(Runner(journal, authority, backend, template), ledger)._run_locked(
                    acquisition=ticket
                )
                claim = journal.claim
            elif status in {"running", "unknown", "stopped"}:
                ledger.begin(claim, acquisition=ticket)
                if operations:
                    with journal.lock():
                        journal.prepare("create_vhd")
                        journal.unknown("create_vhd")
                if status != "running":
                    ledger.finish(claim.claim_id, status)
    finally:
        if journal is not None:
            journal.close()
        ledger.close()
    return claim, root / "state/plans" / str(claim.plan_id)


def update_claim(path, change):
    journal = Journal(path)
    try:
        with journal.lock():
            value = journal.claim.model_dump(mode="json")
            change(value)
            claim = Claim.model_validate_json(canonical(value))
            journal.update_claim(claim)
    finally:
        journal.close()


@pytest.mark.parametrize("status", ["accepted", "running", "unknown", "stopped", "completed"])
def test_legitimate_journals_are_read_only_and_keep_blocked_state(fixture, monkeypatch, status):
    setup, store = fixture
    root, _, _, _ = setup
    add_plan(fixture, status=status, operations=status == "unknown")
    before = snapshot(root)

    def forbidden(*args, **kwargs):
        pytest.fail("Inventário offline tentou rede, geração, reparo ou processo")

    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(SupervisorStore, "acquisitions", forbidden)
    monkeypatch.setattr(SupervisorStore, "runs", forbidden)
    monkeypatch.setattr(SupervisorStore, "requests", forbidden)
    monkeypatch.setattr(Journal, "initialize", forbidden)
    result = store.check_only()
    assert result.configured_local and result.execution_blocked_local == (status != "completed")
    assert reopen(setup).check_only() == result
    assert snapshot(root) == before


@pytest.mark.parametrize("status", ["prepared", "accepted"])
def test_crash_before_journal_creation_is_visible_blocked_without_repair(fixture, status):
    setup, store = fixture
    root, _, _, _ = setup
    _, path = add_plan(fixture, status=status, journal_exists=False)
    before = snapshot(root)
    assert store.check_only().execution_blocked_local
    assert not path.exists() and snapshot(root) == before


@pytest.mark.parametrize("status", ["running", "unknown", "stopped", "completed"])
def test_run_without_its_journal_refused_and_evidence_preserved(fixture, status):
    setup, store = fixture
    root, _, _, _ = setup
    _, path = add_plan(fixture, status=status)
    shutil.rmtree(path)
    before = snapshot(root)
    with pytest.raises(ProvisionError):
        store.check_only()
    assert snapshot(root) == before and not path.exists()


def test_prepared_attempt_cannot_have_unaccepted_journal(fixture):
    _, store = fixture
    add_plan(fixture, status="prepared")
    with pytest.raises(ProvisionError):
        store.check_only()


def test_accepted_attempt_cannot_have_operation_even_if_no_effect(fixture):
    _, store = fixture
    _, path = add_plan(fixture)
    journal = Journal(path)
    try:
        with journal.lock():
            journal.prepare("create_vhd")
    finally:
        journal.close()
    with pytest.raises(ProvisionError):
        store.check_only()


def test_orphan_journal_refused(fixture):
    setup, store = fixture
    root, binding, _, _ = setup
    claim = coherent_claim(binding)
    journal = Journal.initialize(root / "state/plans" / str(claim.plan_id), claim)
    journal.close()
    with pytest.raises(ProvisionError):
        store.check_only()


@pytest.mark.parametrize(
    "name", ["not-a-uuid", "00000000-0000-0000-0000-000000000000", "UPPERCASE"]
)
def test_noncanonical_directories_refused(fixture, name):
    setup, store = fixture
    root, _, _, _ = setup
    _, path = add_plan(fixture)
    path.rename(root / "state/plans" / name)
    with pytest.raises(ProvisionError):
        store.check_only()


@pytest.mark.parametrize("file", ["identity.json", "owner.lock", "journal.sqlite3"])
def test_partial_accepted_journal_refused_without_repair(fixture, file):
    setup, store = fixture
    root, _, _, _ = setup
    _, path = add_plan(fixture)
    (path / file).unlink()
    before = snapshot(root)
    with pytest.raises(ProvisionError):
        store.check_only()
    assert snapshot(root) == before


@pytest.mark.parametrize("field", ["claim_id", "owner_id", "provisioner_id", "generation"])
def test_journal_claim_cannot_change_immutable_ownership(fixture, field):
    _, store = fixture
    _, path = add_plan(fixture)

    def change(value):
        value[field] = value[field] + 1 if field == "generation" else str(uuid4())

    update_claim(path, change)
    with pytest.raises(ProvisionError):
        store.check_only()


@pytest.mark.parametrize(
    "field", ["enrollment_store_id", "issue_request_id", "origin", "provisioner_id"]
)
def test_acquisition_must_belong_to_root_enrollment(fixture, field):
    setup, store = fixture
    root, _, _, _ = setup
    add_plan(fixture, status="prepared", journal_exists=False)
    value = "https://localhost:8181" if field == "origin" else str(uuid4())
    ledger = SupervisorStore.open(root / "state/ledger")
    try:
        ledger.connection.execute(f"UPDATE acquisitions SET {field}=?", (value,))
    finally:
        ledger.close()
    with pytest.raises(ProvisionError):
        store.check_only()


def test_swapped_journals_between_two_completed_plans_refused(fixture):
    setup, store = fixture
    root, _, _, _ = setup
    _, first = add_plan(fixture, status="completed")
    _, second = add_plan(fixture, status="completed")
    temporary = root.parent / "swapped"
    first.rename(temporary)
    second.rename(first)
    temporary.rename(second)
    with pytest.raises(ProvisionError):
        store.check_only()


def test_duplicate_journal_identity_refused_even_with_coherent_bindings(fixture):
    _, store = fixture
    _, first = add_plan(fixture, status="completed")
    _, second = add_plan(fixture, status="completed")
    first_marker = json.loads((first / "identity.json").read_bytes())
    second_marker = json.loads((second / "identity.json").read_bytes())
    second_marker["journal_id"] = first_marker["journal_id"]
    journal = Journal(second)
    try:
        # Atualiza os dois vínculos somente na fixture; o leitor individual é coerente.
        journal.connection.execute("UPDATE binding SET journal_id=?", (first_marker["journal_id"],))
    finally:
        journal.close()
    (second / "identity.json").write_bytes(canonical(second_marker))
    reader = Journal.open_read_only(second)
    reader.close()
    with pytest.raises(ProvisionError):
        store.check_only()


def test_journal_revision_and_lease_can_advance_independently_from_run(fixture):
    _, store = fixture
    _, path = add_plan(fixture, status="running")
    update_claim(
        path,
        lambda value: value.update(
            revision=value["revision"] + 3,
            lease_expires_at=(
                datetime.fromisoformat(value["lease_expires_at"]) + timedelta(seconds=30)
            ).isoformat(),
        ),
    )
    assert store.check_only().execution_blocked_local


def test_run_renewal_can_commit_before_journal_update_and_remains_blocked(fixture):
    setup, store = fixture
    root, _, _, _ = setup
    claim, _ = add_plan(fixture, status="running")
    ledger = SupervisorStore.open(root / "state/ledger")
    try:
        ledger.connection.execute(
            "UPDATE runs SET revision=revision+1,lease_expires_at=? WHERE claim_id=?",
            ((claim.lease_expires_at + timedelta(seconds=30)).isoformat(), str(claim.claim_id)),
        )
    finally:
        ledger.close()
    assert store.check_only().execution_blocked_local


def test_completed_run_requires_fully_confirmed_journal_and_final_claim(fixture):
    _, store = fixture
    _, path = add_plan(fixture, status="completed")
    update_claim(path, lambda value: value.update(status="dispatch_started"))
    with pytest.raises(ProvisionError):
        store.check_only()


def test_crash_after_all_receipts_still_blocks_if_run_not_completed(fixture):
    setup, store = fixture
    root, _, _, _ = setup
    claim, _ = add_plan(fixture, status="completed")
    ledger = SupervisorStore.open(root / "state/ledger")
    try:
        ledger.connection.execute(
            "UPDATE runs SET status='running' WHERE claim_id=?", (str(claim.claim_id),)
        )
        ledger.connection.execute(
            "UPDATE acquisitions SET status='running',revision=3 WHERE claim_id=?",
            (str(claim.claim_id),),
        )
    finally:
        ledger.close()
    assert store.check_only().execution_blocked_local


@pytest.mark.parametrize("status", ["unknown", "stopped"])
@pytest.mark.parametrize("journal_exists", [False, True])
def test_unaccepted_quarantine_has_no_journal_and_never_adopts_one(
    fixture,
    status,
    journal_exists,
):
    _, store = fixture
    add_plan(fixture, status=status, journal_exists=journal_exists, unaccepted=True)
    if journal_exists:
        with pytest.raises(ProvisionError):
            store.check_only()
    else:
        assert store.check_only().execution_blocked_local


@pytest.mark.parametrize("field", ["revision", "lease_expires_at"])
def test_journal_cannot_regress_below_accepted_claim(fixture, field):
    _, store = fixture
    _, path = add_plan(fixture, initial_revision=3)

    def change(value):
        if field == "revision":
            value[field] -= 1
        else:
            value[field] = (datetime.fromisoformat(value[field]) - timedelta(seconds=1)).isoformat()

    update_claim(path, change)
    with pytest.raises(ProvisionError):
        store.check_only()


@pytest.mark.parametrize("field", ["status", "lease_expires_at"])
def test_equal_initial_revision_requires_same_metadata(fixture, field):
    _, store = fixture
    _, path = add_plan(fixture, status="running")

    def change(value):
        value[field] = (
            "dispatch_started"
            if field == "status"
            else (datetime.fromisoformat(value[field]) + timedelta(seconds=1)).isoformat()
        )

    update_claim(path, change)
    with pytest.raises(ProvisionError):
        store.check_only()


def test_stopped_run_cannot_have_an_operation(fixture):
    _, store = fixture
    add_plan(fixture, status="stopped", operations=True)
    with pytest.raises(ProvisionError):
        store.check_only()


@pytest.mark.parametrize("field", ["revision", "lease_expires_at"])
def test_completed_run_cannot_be_ahead_of_final_journal(fixture, field):
    setup, store = fixture
    root, _, _, _ = setup
    claim, _ = add_plan(fixture, status="completed")
    value = (
        claim.revision + 1
        if field == "revision"
        else (claim.lease_expires_at + timedelta(seconds=1)).isoformat()
    )
    ledger = SupervisorStore.open(root / "state/ledger")
    try:
        ledger.connection.execute(f"UPDATE runs SET {field}=?", (value,))
    finally:
        ledger.close()
    with pytest.raises(ProvisionError):
        store.check_only()


def test_all_receipts_but_unknown_run_remains_quarantined(fixture):
    setup, store = fixture
    root, _, _, _ = setup
    claim, _ = add_plan(fixture, status="completed")
    ledger = SupervisorStore.open(root / "state/ledger")
    try:
        ledger.connection.execute(
            "UPDATE runs SET status='unknown' WHERE claim_id=?", (str(claim.claim_id),)
        )
        ledger.connection.execute(
            "UPDATE acquisitions SET status='unknown' WHERE claim_id=?", (str(claim.claim_id),)
        )
    finally:
        ledger.close()
    assert store.check_only().execution_blocked_local


def test_effect_uuid_must_equal_begin_request_even_when_journal_is_valid(fixture):
    _, store = fixture
    _, path = add_plan(fixture, status="completed")
    journal = Journal(path)
    try:
        journal.connection.execute(
            "UPDATE operations SET effect_request_id=? WHERE operation='verify'", (str(uuid4()),)
        )
    finally:
        journal.close()
    reader = Journal.open_read_only(path)
    reader.close()
    with pytest.raises(ProvisionError):
        store.check_only()
