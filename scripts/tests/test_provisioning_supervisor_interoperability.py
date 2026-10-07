"""Supervisor/HTTP/core/journals reais; nenhum teste cria VM ou usa modelo pago."""

from uuid import UUID

import pytest
from bees_host.provisioning.contracts import OPERATIONS, ProvisionError
from bees_host.provisioning.runner import Runner
from bees_host.provisioning.supervisor import Supervisor
from bees_host.provisioning.supervisor_store import SupervisorStore
from test_provisioning_http_interoperability import remote
from test_provisioning_interoperability import Template, revoke
from test_provisioning_interoperability import integration as integration


class SupervisedHardware:
    def __init__(self, hardware):
        self.hardware = hardware
        self.during_wait = None

    def __getattr__(self, name):
        return getattr(self.hardware, name)

    def start(self, *args):
        command = self.hardware.start(*args)
        finish = command.finish

        def controlled_finish(*, guard=None):
            if self.during_wait:
                self.during_wait()
            if guard:
                guard()
            return finish()

        command.finish = controlled_finish
        return command


@pytest.fixture
def store(integration):
    claim, journal = integration[3:5]
    ledger = SupervisorStore.initialize(
        journal.directory.parent / "supervisor",
        installation_id=claim.installation_id,
        host_id=claim.host_id,
    )
    try:
        yield ledger
    finally:
        ledger.close()


def test_real_http_supervisor_completes_without_vm_ready(integration, store):
    core, _, plan, _, journal, _, hardware = integration
    with remote(integration) as (authority, _):
        supervisor = Supervisor(
            Runner(journal, authority, SupervisedHardware(hardware), Template()), store
        )
        assert supervisor.run().verified
    assert hardware.effects == list(OPERATIONS)
    assert store.runs()[0]["status"] == "completed"
    assert core.get(UUID(plan["plan_id"]))["status"] == "hardware_verified"
    with core.database.transaction(write=False) as connection:
        assert connection.execute("SELECT status FROM environments").get == "provisioning"


def test_real_http_guard_renews_while_waiting(integration, store, monkeypatch):
    _, _, _, _, journal, _, hardware = integration
    monkeypatch.setattr("bees_host.provisioning.supervisor.RENEW_MARGIN", 65)
    with remote(integration) as (authority, _):
        backend = SupervisedHardware(hardware)
        supervisor = Supervisor(Runner(journal, authority, backend, Template()), store)
        backend.during_wait = lambda: setattr(supervisor, "_next_check", 0)
        assert supervisor.run().verified
    requests = store.requests()
    assert len(requests) >= 6
    assert all(row["kind"] == "renew" and row["status"] == "confirmed" for row in requests)
    assert store.runs()[0]["status"] == "completed"


def test_real_http_cancel_reports_unknown_without_retry(integration, store):
    core, _, _, _, journal, _, hardware = integration
    with remote(integration) as (authority, _):
        backend = SupervisedHardware(hardware)
        supervisor = Supervisor(Runner(journal, authority, backend, Template()), store)
        backend.during_wait = supervisor.cancelled.set
        with pytest.raises(ProvisionError, match="operation_unknown"):
            supervisor.run()
        assert supervisor.reconcile() == "matched"
    assert hardware.effects == ["create_vhd"]
    assert store.runs()[0]["status"] == "unknown"
    assert store.requests()[0]["status"] == "confirmed"
    with core.database.transaction(write=False) as connection:
        assert connection.execute("SELECT status FROM provisioning_claims").get == "outcome_unknown"
        assert (
            connection.execute("SELECT status FROM provisioning_effects").get == "outcome_unknown"
        )


def test_real_http_revocation_during_wait_preserves_quarantine(integration, store):
    core, _, plan, _, journal, _, hardware = integration
    with remote(integration) as (authority, _):
        backend = SupervisedHardware(hardware)
        supervisor = Supervisor(Runner(journal, authority, backend, Template()), store)

        def revoked():
            revoke(core, plan)
            supervisor._next_check = 0

        backend.during_wait = revoked
        with pytest.raises(ProvisionError):
            supervisor.run()
        with pytest.raises(ProvisionError):
            Supervisor(supervisor.runner, store).run()
    assert hardware.effects == ["create_vhd"] and store.runs()[0]["status"] == "unknown"
    assert journal.operations()["create_vhd"]["status"] == "unknown"


def test_real_http_receipt_committed_but_response_lost_never_repeats(integration, store):
    core, _, _, _, journal, _, hardware = integration
    with remote(integration, drop_receipt=True) as (authority, _):
        supervisor = Supervisor(
            Runner(journal, authority, SupervisedHardware(hardware), Template()), store
        )
        with pytest.raises(ProvisionError):
            supervisor.run()
        with pytest.raises(ProvisionError):
            Supervisor(supervisor.runner, store).run()
        assert supervisor.reconcile() == "matched"
    assert hardware.effects == ["create_vhd"] and store.runs()[0]["status"] == "unknown"
    assert journal.operations()["create_vhd"]["status"] == "unknown"
    with core.database.transaction(write=False) as connection:
        assert connection.execute("SELECT status FROM provisioning_effects").get == "confirmed"


def test_real_http_service_loss_stops_without_any_intent(integration, store):
    _, _, _, _, journal, _, hardware = integration
    with remote(integration) as (authority, child):
        child.terminate()
        child.wait(timeout=10)
        supervisor = Supervisor(
            Runner(journal, authority, SupervisedHardware(hardware), Template()), store
        )
        with pytest.raises(ProvisionError, match="supervisor_stopped"):
            supervisor.run()
    assert not hardware.effects and not journal.operations()
    assert store.runs()[0]["status"] == "stopped"


def test_real_http_renew_committed_response_lost_preserves_both_requests(
    integration, store, monkeypatch
):
    core, _, _, _, journal, _, hardware = integration
    monkeypatch.setattr("bees_host.provisioning.supervisor.RENEW_MARGIN", 0)
    with remote(integration, drop_renew=True) as (authority, _):
        backend = SupervisedHardware(hardware)
        supervisor = Supervisor(Runner(journal, authority, backend, Template()), store)

        def renew_during_wait():
            monkeypatch.setattr("bees_host.provisioning.supervisor.RENEW_MARGIN", 65)
            supervisor._next_check = 0

        backend.during_wait = renew_during_wait
        with pytest.raises(ProvisionError):
            supervisor.run()
        with pytest.raises(ProvisionError):
            Supervisor(supervisor.runner, store).run()
    assert hardware.effects == ["create_vhd"]
    requests = {row["kind"]: row for row in store.requests()}
    assert requests["renew"]["status"] == "pending"
    assert requests["unknown"]["status"] == "confirmed"
    assert journal.claim.status == "outcome_unknown"
    assert store.runs()[0]["status"] == "unknown"
    with core.database.transaction(write=False) as connection:
        assert connection.execute("SELECT status FROM provisioning_claims").get == "outcome_unknown"
