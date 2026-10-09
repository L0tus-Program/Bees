"""Reserva/core/HTTP/inscrição/journals reais, backend falso e nenhuma VM."""

import hashlib
import os
import secrets
import socket
import subprocess
import tempfile
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from bees_host.provisioning import acquisition as module
from bees_host.provisioning import assets as assets_module
from bees_host.provisioning import root_store as root_module
from bees_host.provisioning import runtime as runtime_module
from bees_host.provisioning.acquisition import Acquisition
from bees_host.provisioning.assets import KIT_FILES, AssetsBinding, AssetsStore
from bees_host.provisioning.contracts import Plan, ProvisionError, canonical
from bees_host.provisioning.enrollment import EnrollmentBinding, EnrollmentStore
from bees_host.provisioning.journal import Journal, create
from bees_host.provisioning.root_store import RootStore
from bees_host.provisioning.runtime import ProvisionerRuntime
from bees_host.provisioning.supervisor_store import SupervisorStore
from bees_host.security import DPAPICipher, FernetCipher, check_private
from cryptography.fernet import Fernet
from test_provisioning_http_interoperability import api_process
from test_provisioning_interoperability import Hardware
from test_provisioning_supervisor_interoperability import SupervisedHardware

from bees_core.environments import EnvironmentService
from bees_core.models import Agent
from bees_core.provisioning import ProvisioningService
from bees_core.security.hosts import HostService
from bees_core.security.identity import IdentityService
from bees_core.storage.database import Database
from bees_core.storage.store import StateStore


@pytest.fixture
def prepared(tmp_path):
    database = Database(tmp_path / "bees.sqlite3")
    database.initialize()
    identity = IdentityService(database)
    identity.setup(identity.issue_bootstrap(), "Reserva de teste", "senha própria teste 123456")
    hosts = HostService(database)
    invite = hosts.issue_invite(uuid4())
    diagnostic = "bh_" + secrets.token_urlsafe(32)
    host = hosts.pair(
        dict(
            installation_id=invite.installation_id,
            invite_token=invite.invite_token,
            host_id=uuid4(),
            host_credential=diagnostic,
            client_request_id=uuid4(),
        )
    )
    host = hosts.confirm(
        UUID(host["host_id"]),
        dict(
            expected_revision=host["revision"],
            fingerprint=host["fingerprint"],
            client_request_id=uuid4(),
        ),
    )
    hosts.report(
        diagnostic,
        dict(
            host_id=host["host_id"],
            sequence=1,
            expected_revision=0,
            client_request_id=uuid4(),
            driver="hyperv",
            probe=dict(platform=True, module=True, service=True),
        ),
    )
    with StateStore(database).transaction() as unit:
        agent = unit.agents.create(Agent(name="Reserva descartável"))
    environment = EnvironmentService(database).request(
        agent.id,
        dict(
            name="Computador descartável",
            cpu_count=3,
            memory_mib=6144,
            disk_gib=42,
            client_request_id=uuid4(),
        ),
    )
    core = ProvisioningService(database)
    snapshot = core.prepare_plan(
        agent.id,
        environment.id,
        dict(
            host_id=host["host_id"],
            expected_host_revision=host["revision"],
            expected_environment_revision=environment.revision,
            client_request_id=uuid4(),
        ),
    )
    snapshot = core.authorize_once(
        UUID(snapshot["plan_id"]),
        dict(
            expected_revision=snapshot["revision"],
            plan_hash=snapshot["plan_hash"],
            client_request_id=uuid4(),
        ),
    )
    issue_id = uuid4()
    issued = core.issue_provisioner(
        UUID(host["host_id"]),
        dict(expected_host_revision=host["revision"], client_request_id=issue_id),
    )
    plan = Plan.model_validate_json(canonical(snapshot["plan"]))
    with tempfile.TemporaryDirectory(prefix="bees-claim-interop-", dir=Path.home()) as name:
        root = Path(name)
        check_private(root, directory=True, protect=True)
        yield core, plan, issued, issue_id, root


def bootstrap_configuration(prepared, origin):
    _, _, issued, issue_id, root = prepared
    binding = EnrollmentBinding(
        origin=origin,
        installation_id=issued.installation_id,
        host_id=issued.host_id,
        provisioner_id=issued.provisioner_id,
        issue_request_id=issue_id,
    )
    bootstrap = root / "bootstrap.json"
    create(
        bootstrap,
        canonical(
            dict(
                format=1,
                **binding.model_dump(mode="json"),
                provisioner_credential=issued.credential.get_secret_value(),
                expires_at=(datetime.now(UTC) + timedelta(minutes=10)).isoformat(),
            )
        ),
    )
    cipher = DPAPICipher() if os.name == "nt" else FernetCipher(Fernet.generate_key().decode())
    return binding, bootstrap, cipher


def composition(prepared, origin):
    _, plan, _, _, root = prepared
    binding, bootstrap, cipher = bootstrap_configuration(prepared, origin)
    enrollment = EnrollmentStore.initialize(
        root / "enrollment", bootstrap, binding=binding, cipher=cipher
    )
    ledger = SupervisorStore.initialize_for_acquisition(
        root / "ledger", installation_id=plan.installation_id, host_id=plan.host_id
    )
    journals = root / "plans"
    journals.mkdir()
    check_private(journals, directory=True, protect=True)
    hardware = SupervisedHardware(Hardware(SimpleNamespace(plan=plan)))
    template = SimpleNamespace(verify=lambda plan: root / "fixture.iso")
    return Acquisition(ledger, enrollment, hardware, template, journals), ledger, hardware


def root_composition(prepared, origin, monkeypatch):
    _, plan, _, _, private_base = prepared
    binding, bootstrap, cipher = bootstrap_configuration(prepared, origin)
    directory = private_base / "bees-provisioner"
    monkeypatch.setattr(root_module, "_native_root", lambda: directory)
    manager = RootStore.initialize(binding=binding, bootstrap=bootstrap, cipher=cipher)
    enrollment = EnrollmentStore.open(
        directory / "state/enrollment", binding=binding, cipher=cipher
    )
    ledger = SupervisorStore.open(directory / "state/ledger")
    hardware = SupervisedHardware(Hardware(SimpleNamespace(plan=plan)))
    template = SimpleNamespace(verify=lambda plan: private_base / "fixture.iso")
    coordinator = Acquisition(ledger, enrollment, hardware, template, directory / "state/plans")
    return coordinator, ledger, hardware, manager, directory, binding, cipher


def root_fingerprint(directory):
    return {
        str(path.relative_to(directory)): (
            hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None,
            path.stat().st_mtime_ns,
        )
        for path in (directory, *directory.rglob("*"))
    }


def operational_composition(prepared, origin, monkeypatch):
    """Root/assets/cifra/ledger/HTTP reais; kit/hardware/ACL VMMS falsos explícitos."""
    _, plan, _, _, private_base = prepared
    binding, bootstrap, cipher = bootstrap_configuration(prepared, origin)
    directory = private_base / "bees-provisioner"
    monkeypatch.setattr(root_module, "_native_root", lambda: directory)
    manager = RootStore.initialize(binding=binding, bootstrap=bootstrap, cipher=cipher)
    resources = ProvisionerRuntime.initialize_assets(binding=binding, cipher=cipher)
    checked = manager.check_only()
    resource_binding = AssetsBinding.from_root_check(checked)
    assert resources.binding == resource_binding
    for name in KIT_FILES:
        create(resources.kit_directory / name, b"fixture kit, not an installable image")
    hardware_root = private_base / "bees-provisioner-hardware"
    hardware_root.mkdir(mode=0o700)
    check_private(hardware_root, directory=True, protect=True)
    create(
        hardware_root / "identity.json",
        canonical(assets_module._identity(resource_binding).model_dump(mode="json")),
    )
    create(hardware_root / "owner.lock", b"0")
    # ACL CurrentUser/SYSTEM/Admin possui prova nativa na suíte host. Aqui a
    # fronteira hardware inteira é double e não concede permissões de VMMS.
    monkeypatch.setattr(
        assets_module, "validate_hardware_root", lambda path: check_private(path, directory=True)
    )
    monkeypatch.setattr(assets_module, "validate_hardware_file", check_private)
    hardware = SupervisedHardware(Hardware(SimpleNamespace(plan=plan)))
    constructed = []

    def backend(path):
        assert path == hardware_root
        constructed.append(True)
        return hardware

    def template(path):
        assert path == resources.kit_directory
        return SimpleNamespace(verify=lambda plan: private_base / "fixture.iso")

    monkeypatch.setattr(runtime_module, "HyperVBackend", backend)
    monkeypatch.setattr(runtime_module, "LocalTemplate", template)
    runtime = ProvisionerRuntime.open(binding=binding, cipher=cipher)
    assert constructed == []  # open não instancia nem sonda backend.
    return runtime, hardware, manager, directory, binding, cipher, constructed


@pytest.mark.parametrize("failure", [None, "claim_response", "receipt_response", "cancel"])
def test_operational_runtime_real_http_preserves_completed_and_unknown_without_retry(
    prepared, monkeypatch, failure
):
    core, plan, issued, _, private_base = prepared
    with api_process(
        core.database.path.parent,
        drop_claim=failure == "claim_response",
        drop_receipt=failure == "receipt_response",
    ) as (origin, _):
        runtime, hardware, manager, directory, binding, cipher, constructed = (
            operational_composition(prepared, origin, monkeypatch)
        )
        cancelled = threading.Event()
        if failure == "cancel":
            hardware.during_wait = cancelled.set
        if failure is None:
            assert runtime.run(plan, cancelled=cancelled).verified
        else:
            with pytest.raises(ProvisionError, match="provision_acquisition_unknown"):
                runtime.run(plan, cancelled=cancelled)
        assert constructed == [True]
        ledger = SupervisorStore.open_read_only(directory / "state/ledger")
        try:
            with ledger.lock():
                row = ledger.acquisitions()[0]
                assert row["status"] == ("completed" if failure is None else "unknown")
                canonical_claim(core, row)
        finally:
            ledger.close()
        count = {None: 6, "claim_response": 0, "receipt_response": 1, "cancel": 1}[failure]
        assert len(hardware.effects) == count
        with core.database.transaction(write=False) as connection:
            assert connection.execute("SELECT count(*) FROM provisioning_effects").get == count
            if failure is None:
                assert core.get(plan.plan_id)["status"] == "hardware_verified"
                assert connection.execute("SELECT status FROM environments").get == "provisioning"
        with pytest.raises(ProvisionError):
            runtime.run(plan)
        assert len(hardware.effects) == count
        before = root_fingerprint(private_base)

    def forbidden(*args, **kwargs):
        pytest.fail("Abertura local tentou rede/processo/backend ou mutação")

    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(runtime_module, "HyperVBackend", forbidden)
    monkeypatch.setattr(runtime_module, "LocalTemplate", forbidden)
    monkeypatch.setattr(Journal, "initialize", forbidden)
    monkeypatch.setattr(AssetsStore, "initialize", forbidden)
    reopened = ProvisionerRuntime.open(binding=binding, cipher=cipher)
    check = manager.check_only()
    assert check.execution_blocked_local == (failure is not None)
    with pytest.raises(ProvisionError):
        reopened.run(plan)
    assert len(hardware.effects) == count
    assert root_fingerprint(private_base) == before
    assert issued.credential.get_secret_value() not in check.model_dump_json()


def test_operational_runtime_refuses_corrupt_kit_before_http_claim(prepared, monkeypatch):
    core, plan, _, _, _ = prepared
    with api_process(core.database.path.parent) as (origin, _):
        runtime, hardware, _, directory, _, _, constructed = operational_composition(
            prepared, origin, monkeypatch
        )
        # Recoloca verificador de imagem real: o kit falso não pode obter claim.
        from bees_host.provisioning.image import LocalTemplate

        monkeypatch.setattr(runtime_module, "LocalTemplate", LocalTemplate)
        before = root_fingerprint(directory)
        with pytest.raises(ProvisionError):
            runtime.run(plan)
        assert not hardware.effects and constructed == [True]
        assert root_fingerprint(directory) == before
        with core.database.transaction(write=False) as connection:
            assert connection.execute("SELECT count(*) FROM provisioning_claims").get == 0
            assert connection.execute("SELECT count(*) FROM provisioning_effects").get == 0


@pytest.mark.parametrize("failure", [None, "claim_response", "receipt_response"])
def test_root_reader_preserves_real_http_completed_and_unknown_without_service(
    prepared, monkeypatch, failure
):
    core, plan, issued, _, _ = prepared
    with api_process(
        core.database.path.parent,
        drop_claim=failure == "claim_response",
        drop_receipt=failure == "receipt_response",
    ) as (origin, _):
        coordinator, ledger, hardware, manager, directory, binding, cipher = root_composition(
            prepared, origin, monkeypatch
        )
        try:
            if failure is None:
                assert coordinator.run(plan).verified
            else:
                with pytest.raises(ProvisionError, match="provision_acquisition_unknown"):
                    coordinator.run(plan)
            row = ledger.acquisitions()[0]
            assert row["status"] == ("completed" if failure is None else "unknown")
            canonical_claim(core, row)
            effect_count = {None: 6, "claim_response": 0, "receipt_response": 1}[failure]
            assert len(hardware.effects) == effect_count
            with core.database.transaction(write=False) as connection:
                assert connection.execute("SELECT count(*) FROM provisioning_effects").get == (
                    effect_count
                )
            before = root_fingerprint(directory)
        finally:
            ledger.close()

    # A API própria já encerrou: o leitor não precisa de sessão nem reconcilia a rede.
    def forbidden(*args, **kwargs):
        pytest.fail("Leitor local tentou rede, processo ou mutação")

    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(Journal, "initialize", forbidden)
    monkeypatch.setattr(SupervisorStore, "acquisitions", forbidden)
    result = manager.check_only()
    assert result.configured_local and result.execution_blocked_local == (failure is not None)
    assert RootStore.open(binding=binding, cipher=cipher).check_only() == result
    assert root_fingerprint(directory) == before
    assert issued.credential.get_secret_value() not in result.model_dump_json()


def retry(coordinator):
    return Acquisition(
        coordinator.store,
        coordinator.enrollment,
        coordinator.backend,
        coordinator.template,
        coordinator.journals_directory,
    )


def canonical_claim(core, row):
    with core.database.transaction(write=False) as connection:
        claims = list(
            connection.execute(
                "SELECT id,client_request_id,owner_id,generation FROM provisioning_claims"
            )
        )
    assert len(claims) == 1
    assert claims[0][1:3] == (row["request_id"], row["owner_id"])
    return claims[0]


def test_http_claim_owner_is_persisted_and_six_effects_finish_under_one_lock(prepared):
    core, plan, _, _, _ = prepared
    with api_process(core.database.path.parent) as (origin, _):
        coordinator, ledger, hardware = composition(prepared, origin)
        try:
            assert coordinator.run(plan).verified
            row = ledger.acquisitions()[0]
            assert row["status"] == ledger.runs()[0]["status"] == "completed"
            assert canonical_claim(core, row)[0] == row["claim_id"]
            assert len(hardware.effects) == 6
            assert core.get(plan.plan_id)["status"] == "hardware_verified"
            with core.database.transaction(write=False) as connection:
                assert connection.execute("SELECT count(*) FROM provisioning_effects").get == 6
                assert connection.execute("SELECT status FROM environments").get == "provisioning"
        finally:
            ledger.close()


def test_503_after_committed_claim_keeps_single_owner_and_never_repeats_http(prepared):
    core, plan, _, _, _ = prepared
    with api_process(core.database.path.parent, drop_claim=True) as (origin, _):
        coordinator, ledger, hardware = composition(prepared, origin)
        try:
            with pytest.raises(ProvisionError, match="acquisition_unknown"):
                coordinator.run(plan)
            row = ledger.acquisitions()[0]
            assert row["status"] == "unknown" and row["claim_id"] is None
            canonical_claim(core, row)
            assert not hardware.effects and not ledger.runs()
            with core.database.transaction(write=False) as connection:
                assert connection.execute("SELECT count(*) FROM provisioning_effects").get == 0
                assert (
                    connection.execute(
                        "SELECT consumed_claim_id FROM provisioning_authorizations"
                    ).get
                    is None
                )
            with pytest.raises(ProvisionError, match="reconciliation_required"):
                retry(coordinator).run(plan)
            reopened = SupervisorStore.open(ledger.directory)
            try:
                coordinator.store = reopened
                with pytest.raises(ProvisionError, match="reconciliation_required"):
                    retry(coordinator).run(plan)
                assert reopened.acquisitions() == [row]
            finally:
                reopened.close()
            canonical_claim(core, row)
            assert not hardware.effects
        finally:
            ledger.close()


def test_revoked_credential_before_claim_is_quarantined_without_any_canonical_effect(prepared):
    core, plan, issued, _, _ = prepared
    with api_process(core.database.path.parent) as (origin, _):
        coordinator, ledger, hardware = composition(prepared, origin)
        try:
            core.revoke_provisioner(
                issued.provisioner_id, dict(expected_revision=1, client_request_id=uuid4())
            )
            with pytest.raises(ProvisionError, match="acquisition_unknown"):
                coordinator.run(plan)
            assert ledger.acquisitions()[0]["status"] == "unknown"
            assert not hardware.effects
            with core.database.transaction(write=False) as connection:
                assert connection.execute("SELECT count(*) FROM provisioning_claims").get == 0
                assert connection.execute("SELECT count(*) FROM provisioning_effects").get == 0
            with pytest.raises(ProvisionError, match="reconciliation_required"):
                retry(coordinator).run(plan)
        finally:
            ledger.close()


def test_crash_equivalent_after_accepting_response_keeps_claim_without_hardware(
    prepared, monkeypatch
):
    core, plan, _, _, _ = prepared
    with api_process(core.database.path.parent) as (origin, _):
        coordinator, ledger, hardware = composition(prepared, origin)
        initialize = Journal.initialize

        def interrupted(directory, claim):
            initialize(directory, claim).close()
            raise KeyboardInterrupt

        monkeypatch.setattr(module.Journal, "initialize", interrupted)
        try:
            with pytest.raises(ProvisionError, match="acquisition_unknown"):
                coordinator.run(plan)
            row = ledger.acquisitions()[0]
            assert row["status"] == "unknown" and row["claim_id"] is not None
            assert canonical_claim(core, row)[0] == row["claim_id"]
            assert not hardware.effects
            with pytest.raises(ProvisionError, match="reconciliation_required"):
                retry(coordinator).run(plan)
        finally:
            ledger.close()
