"""Core e runner reais com hardware falso; nenhuma alteração no Windows/hipervisor."""

import json
import secrets
import tempfile
import time
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from bees_host.provisioning.contracts import Claim, DispatchPermit, Inventory, ProvisionError
from bees_host.provisioning.journal import Journal
from bees_host.provisioning.runner import Runner
from bees_host.security import check_private

from bees_core.environments import EnvironmentService
from bees_core.models import Agent
from bees_core.provisioning import OPERATIONS, ProvisioningService
from bees_core.security.hosts import HostService
from bees_core.security.identity import IdentityService
from bees_core.storage.database import Database
from bees_core.storage.store import StateStore


def parse(model, value):
    return model.model_validate_json(json.dumps(value))


def binding(claim):
    return dict(claim_id=claim.claim_id, owner_id=claim.owner_id, generation=claim.generation)


class Authority:
    """Adaptador somente de teste; contratos reais serializados dos dois lados."""

    def __init__(self, core, token):
        self.core, self.token = core, token
        self.drop_receipt = False

    def assert_current(self, claim):
        return parse(Claim, self.core.assert_current(self.token, binding(claim)))

    def begin_dispatch(self, claim, operation, request_id):
        return parse(
            DispatchPermit,
            self.core.begin_dispatch(
                self.token,
                binding(claim) | dict(operation=operation, client_request_id=request_id),
            ),
        )

    def record_receipt(self, claim, effect_id, request_id, result):
        response = self.core.record_receipt(
            self.token,
            binding(claim)
            | dict(
                effect_request_id=effect_id,
                client_request_id=request_id,
                result=result.model_dump(mode="json"),
            ),
        )
        if self.drop_receipt:
            raise OSError("Resposta perdida após confirmação canônica.")
        return parse(DispatchPermit, response)


class Template:
    def verify(self, plan):
        return Path("fixture-readonly.iso")


class Hardware:
    """Não simula isolamento/Hyper-V; só observa quantidade e ordem dos efeitos."""

    def __init__(self, claim):
        self.claim, self.vm_id = claim, uuid4()
        self.effects = []
        self.before_go = None
        self.before_finish = None

    def preflight(self):
        pass

    def inspect(self, plan, iso, vm_id):
        return self.inventory(self.effects[-1] if self.effects else None)

    def inventory(self, operation):
        plan = self.claim.plan
        has_vm = operation is not None and operation != "create_vhd"
        return Inventory(
            found=operation is not None,
            vm_id=self.vm_id if has_vm else None,
            owned=operation is not None,
            powered_off=has_vm,
            generation2=has_vm,
            cpu_count=plan.cpu_count if has_vm else None,
            memory_bytes=plan.memory_bytes if has_vm else None,
            disk_bytes=plan.disk_bytes if operation else None,
            fixed_vhdx=operation is not None,
            network_adapter_count=0 if operation in {"remove_nic", "attach_iso", "verify"} else 1,
            iso_attached=operation in {"attach_iso", "verify"},
        )

    def start(self, plan, operation, effect_id, iso, vm_id):
        hardware = self

        class Command:
            def ready(self):
                if hardware.before_go:
                    hardware.before_go()
                return 12345, 123456789

            def go(self):
                hardware.effects.append(operation)

            def finish(self):
                if hardware.before_finish:
                    hardware.before_finish()
                return hardware.inventory(operation)

            def close(self):
                pass

        return Command()

    def running(self, pid, start_ticks):
        return False


@pytest.fixture
def integration(tmp_path):
    clock = [time.time()]
    database = Database(tmp_path / "canonical.sqlite3")
    database.initialize()
    identity = IdentityService(database, clock=lambda: clock[0])
    identity.setup(identity.issue_bootstrap(), "Teste", "senha própria de teste 123456")
    hosts = HostService(database, clock=lambda: clock[0])
    invite = hosts.issue_invite(uuid4())
    host_token = "bh_" + secrets.token_urlsafe(32)
    host = hosts.pair(
        dict(
            installation_id=invite.installation_id,
            invite_token=invite.invite_token,
            host_id=uuid4(),
            host_credential=host_token,
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
        host_token,
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
        agent = unit.agents.create(Agent(name="Integração descartável"))
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
    core = ProvisioningService(database, clock=lambda: clock[0])
    plan = core.prepare_plan(
        agent.id,
        environment.id,
        dict(
            host_id=host["host_id"],
            expected_host_revision=host["revision"],
            expected_environment_revision=environment.revision,
            client_request_id=uuid4(),
        ),
    )
    plan = core.authorize_once(
        UUID(plan["plan_id"]),
        dict(
            expected_revision=plan["revision"],
            plan_hash=plan["plan_hash"],
            client_request_id=uuid4(),
        ),
    )
    issued = core.issue_provisioner(
        UUID(host["host_id"]),
        dict(expected_host_revision=host["revision"], client_request_id=uuid4()),
    )
    token = issued.credential.get_secret_value()
    claim = parse(
        Claim,
        core.claim(
            token,
            dict(
                plan_id=plan["plan_id"],
                plan_hash=plan["plan_hash"],
                owner_id=uuid4(),
                client_request_id=uuid4(),
            ),
        ),
    )
    with tempfile.TemporaryDirectory(
        prefix="bees-provision-interop-", dir=Path.home()
    ) as temporary:
        directory = Path(temporary)
        check_private(directory, directory=True, protect=True)
        journal = Journal.initialize(directory / "journal", claim)
        authority = Authority(core, token)
        hardware = Hardware(claim)
        try:
            yield core, clock, plan, claim, journal, authority, hardware
        finally:
            journal.close()


def revoke(core, plan):
    current = core.get(UUID(plan["plan_id"]))
    core.revoke_authorization(
        UUID(plan["plan_id"]),
        dict(
            expected_revision=current["revision"],
            plan_hash=current["plan_hash"],
            client_request_id=uuid4(),
        ),
    )


def test_actual_contracts_complete_six_effects_with_real_vm_id_receipt(integration):
    core, _, plan, claim, journal, authority, hardware = integration
    runner = Runner(journal, authority, hardware, Template())
    for operation in OPERATIONS:
        result = runner.execute_next()
        assert result.verified is True
        assert hardware.effects[-1] == operation
    assert hardware.effects == list(OPERATIONS)
    assert journal.claim.status == "confirmed"
    assert core.get(UUID(plan["plan_id"]))["status"] == "hardware_verified"
    with core.database.transaction(write=False) as connection:
        assert (
            connection.execute(
                "SELECT count(*) FROM provisioning_effects WHERE status='confirmed'"
            ).get
            == 6
        )
        assert (
            connection.execute(
                "SELECT status FROM environments WHERE id=?", (str(claim.plan.environment_id),)
            ).get
            == "provisioning"
        )
    with pytest.raises(ProvisionError, match="already_completed"):
        runner.execute_next()
    assert len(hardware.effects) == 6


def test_revoke_while_child_waits_prevents_go_and_unknown_never_retries(integration):
    core, _, plan, _, journal, authority, hardware = integration
    hardware.before_go = lambda: revoke(core, plan)
    runner = Runner(journal, authority, hardware, Template())
    with pytest.raises(ProvisionError):
        runner.execute_next()
    assert hardware.effects == []
    assert journal.operations()["create_vhd"]["status"] == "unknown"
    with pytest.raises(ProvisionError, match="reconciliation_required"):
        runner.execute_next()
    assert hardware.effects == []


def test_response_lost_after_core_confirmation_preserves_evidence_and_no_second_effect(integration):
    core, _, _, _, journal, authority, hardware = integration
    authority.drop_receipt = True
    runner = Runner(journal, authority, hardware, Template())
    with pytest.raises(ProvisionError):
        runner.execute_next()
    assert hardware.effects == ["create_vhd"]
    with core.database.transaction(write=False) as connection:
        assert connection.execute("SELECT status FROM provisioning_effects").get == "confirmed"
    assert journal.operations()["create_vhd"]["status"] == "unknown"
    with pytest.raises(ProvisionError, match="reconciliation_required"):
        runner.execute_next()
    assert runner.reconcile() == "matched"
    assert hardware.effects == ["create_vhd"]


def test_lease_expires_after_go_cannot_publish_or_continue(integration):
    core, clock, _, claim, journal, authority, hardware = integration
    hardware.before_finish = lambda: clock.__setitem__(0, clock[0] + 61)
    runner = Runner(journal, authority, hardware, Template())
    with pytest.raises(ProvisionError):
        runner.execute_next()
    assert hardware.effects == ["create_vhd"]
    assert journal.operations()["create_vhd"]["status"] == "unknown"
    core.recover_expired(claim.host_id, recovery_evidence=uuid4())
    with core.database.transaction(write=False) as connection:
        assert (
            connection.execute("SELECT status FROM provisioning_effects").get == "outcome_unknown"
        )
    with pytest.raises(ProvisionError):
        runner.execute_next()
    assert hardware.effects == ["create_vhd"]
