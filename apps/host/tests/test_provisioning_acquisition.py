"""Coordenador/journals/cifra reais; transporte e hardware falsos explícitos."""

import os
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from bees_host.provisioning import acquisition as module
from bees_host.provisioning.acquisition import Acquisition
from bees_host.provisioning.contracts import OPERATIONS, ProvisionError, canonical
from bees_host.provisioning.enrollment import EnrollmentBinding, EnrollmentStore
from bees_host.provisioning.journal import create
from bees_host.provisioning.supervisor_store import SupervisorStore
from bees_host.security import DPAPICipher, FernetCipher, check_private
from cryptography.fernet import Fernet
from test_guest_bridge_tls import private_bridge_directory
from test_provisioning_runner import claim_fixture
from test_provisioning_supervisor import Authority, Backend


class Transport:
    def __init__(self, claim, store):
        self.control = Authority(claim)
        self.store, self.calls = store, []
        self.lost = False
        self.before_return = None
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.closed = True

    def __getattr__(self, name):
        return getattr(self.control, name)

    def claim(self, plan_id, plan_hash, owner_id, request_id):
        self.store.assert_locked()
        row = self.store.acquisitions()[0]
        assert row["owner_id"] == str(owner_id) and row["request_id"] == str(request_id)
        assert row["status"] == "prepared"
        assert row["plan_id"] == str(plan_id) and row["plan_hash"] == plan_hash
        self.calls.append((plan_id, plan_hash, owner_id, request_id))
        self.control.claim = self.control.claim.model_copy(update={"owner_id": owner_id})
        if self.lost:
            raise OSError("private detail")
        if self.before_return:
            self.before_return()
        return self.control.claim


@pytest.fixture
def fixture(monkeypatch):
    with private_bridge_directory() as base:
        claim = claim_fixture().model_copy(
            update={"lease_expires_at": datetime.now(UTC) + timedelta(seconds=60)}
        )
        binding = EnrollmentBinding(
            origin="http://localhost:8080",
            installation_id=claim.installation_id,
            host_id=claim.host_id,
            provisioner_id=claim.provisioner_id,
            issue_request_id=uuid4(),
        )
        bootstrap = base / "bootstrap.json"
        create(
            bootstrap,
            canonical(
                dict(
                    format=1,
                    **binding.model_dump(mode="json"),
                    provisioner_credential="bp_" + "a" * 43,
                    expires_at=(datetime.now(UTC) + timedelta(minutes=10)).isoformat(),
                )
            ),
        )
        cipher = DPAPICipher() if os.name == "nt" else FernetCipher(Fernet.generate_key().decode())
        enrollment = EnrollmentStore.initialize(
            base / "enrollment", bootstrap, binding=binding, cipher=cipher
        )
        store = SupervisorStore.initialize_for_acquisition(
            base / "ledger", installation_id=claim.installation_id, host_id=claim.host_id
        )
        journals = base / "plans"
        journals.mkdir()
        check_private(journals, directory=True, protect=True)
        transport = Transport(claim, store)
        factories = []

        def factory(*args, **kwargs):
            assert store.acquisitions()[0]["status"] == "prepared"
            factories.append(True)
            return transport

        monkeypatch.setattr(module, "HTTPAuthority", factory)
        backend = Backend(claim)
        template = SimpleNamespace(verify=lambda plan: base / "fixture.iso")
        coordinator = Acquisition(store, enrollment, backend, template, journals)
        try:
            yield coordinator, transport, backend, store, claim.plan, factories
        finally:
            store.close()


def restarted(coordinator, *, store=None):
    return Acquisition(
        store or coordinator.store,
        coordinator.enrollment,
        coordinator.backend,
        coordinator.template,
        coordinator.journals_directory,
    )


def test_reservation_is_durable_before_factory_and_single_lock_covers_entire_handoff(fixture):
    coordinator, transport, backend, store, plan, factories = fixture
    epochs = []

    def held():
        store.assert_locked()
        epochs.append(store._lock_epoch)
        other = SupervisorStore.open(store.directory)
        try:
            with pytest.raises(ProvisionError, match="owner_running"):
                with other.lock():
                    pytest.fail("Segundo dono entrou")
        finally:
            other.close()

    transport.before_return = held
    backend.on_ready = held
    backend.before_finish = held
    assert coordinator.run(plan).verified
    assert factories == [True] and len(transport.calls) == 1 and transport.closed
    assert len(epochs) > 6 and all(epoch is epochs[0] for epoch in epochs)
    assert backend.effects == list(OPERATIONS)
    assert store.runs()[0]["status"] == store.acquisitions()[0]["status"] == "completed"
    with pytest.raises(ProvisionError, match="reconciliation_required"):
        coordinator.run(plan)
    with pytest.raises(ProvisionError):
        restarted(coordinator).run(plan)
    assert len(transport.calls) == 1 and factories == [True]


def test_response_loss_blocks_new_instance_and_reopen_without_another_http(fixture):
    coordinator, transport, backend, store, plan, factories = fixture
    transport.lost = True
    with pytest.raises(ProvisionError, match="acquisition_unknown"):
        coordinator.run(plan)
    row = store.acquisitions()[0]
    assert row["status"] == "unknown" and row["claim_id"] is None
    assert len(transport.calls) == 1 and not backend.effects and not transport.control.unknowns
    reopened = SupervisorStore.open(store.directory)
    try:
        for candidate in (restarted(coordinator), restarted(coordinator, store=reopened)):
            with pytest.raises(ProvisionError, match="reconciliation_required"):
                candidate.run(plan)
    finally:
        reopened.close()
    assert store.acquisitions() == [row] and factories == [True]


def test_constructor_failure_after_prepare_keeps_ids_and_never_retries(fixture, monkeypatch):
    coordinator, transport, backend, store, plan, _ = fixture

    def fail(*args, **kwargs):
        raise OSError("private credential/path")

    monkeypatch.setattr(module, "HTTPAuthority", fail)
    with pytest.raises(ProvisionError, match="acquisition_unknown") as error:
        coordinator.run(plan)
    assert "private" not in str(error.value)
    assert store.acquisitions()[0]["status"] == "unknown"
    assert not transport.calls and not backend.effects
    with pytest.raises(ProvisionError, match="reconciliation_required"):
        restarted(coordinator).run(plan)


@pytest.mark.parametrize("moment", ["before", "after_claim", "after_journal"])
def test_cancellation_never_dispatches_hardware(fixture, monkeypatch, moment):
    coordinator, transport, backend, store, plan, factories = fixture
    if moment == "before":
        coordinator.cancelled.set()
    elif moment == "after_claim":
        transport.before_return = coordinator.cancelled.set
    else:
        initialize = module.Journal.initialize

        def cancel(*args, **kwargs):
            result = initialize(*args, **kwargs)
            coordinator.cancelled.set()
            return result

        monkeypatch.setattr(module.Journal, "initialize", cancel)
    with pytest.raises(ProvisionError):
        coordinator.run(plan)
    assert not backend.effects and not transport.control.unknowns
    if moment == "before":
        assert not store.acquisitions() and not factories
    else:
        assert store.acquisitions()[0]["status"] == "unknown"
        assert len(transport.calls) == 1


def test_partial_journal_after_accepted_response_never_resumes_or_reclaims(fixture, monkeypatch):
    coordinator, transport, backend, store, plan, factories = fixture

    def partial(directory, claim):
        directory.mkdir()
        check_private(directory, directory=True, protect=True)
        create(directory / "identity.json", b"partial")
        raise OSError("fixture")

    monkeypatch.setattr(module.Journal, "initialize", partial)
    with pytest.raises(ProvisionError, match="acquisition_unknown"):
        coordinator.run(plan)
    row = store.acquisitions()[0]
    assert row["status"] == "unknown" and row["claim_id"] is not None
    assert not backend.effects and not store.runs()
    with pytest.raises(ProvisionError, match="reconciliation_required"):
        restarted(coordinator).run(plan)
    assert len(transport.calls) == 1 and factories == [True]


@pytest.mark.parametrize("failure", ["preflight", "kit", "existing_journal", "binding", "v1"])
def test_local_prerequisites_fail_before_reserving_or_contacting_service(fixture, failure):
    coordinator, transport, backend, store, plan, factories = fixture
    legacy = None

    def fail(*args):
        raise ProvisionError("provision_fixture_unavailable")

    if failure == "preflight":
        backend.preflight = fail
    elif failure == "kit":
        coordinator.template.verify = fail
    elif failure == "existing_journal":
        (coordinator.journals_directory / str(plan.plan_id)).mkdir()
    elif failure == "binding":
        plan = plan.model_copy(update={"host_id": uuid4()})
    else:
        legacy = SupervisorStore.initialize(
            store.directory.parent / "legacy",
            installation_id=plan.installation_id,
            host_id=plan.host_id,
        )
        coordinator.store = legacy
    try:
        with pytest.raises(ProvisionError):
            coordinator.run(plan)
        assert not factories and not transport.calls and not backend.effects
        assert not store.acquisitions()
    finally:
        if legacy is not None:
            legacy.close()


def test_supervisor_failure_keeps_acquisition_and_run_unknown_together(fixture):
    coordinator, transport, backend, store, plan, _ = fixture
    backend.fail_after_go = True
    with pytest.raises(ProvisionError, match="acquisition_unknown"):
        coordinator.run(plan)
    assert backend.effects == ["create_vhd"]
    assert store.acquisitions()[0]["status"] == store.runs()[0]["status"] == "unknown"
    with pytest.raises(ProvisionError, match="reconciliation_required"):
        restarted(coordinator).run(plan)
    assert len(transport.calls) == 1 and backend.effects == ["create_vhd"]
