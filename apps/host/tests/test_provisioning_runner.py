"""Doubles explícitos de comandos; journaling/ACL/locks são reais, nenhuma VM é criada."""

import hashlib
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from bees_host.provisioning.contracts import (
    ISO_SHA256,
    ISO_SIZE,
    OPERATIONS,
    PAYLOAD_SHA256,
    PAYLOAD_SIZE,
    Claim,
    DispatchPermit,
    Inventory,
    ProvisionError,
    canonical,
)
from bees_host.provisioning.journal import Journal
from bees_host.provisioning.runner import Runner
from test_guest_bridge_tls import private_bridge_directory


def claim_fixture():
    installation, environment = str(uuid4()), str(uuid4())
    catalog = {
        "format": 1,
        "template_id": "linux-desktop-v1",
        "image_iso": {"sha256": ISO_SHA256, "size": ISO_SIZE},
        "payload": {"sha256": PAYLOAD_SHA256, "size": PAYLOAD_SIZE},
    }
    plan = {
        "format": 1,
        "plan_id": str(uuid4()),
        "installation_id": installation,
        "agent_id": str(uuid4()),
        "host_id": str(uuid4()),
        "host_revision": 1,
        "environment_id": environment,
        "environment_revision": 1,
        "job_id": str(uuid4()),
        "template_id": "linux-desktop-v1",
        "driver": "hyperv",
        "mode": "create_stopped_hardware",
        "vm_name": f"Bees-{installation}-{environment}",
        "cpu_count": 3,
        "memory_bytes": 3 * 1024**3,
        "disk_bytes": 23 * 1024**3,
        "image_iso_sha256": ISO_SHA256,
        "image_iso_size": ISO_SIZE,
        "payload_sha256": PAYLOAD_SHA256,
        "payload_size": PAYLOAD_SIZE,
        "catalog_hash": hashlib.sha256(canonical(catalog)).hexdigest(),
        "network": "none",
        "storage_profile": "bees-managed-v1",
        "boot": False,
    }
    value = {
        "claim_id": str(uuid4()),
        "installation_id": installation,
        "host_id": plan["host_id"],
        "provisioner_id": str(uuid4()),
        "plan_id": plan["plan_id"],
        "plan_hash": hashlib.sha256(canonical(plan)).hexdigest(),
        "owner_id": str(uuid4()),
        "generation": 1,
        "revision": 1,
        "status": "claimed",
        "plan": plan,
        "lease_expires_at": (datetime.now(UTC) + timedelta(minutes=10)).isoformat(),
    }
    return Claim.model_validate_json(json.dumps(value))


class FakeAuthority:
    def __init__(self, claim):
        self.claim = claim
        self.revoked = False
        self.cached = False
        self.lose_receipt = False
        self.assertions = 0
        self.receipts = []

    def assert_current(self, claim):
        self.assertions += 1
        if self.revoked:
            raise RuntimeError("private authority detail")
        return self.claim

    def begin_dispatch(self, claim, operation, client_request_id):
        self.claim = self.claim.model_copy(update={"status": "dispatch_started"})
        self.permit = DispatchPermit(
            claim=self.claim,
            effect_request_id=uuid4(),
            operation=operation,
            status="dispatch_started",
            cached=self.cached,
            dispatch_allowed=not self.cached,
        )
        return self.permit

    def record_receipt(self, claim, effect_request_id, client_request_id, result):
        self.receipts.append(result)
        if self.permit.operation == "verify":
            self.claim = self.claim.model_copy(update={"status": "confirmed"})
        if self.lose_receipt:
            raise ConnectionError("private lost response")
        return DispatchPermit(
            claim=self.claim,
            effect_request_id=effect_request_id,
            operation=self.permit.operation,
            status="confirmed",
            cached=False,
            dispatch_allowed=False,
        )


class FakeBackend:
    def __init__(self, claim):
        self.claim = claim
        self.effects = []
        self.alive = False
        self.fail_after_go = False
        self.on_ready = None
        self.on_go = None
        self.inventory = Inventory(
            found=False,
            vm_id=None,
            owned=False,
            powered_off=False,
            generation2=False,
            cpu_count=None,
            memory_bytes=None,
            disk_bytes=None,
            fixed_vhdx=False,
            network_adapter_count=0,
            iso_attached=False,
        )

    def preflight(self):
        pass

    def inspect(self, *args):
        return self.inventory

    def running(self, pid, start_ticks):
        return self.alive

    def start(self, plan, operation, request_id, iso, vm_id):
        backend = self
        self.alive = True

        class FakeCommand:
            def ready(self):
                if backend.on_ready:
                    backend.on_ready()
                return 12345, 133900000000000000

            def go(self):
                backend.effects.append(operation)
                changes = {
                    "found": True,
                    "owned": True,
                    "fixed_vhdx": True,
                    "disk_bytes": plan.disk_bytes,
                }
                if operation == "create_vm":
                    changes |= {
                        "vm_id": uuid4(),
                        "powered_off": True,
                        "generation2": True,
                        "memory_bytes": plan.memory_bytes,
                        "cpu_count": 1,
                        "network_adapter_count": 1,
                    }
                elif operation == "configure_vm":
                    changes |= {"cpu_count": plan.cpu_count, "memory_bytes": plan.memory_bytes}
                elif operation == "remove_nic":
                    changes |= {"network_adapter_count": 0}
                elif operation == "attach_iso":
                    changes |= {"iso_attached": True}
                backend.inventory = backend.inventory.model_copy(update=changes)
                if backend.on_go:
                    backend.on_go()

            def finish(self):
                if backend.fail_after_go:
                    raise RuntimeError("private command details")
                return backend.inventory

            def close(self):
                backend.alive = False

        return FakeCommand()


@pytest.fixture
def fixture():
    with private_bridge_directory() as directory:
        claim = claim_fixture()
        journal = Journal.initialize(directory / str(claim.plan_id), claim)
        authority, backend = FakeAuthority(claim), FakeBackend(claim)
        template = SimpleNamespace(verify=lambda plan: directory / "fixture.iso")
        try:
            yield Runner(journal, authority, backend, template), authority, backend, journal
        finally:
            journal.close()


def test_six_operations_dynamic_resources_stopped_zero_network(fixture):
    runner, authority, backend, journal = fixture
    for operation in OPERATIONS:
        result = runner.execute_next()
        assert result.verified and journal.operations()[operation]["status"] == "confirmed"
    assert backend.effects == list(OPERATIONS)
    assert authority.assertions == 18
    assert journal.claim.status == "confirmed"
    final = authority.receipts[-1]
    assert (final.cpu_count, final.memory_bytes, final.disk_bytes) == (3, 3 * 1024**3, 23 * 1024**3)
    assert final.network_none and final.powered_off and final.image_iso_sha256 == ISO_SHA256
    with pytest.raises(ProvisionError, match="provision_already_completed"):
        runner.execute_next()
    assert not hasattr(final, "usable")


def test_revocation_while_ready_never_sends_go(fixture):
    runner, authority, backend, journal = fixture
    backend.on_ready = lambda: setattr(authority, "revoked", True)
    with pytest.raises(ProvisionError, match="^provision_operation_unknown$"):
        runner.execute_next()
    assert not backend.effects and not backend.alive
    assert journal.operations()["create_vhd"]["status"] == "unknown"


def test_revocation_after_go_preserves_unknown_and_no_result_publication(fixture):
    runner, authority, backend, journal = fixture
    backend.on_go = lambda: setattr(authority, "revoked", True)
    with pytest.raises(ProvisionError, match="provision_operation_unknown"):
        runner.execute_next()
    assert backend.effects == ["create_vhd"] and not authority.receipts
    assert journal.operations()["create_vhd"]["result_json"] is not None


def test_receipt_replay_never_authorizes_command(fixture):
    runner, authority, backend, journal = fixture
    authority.cached = True
    with pytest.raises(ProvisionError, match="provision_operation_unknown"):
        runner.execute_next()
    assert not backend.effects
    assert journal.operations()["create_vhd"]["status"] == "unknown"


def test_unknown_new_vm_survives_restart_and_reconcile_does_not_retry(fixture):
    runner, authority, backend, journal = fixture
    runner.execute_next()
    backend.fail_after_go = True
    with pytest.raises(ProvisionError, match="provision_operation_unknown"):
        runner.execute_next()
    assert backend.effects == ["create_vhd", "create_vm"]
    replacement = Journal(journal.directory)
    try:
        restarted = Runner(replacement, authority, backend, runner.template)
        assert restarted.reconcile() == "matched"
        with pytest.raises(ProvisionError, match="provision_reconciliation_required"):
            restarted.execute_next()
        assert replacement.operations()["create_vm"]["status"] == "unknown"
        assert backend.effects == ["create_vhd", "create_vm"]
    finally:
        replacement.close()


def test_old_process_still_alive_blocks_reconcile_and_next_effect(fixture):
    runner, authority, backend, journal = fixture
    backend.fail_after_go = True
    with pytest.raises(ProvisionError):
        runner.execute_next()
    backend.alive = True
    with pytest.raises(ProvisionError, match="provision_owner_running"):
        runner.reconcile()
    with pytest.raises(ProvisionError, match="provision_owner_running"):
        runner.execute_next()
    assert backend.effects == ["create_vhd"]


def test_lost_receipt_response_keeps_observed_result_but_blocks_chain(fixture):
    runner, authority, backend, journal = fixture
    authority.lose_receipt = True
    with pytest.raises(ProvisionError, match="provision_operation_unknown") as caught:
        runner.execute_next()
    assert "private" not in str(caught.value)
    assert journal.operations()["create_vhd"]["result_json"] is not None
    with pytest.raises(ProvisionError, match="provision_reconciliation_required"):
        runner.execute_next()
    assert backend.effects == ["create_vhd"]


@pytest.mark.parametrize(
    "updates",
    [
        {"owner_id": uuid4()},
        {"generation": 2},
        {"provisioner_id": uuid4()},
        {"lease_expires_at": datetime.now(UTC) - timedelta(seconds=1)},
        {"lease_expires_at": datetime.now(UTC) + timedelta(seconds=10)},
        {"status": "outcome_unknown"},
    ],
)
def test_changed_owner_fence_or_expired_lease_never_creates_intent(fixture, updates):
    runner, authority, backend, journal = fixture
    authority.claim = authority.claim.model_copy(update=updates)
    with pytest.raises(ProvisionError, match="provision_claim_stale"):
        runner.execute_next()
    assert not backend.effects and not journal.operations()


def test_existing_foreign_vm_is_not_adopted_or_changed(fixture):
    runner, _, backend, journal = fixture
    backend.inventory = backend.inventory.model_copy(update={"found": True})
    with pytest.raises(ProvisionError, match="provision_inventory_conflict"):
        runner.execute_next()
    assert not backend.effects and not journal.operations()
