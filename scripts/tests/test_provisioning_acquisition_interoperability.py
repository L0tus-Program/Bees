"""Reserva/core/HTTP/inscrição/journals reais, backend falso e nenhuma VM."""

import os
import secrets
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from bees_host.provisioning import acquisition as module
from bees_host.provisioning.acquisition import Acquisition
from bees_host.provisioning.contracts import Plan, ProvisionError, canonical
from bees_host.provisioning.enrollment import EnrollmentBinding, EnrollmentStore
from bees_host.provisioning.journal import Journal, create
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


def composition(prepared, origin):
    _, plan, issued, issue_id, root = prepared
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
