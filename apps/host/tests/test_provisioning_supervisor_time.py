"""Recuo do relógio civil não corrompe auditoria nem concede lease/retomada."""

from contextlib import closing
from datetime import UTC, datetime, timedelta, timezone

import pytest
from bees_host.provisioning.contracts import ProvisionError
from bees_host.provisioning.supervisor_store import SupervisorStore
from test_guest_bridge_tls import private_bridge_directory
from test_provisioning_acquisition_store import prepare
from test_provisioning_runner import claim_fixture
from test_provisioning_supervisor import fixture as supervisor_fixture
from test_provisioning_supervisor_store import renewed, successor


@pytest.fixture
def clock(monkeypatch):
    class Clock(datetime):
        value = datetime.now(UTC)

        @classmethod
        def now(cls, tz=None):
            return cls.value.astimezone(tz)

    monkeypatch.setattr("bees_host.provisioning.supervisor_store.datetime", Clock)
    return Clock


@pytest.fixture
def live_supervisor():
    yield from supervisor_fixture.__wrapped__()


def test_completed_sequence_remains_valid_after_wall_clock_regresses(live_supervisor, clock):
    supervisor, _, backend, _, store = live_supervisor
    start = clock.value
    lease = supervisor.runner.journal.claim.lease_expires_at

    def regress():
        clock.value = start - timedelta(seconds=10)

    backend.on_ready = regress
    assert supervisor.run().verified
    run = store.runs()[0]
    assert run["status"] == "completed"
    assert datetime.fromisoformat(run["created_at"]) == start
    assert datetime.fromisoformat(run["updated_at"]) == start
    assert datetime.fromisoformat(run["lease_expires_at"]) == lease
    assert store.requests() == []
    with pytest.raises(ProvisionError, match="^provision_reconciliation_required$"):
        supervisor.run()
    assert len(backend.effects) == 6


@pytest.mark.parametrize("version", [1, 2])
def test_linked_audit_dates_survive_regression_and_follow_restored_clock(version, clock):
    base_time = clock.value
    claim = claim_fixture()
    with private_bridge_directory() as base:
        initialize = (
            SupervisorStore.initialize_for_acquisition
            if version == 2
            else SupervisorStore.initialize
        )
        with closing(
            initialize(base / "host", installation_id=claim.installation_id, host_id=claim.host_id)
        ) as store:
            with store.lock():
                ticket = None
                clock.value = base_time + timedelta(seconds=5)
                if version == 2:
                    ticket = prepare(store, claim)
                    claim = successor(claim, owner_id=str(ticket.owner_id))
                    # Offsets distintos devem ser comparados como instantes aware.
                    stored = clock.value.astimezone(timezone(timedelta(hours=14))).isoformat()
                    store.connection.execute(
                        "UPDATE acquisitions SET created_at=?,updated_at=?", (stored, stored)
                    )
                    clock.value = base_time + timedelta(seconds=4)
                    store.accept_acquisition(ticket, claim)
                    clock.value = base_time + timedelta(seconds=3)
                store.begin(claim, acquisition=ticket)
                initial = datetime.fromisoformat(store.runs()[0]["created_at"])
                assert initial == base_time + timedelta(seconds=5)
                clock.value = base_time - timedelta(seconds=10)
                first_id = store.request(claim.claim_id, "renew")
                first_claim = renewed(claim)
                clock.value = base_time - timedelta(seconds=20)
                store.confirm(first_id, first_claim)
                assert datetime.fromisoformat(store.requests()[0]["confirmed_at"]) == initial
                # O relógio restaurado volta a avançar os carimbos normalmente.
                restored = base_time + timedelta(seconds=20)
                clock.value = restored
                second_id = store.request(claim.claim_id, "renew")
                second_claim = renewed(first_claim)
                clock.value = base_time - timedelta(seconds=30)
                store.confirm(second_id, second_claim)
                clock.value = base_time - timedelta(seconds=40)
                store.finish(claim.claim_id, "completed")
                run = store.runs()[0]
                assert datetime.fromisoformat(run["updated_at"]) == restored
                assert (
                    datetime.fromisoformat(run["lease_expires_at"]) == second_claim.lease_expires_at
                )
                if version == 2:
                    assert datetime.fromisoformat(store.acquisitions()[0]["updated_at"]) == restored
            # A abertura real valida as relações persistidas, sem depender do mock.
            with closing(SupervisorStore.open_read_only(store.directory)) as reader:
                with reader.lock():
                    assert reader.runs()[0] == run
                    assert all(row["status"] == "confirmed" for row in reader.requests())


@pytest.mark.parametrize("phase", ["prepared", "accepted", "running"])
@pytest.mark.parametrize("status", ["unknown", "stopped"])
def test_regressed_clock_preserves_quarantine_and_cannot_recover_ticket(phase, status, clock):
    start = clock.value
    claim = claim_fixture()
    with private_bridge_directory() as base:
        with closing(
            SupervisorStore.initialize_for_acquisition(
                base / "host", installation_id=claim.installation_id, host_id=claim.host_id
            )
        ) as store:
            with store.lock():
                ticket = prepare(store, claim)
                claim = successor(claim, owner_id=str(ticket.owner_id))
                clock.value = start - timedelta(seconds=1)
                if phase != "prepared":
                    store.accept_acquisition(ticket, claim)
                clock.value = start - timedelta(seconds=2)
                if phase == "running":
                    store.begin(claim, acquisition=ticket)
                    clock.value = start - timedelta(seconds=3)
                    store.finish(claim.claim_id, status)
                else:
                    store.fail_acquisition(ticket, status)
                row = store.acquisitions()[0]
                assert datetime.fromisoformat(row["updated_at"]) == start
                assert row["status"] == status
            with store.lock():
                with pytest.raises(ProvisionError, match="^provision_reconciliation_required$"):
                    store.assert_acquisition_ready()
                with pytest.raises(ProvisionError, match="^provision_reconciliation_required$"):
                    store.begin(claim, acquisition=ticket)


@pytest.mark.parametrize("version", [1, 2])
def test_unknown_confirmation_after_clock_regression_keeps_quarantine(version, clock):
    start = clock.value
    claim = claim_fixture()
    with private_bridge_directory() as base:
        initialize = (
            SupervisorStore.initialize_for_acquisition
            if version == 2
            else SupervisorStore.initialize
        )
        with closing(
            initialize(base / "host", installation_id=claim.installation_id, host_id=claim.host_id)
        ) as store:
            with store.lock():
                ticket = None
                if version == 2:
                    ticket = prepare(store, claim)
                    claim = successor(claim, owner_id=str(ticket.owner_id))
                    store.accept_acquisition(ticket, claim)
                store.begin(claim, acquisition=ticket)
                clock.value = start + timedelta(seconds=10)
                store.finish(claim.claim_id, "unknown")
                clock.value = start - timedelta(seconds=10)
                request_id = store.request(claim.claim_id, "unknown")
                unknown = successor(claim, revision=claim.revision + 1, status="outcome_unknown")
                clock.value = start - timedelta(seconds=20)
                store.confirm(request_id, unknown)
                request = store.requests()[0]
                assert datetime.fromisoformat(request["created_at"]) == start + timedelta(
                    seconds=10
                )
                assert request["confirmed_at"] == request["created_at"]
                assert store.runs()[0]["status"] == "unknown"
                assert datetime.fromisoformat(store.runs()[0]["lease_expires_at"]) == (
                    claim.lease_expires_at
                )
            with store.lock():
                with pytest.raises(ProvisionError, match="^provision_reconciliation_required$"):
                    store.begin(claim, acquisition=ticket)


@pytest.mark.parametrize("phase", ["accept", "begin"])
def test_audit_floor_neither_expires_valid_claim_nor_extends_expired_lease(phase, clock):
    start = clock.value
    claim = successor(claim_fixture(), lease_expires_at=(start + timedelta(seconds=30)).isoformat())
    with private_bridge_directory() as base:
        with closing(
            SupervisorStore.initialize_for_acquisition(
                base / "host", installation_id=claim.installation_id, host_id=claim.host_id
            )
        ) as store:
            with store.lock():
                clock.value = start + timedelta(seconds=120)
                ticket = prepare(store, claim)
                claim = successor(claim, owner_id=str(ticket.owner_id))
                if phase == "begin":
                    clock.value = start
                    store.accept_acquisition(ticket, claim)
                    assert datetime.fromisoformat(store.acquisitions()[0]["updated_at"]) > (
                        claim.lease_expires_at
                    )
                clock.value = start + timedelta(seconds=31)
                with pytest.raises(ProvisionError, match="^provision_claim_stale$"):
                    if phase == "accept":
                        store.accept_acquisition(ticket, claim)
                    else:
                        store.begin(claim, acquisition=ticket)
                assert store.runs() == []
                if phase == "begin":
                    assert datetime.fromisoformat(store.acquisitions()[0]["lease_expires_at"]) == (
                        claim.lease_expires_at
                    )
                else:
                    assert store.acquisitions()[0]["claim_id"] is None
