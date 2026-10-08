"""Inventário completo interno; nenhuma associação fabricada ou liberação local."""

import sqlite3
from contextlib import closing
from uuid import uuid4

import pytest
from bees_host.provisioning.contracts import ProvisionError
from bees_host.provisioning.supervisor_store import SupervisorStore
from test_guest_bridge_tls import private_bridge_directory
from test_provisioning_acquisition_store import acquire, prepare
from test_provisioning_runner import claim_fixture
from test_provisioning_supervisor_read_only import fingerprint


@pytest.fixture
def state():
    with private_bridge_directory() as base:
        claim = claim_fixture()
        store = SupervisorStore.initialize_for_acquisition(
            base / "ledger", installation_id=claim.installation_id, host_id=claim.host_id
        )
        try:
            yield store, claim
        finally:
            store.close()


def test_inventory_requires_owner_and_does_not_generate_identity(state):
    store, claim = state
    with pytest.raises(ProvisionError, match="provision_lock_required"):
        store.plan_inventory()
    with store.lock():
        assert store.plan_inventory() == {}
        ticket = prepare(store, claim)
        row = store.plan_inventory()[str(claim.plan_id)]
        assert row["run"] is None
        assert row["acquisition"]["request_id"] == str(ticket.request_id)
        assert row["acquisition"]["status"] == "prepared"


@pytest.mark.parametrize("phase", ["accepted", "running", "completed", "unknown"])
def test_read_only_association_preserves_rows_and_files(state, phase):
    store, claim = state
    with store.lock():
        ticket, claim = acquire(store, claim)
        if phase != "accepted":
            store.begin(claim, acquisition=ticket)
        if phase in {"completed", "unknown"}:
            store.finish(claim.claim_id, phase)
    before = fingerprint(store.directory)
    with closing(SupervisorStore.open_read_only(store.directory)) as reader:
        with reader.lock():
            row = reader.plan_inventory()[str(claim.plan_id)]
            assert row["acquisition"] == reader.acquisitions()[0]
            assert row["acquisition"]["status"] == phase
            assert row["run"] == (None if phase == "accepted" else reader.runs()[0])
            row["acquisition"]["status"] = "tampered"
            assert reader.plan_inventory()[str(claim.plan_id)]["acquisition"]["status"] == phase
    assert fingerprint(store.directory) == before


def test_inventory_includes_old_records_beyond_snapshot_limit(state):
    store, claim = state
    with store.lock():
        ticket, claim = acquire(store, claim)
        store.begin(claim, acquisition=ticket)
        store.finish(claim.claim_id, "completed")
        initial = store.plan_inventory()[str(claim.plan_id)]
        # Histórico válido maior que o teto1000 de projeção; SQL próprio da fixture.
        with store._transaction():
            for _ in range(1001):
                claim_id, owner_id, plan_id = (str(uuid4()) for _ in range(3))
                acquisition = initial["acquisition"] | {
                    "request_id": str(uuid4()),
                    "claim_id": claim_id,
                    "owner_id": owner_id,
                    "plan_id": plan_id,
                }
                run = initial["run"] | {
                    "claim_id": claim_id,
                    "owner_id": owner_id,
                    "plan_id": plan_id,
                }
                for table, record in (("runs", run), ("acquisitions", acquisition)):
                    placeholders = ",".join("?" for _ in record)
                    store.connection.execute(
                        f"INSERT INTO {table} ({','.join(record)}) VALUES ({placeholders})",
                        list(record.values()),
                    )
        assert len(store.acquisitions(limit=1000)) == 1000
        assert len(store.plan_inventory()) == 1002
        assert str(claim.plan_id) in store.plan_inventory()
    before = fingerprint(store.directory)
    with closing(SupervisorStore.open_read_only(store.directory)) as reader:
        with reader.lock():
            assert len(reader.plan_inventory()) == 1002
            assert reader.execution_blocked_local() is False
    assert fingerprint(store.directory) == before


def test_legacy_history_cannot_fabricate_root_association():
    with private_bridge_directory() as base:
        claim = claim_fixture()
        with closing(
            SupervisorStore.initialize(
                base / "ledger", installation_id=claim.installation_id, host_id=claim.host_id
            )
        ) as store:
            with store.lock():
                with pytest.raises(ProvisionError, match="provision_acquisition_required"):
                    store.plan_inventory()
                store.begin(claim)
                store.finish(claim.claim_id, "completed")
                store.migrate_for_acquisition()
            before = fingerprint(store.directory)
            with closing(SupervisorStore.open_read_only(store.directory)) as reader:
                with reader.lock():
                    assert reader.acquisitions() == [] and len(reader.runs()) == 1
                    with pytest.raises(
                        ProvisionError, match="provision_supervisor_binding_invalid"
                    ):
                        reader.plan_inventory()
            assert fingerprint(store.directory) == before


def test_binding_tamper_is_not_projected_as_inventory(state):
    store, claim = state
    with store.lock():
        ticket, claim = acquire(store, claim)
        store.begin(claim, acquisition=ticket)
        with closing(sqlite3.connect(store.path)) as connection:
            connection.execute("UPDATE runs SET owner_id=?", (str(uuid4()),))
            connection.commit()
        before = (store.path.read_bytes(), store.path.stat().st_mtime_ns)
        with pytest.raises(ProvisionError, match="provision_state_invalid"):
            store.plan_inventory()
        assert (store.path.read_bytes(), store.path.stat().st_mtime_ns) == before
