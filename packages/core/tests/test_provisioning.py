"""Autoridade distinta, intents duráveis e hardware parado; nenhum comando real."""

import json
import os
import secrets
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from uuid import UUID, uuid4

import apsw
import pytest
from pydantic import ValidationError

from bees_core.environments import EnvironmentService
from bees_core.models import Agent
from bees_core.provisioning import (
    DEFAULT_PROVISIONING_CATALOG,
    OPERATIONS,
    ArtifactPin,
    BeginDispatchInput,
    HardwareReceipt,
    PreparePlanInput,
    ProvisioningError,
    ProvisioningService,
    TrustedProvisioningCatalog,
)
from bees_core.security.hosts import HostService
from bees_core.security.identity import IdentityService
from bees_core.storage.database import Database
from bees_core.storage.store import NotFoundError, RevisionConflict, StateStore


@dataclass
class Setup:
    db: Database
    clock: list
    hosts: HostService
    host: dict
    diagnostic_token: str
    envs: EnvironmentService
    agent: Agent
    environment: object
    service: ProvisioningService
    catalog: TrustedProvisioningCatalog

    def report(self, **changes):
        current = self.hosts.get(UUID(self.host["host_id"]))
        self.host = self.hosts.report(
            self.diagnostic_token,
            {
                "host_id": current["host_id"],
                "sequence": current["last_report_sequence"] + 1,
                "expected_revision": current["report_revision"],
                "client_request_id": uuid4(),
                "driver": "hyperv",
                "probe": {"platform": True, "module": True, "service": True},
            }
            | changes,
        )
        return self.host

    def prepare(self):
        return self.service.prepare_plan(
            self.agent.id,
            self.environment.id,
            {
                "host_id": self.host["host_id"],
                "expected_host_revision": self.host["revision"],
                "expected_environment_revision": self.environment.revision,
                "client_request_id": uuid4(),
            },
        )

    def authorize(self, plan):
        return self.service.authorize_once(
            plan["plan_id"],
            decision(plan),
            agent_id=self.agent.id,
            environment_id=self.environment.id,
        )

    def issuer(self):
        issued = self.service.issue_provisioner(
            UUID(self.host["host_id"]),
            {
                "expected_host_revision": self.host["revision"],
                "client_request_id": uuid4(),
            },
        )
        return issued, issued.credential.get_secret_value()

    def claimed(self):
        plan = self.authorize(self.prepare())
        issued, token = self.issuer()
        claim = self.service.claim(
            token,
            {
                "plan_id": plan["plan_id"],
                "plan_hash": plan["plan_hash"],
                "owner_id": uuid4(),
                "client_request_id": uuid4(),
            },
        )
        return plan, issued, token, claim


def decision(plan, **changes):
    return {
        "expected_revision": plan["revision"],
        "plan_hash": plan["plan_hash"],
        "client_request_id": uuid4(),
    } | changes


def binding(claim, **changes):
    return {
        "claim_id": claim["claim_id"],
        "owner_id": claim["owner_id"],
        "generation": claim["generation"],
    } | changes


def begin(claim, operation="create_vhd", **changes):
    return binding(claim, operation=operation, client_request_id=uuid4()) | changes


def receipt(claim, effect, vm_id=None, **changes):
    return binding(
        claim,
        effect_request_id=effect["effect_request_id"],
        client_request_id=uuid4(),
        result={"vm_id": vm_id, "verified": True} | changes,
    )


@pytest.fixture
def setup(tmp_path):
    db = Database(tmp_path / "provisioning.sqlite3")
    db.initialize()
    clock = [1900000000.0]
    identity = IdentityService(db, clock=lambda: clock[0])
    identity.setup(identity.issue_bootstrap(), "Pessoa de teste", "senha descartavel 123456")
    hosts = HostService(db, clock=lambda: clock[0])
    invite = hosts.issue_invite(uuid4())
    diagnostic_token = "bh_" + secrets.token_urlsafe(32)
    host = hosts.pair(
        {
            "installation_id": invite.installation_id,
            "invite_token": invite.invite_token,
            "host_id": uuid4(),
            "host_credential": diagnostic_token,
            "client_request_id": uuid4(),
        }
    )
    host = hosts.confirm(
        UUID(host["host_id"]),
        {
            "expected_revision": host["revision"],
            "fingerprint": host["fingerprint"],
            "client_request_id": uuid4(),
        },
    )
    with StateStore(db).transaction() as unit:
        agent = unit.agents.create(Agent(name="Abelha de teste"))
    envs = EnvironmentService(db, host_service=hosts)
    environment = envs.request(
        agent.id,
        {
            "name": "Linux",
            "cpu_count": 3,
            "memory_mib": 6144,
            "disk_gib": 42,
            "client_request_id": uuid4(),
        },
    )
    catalog = TrustedProvisioningCatalog(
        image_iso=ArtifactPin(sha256="a" * 64, size=500),
        payload=ArtifactPin(sha256="b" * 64, size=200),
    )
    value = Setup(
        db,
        clock,
        hosts,
        host,
        diagnostic_token,
        envs,
        agent,
        environment,
        ProvisioningService(db, catalog, clock=lambda: clock[0]),
        catalog,
    )
    value.report()
    return value


def test_plan_is_canonical_immutable_safe_and_preparation_is_not_execution(setup):
    setup.report(probe={"platform": True, "module": False, "service": False})
    plan = setup.prepare()
    snapshot = plan["plan"]
    assert snapshot["vm_name"] == f"Bees-{setup.host['installation_id']}-{setup.environment.id}"
    assert snapshot["cpu_count"] == 3 and snapshot["memory_bytes"] == 6144 * 1024**2
    assert snapshot["disk_bytes"] == 42 * 1024**3
    assert snapshot["network"] == "none" and snapshot["boot"] is False
    assert not {"vm_id", "generation", "provisioner_id", "path", "url", "shell"} & snapshot.keys()
    assert plan["status"] == "prepared" and plan["context_valid"] and plan["usable"] is False
    assert setup.service.current_plan(setup.agent.id, setup.environment.id) == plan
    assert set(setup.service.active_host_summary()) == {"host_id", "revision", "fingerprint"}
    assert setup.envs.get(setup.agent.id, setup.environment.id).status == "awaiting_host"
    with setup.db.transaction() as connection:
        with pytest.raises(apsw.ConstraintError, match="immutable"):
            connection.execute("UPDATE provisioning_plans SET plan_hash=?", ("c" * 64,))
    assert DEFAULT_PROVISIONING_CATALOG.image_iso.size == 792723456


def test_prepare_and_decision_replay_conflict_and_atomic_path_ownership(setup):
    value = {
        "host_id": setup.host["host_id"],
        "expected_host_revision": setup.host["revision"],
        "expected_environment_revision": setup.environment.revision,
        "client_request_id": uuid4(),
    }
    plan = setup.service.prepare_plan(setup.agent.id, setup.environment.id, value)
    assert setup.service.prepare_plan(setup.agent.id, setup.environment.id, value) == plan
    with pytest.raises(ProvisioningError, match="request_conflict"):
        setup.service.prepare_plan(
            setup.agent.id, setup.environment.id, value | {"expected_host_revision": 99}
        )
    command = decision(plan)
    approved = setup.service.authorize_once(
        plan["plan_id"], command, agent_id=setup.agent.id, environment_id=setup.environment.id
    )
    assert (
        setup.service.authorize_once(
            plan["plan_id"], command, agent_id=setup.agent.id, environment_id=setup.environment.id
        )
        == approved
    )
    with pytest.raises(NotFoundError):
        setup.service.authorize_once(
            plan["plan_id"], command, agent_id=uuid4(), environment_id=setup.environment.id
        )
    with pytest.raises(ProvisioningError, match="request_conflict"):
        setup.service.authorize_once(plan["plan_id"], command | {"plan_hash": "f" * 64})
    with pytest.raises(RevisionConflict):
        setup.service.revoke_authorization(plan["plan_id"], decision(plan))


def test_expiration_is_not_context_invalid_and_refresh_is_explicit_cas(setup):
    approved = setup.authorize(setup.prepare())
    setup.clock[0] += 901
    current = setup.service.get(approved["plan_id"])
    assert current["status"] == "authorized" and current["context_valid"]
    assert current["reason_code"] == "authorization_expired"
    assert current["revision"] == approved["revision"]
    renewed = setup.authorize(current)
    assert renewed["revision"] == current["revision"] + 1
    assert renewed["reason_code"] is None


def test_credential_is_distinct_one_time_hash_only_and_revocable(setup):
    issued, token = setup.issuer()
    assert token.startswith("bp_") and token not in repr(issued)
    assert setup.service.session(token)["provisioner_id"] == str(issued.provisioner_id)
    for wrong in (setup.diagnostic_token, "bp_" + "x" * 43, "session-cookie", ""):
        with pytest.raises(ProvisioningError, match="credentials_invalid"):
            setup.service.session(wrong)
    with setup.db.transaction(write=False) as connection:
        contents = repr(connection.execute("SELECT * FROM provisioning_credentials").fetchall())
        events = repr(connection.execute("SELECT payload_json FROM domain_events").fetchall())
    assert token not in contents and token not in events and "credential_hash" not in events
    command = {"expected_revision": 1, "client_request_id": uuid4()}
    revoked = setup.service.revoke_provisioner(issued.provisioner_id, command)
    assert revoked["status"] == "revoked"
    assert setup.service.revoke_provisioner(issued.provisioner_id, command) == revoked
    with pytest.raises(ProvisioningError, match="credentials_invalid"):
        setup.service.session(token)


def test_human_provisioner_listing_is_readonly_paginated_sanitized_and_deterministic(setup):
    host_id = UUID(setup.host["host_id"])
    issued_ids = []
    tokens = []
    for _ in range(4):
        issued, token = setup.issuer()
        issued_ids.append(str(issued.provisioner_id))
        tokens.append(token)
        setup.service.revoke_provisioner(
            issued.provisioner_id, {"expected_revision": 1, "client_request_id": uuid4()}
        )
    # Todos os timestamps iguais exigem o desempate ID estável.
    expected = sorted(issued_ids, reverse=True)
    with setup.db.transaction(write=False) as connection:
        events = connection.execute("SELECT count(*) FROM domain_events").get
        commands = connection.execute("SELECT count(*) FROM provisioning_commands").get
    first = setup.service.list_provisioners(host_id, limit=2)
    second = setup.service.list_provisioners(host_id, limit=2, offset=2)
    assert [row["provisioner_id"] for row in first + second] == expected
    assert setup.service.list_provisioners(host_id, offset=4) == []
    assert set(first[0]) == {"provisioner_id", "installation_id", "host_id", "revision", "status"}
    assert all(row["status"] == "revoked" and row["revision"] == 2 for row in first + second)
    assert all(token not in repr(first + second) for token in tokens)
    with setup.db.transaction(write=False) as connection:
        assert connection.execute("SELECT count(*) FROM domain_events").get == events
        assert connection.execute("SELECT count(*) FROM provisioning_commands").get == commands


@pytest.mark.parametrize(
    "kwargs",
    [
        {"offset": -1},
        {"offset": True},
        {"offset": 1000001},
        {"limit": 0},
        {"limit": 102},
        {"limit": True},
    ],
)
def test_provisioner_listing_bounds_are_explicit(setup, kwargs):
    with pytest.raises(ValueError):
        setup.service.list_provisioners(UUID(setup.host["host_id"]), **kwargs)


def test_provisioner_listing_unknown_host_and_identity_required(setup, tmp_path):
    with pytest.raises(NotFoundError):
        setup.service.list_provisioners(uuid4())
    db = Database(tmp_path / "no-identity.sqlite3")
    db.initialize()
    with pytest.raises(ProvisioningError, match="identity_required"):
        ProvisioningService(db).list_provisioners(uuid4())


def test_provisioner_rows_from_foreign_installation_are_not_listed_or_revoked(setup):
    issued, _ = setup.issuer()
    foreign_id = uuid4()
    host_id = UUID(setup.host["host_id"])
    with setup.db.transaction() as connection:
        connection.execute(
            "INSERT INTO provisioning_credentials(id,installation_id,host_id,host_revision,"
            "credential_hash,status,issue_request_id,created_at,revoked_at) "
            "VALUES(?,?,?,?,?,'revoked',?,?,?)",
            (
                str(foreign_id),
                str(uuid4()),
                str(host_id),
                setup.host["revision"],
                secrets.token_hex(32),
                str(uuid4()),
                setup.clock[0],
                setup.clock[0],
            ),
        )
    assert [row["provisioner_id"] for row in setup.service.list_provisioners(host_id)] == [
        str(issued.provisioner_id)
    ]
    with pytest.raises(NotFoundError):
        setup.service.revoke_provisioner(
            foreign_id, {"expected_revision": 1, "client_request_id": uuid4()}, host_id=host_id
        )


def test_provisioner_listing_and_cleanup_remain_available_after_host_revocation(setup):
    issued, _ = setup.issuer()
    host_id = UUID(setup.host["host_id"])
    setup.hosts.revoke(
        host_id,
        {"expected_revision": setup.host["revision"], "client_request_id": uuid4()},
    )
    assert setup.service.list_provisioners(host_id)[0]["status"] == "active"
    assert (
        setup.service.revoke_provisioner(
            issued.provisioner_id,
            {"expected_revision": 1, "client_request_id": uuid4()},
            host_id=host_id,
        )["status"]
        == "revoked"
    )


def pending_host(setup):
    invite = setup.hosts.issue_invite(uuid4())
    return setup.hosts.pair(
        {
            "installation_id": invite.installation_id,
            "invite_token": invite.invite_token,
            "host_id": uuid4(),
            "host_credential": "bh_" + secrets.token_urlsafe(32),
            "client_request_id": uuid4(),
        }
    )


def test_human_revoke_exact_host_binding_is_checked_before_effect_and_replay(setup):
    issued, token = setup.issuer()
    other = pending_host(setup)
    command = {"expected_revision": 1, "client_request_id": uuid4()}
    with pytest.raises(NotFoundError):
        setup.service.revoke_provisioner(
            issued.provisioner_id, command, host_id=UUID(other["host_id"])
        )
    assert setup.service.session(token)["status"] == "active"
    revoked = setup.service.revoke_provisioner(
        issued.provisioner_id, command, host_id=UUID(setup.host["host_id"])
    )
    assert revoked["status"] == "revoked"
    with pytest.raises(NotFoundError):
        setup.service.revoke_provisioner(
            issued.provisioner_id, command, host_id=UUID(other["host_id"])
        )
    assert setup.service.revoke_provisioner(issued.provisioner_id, command) == revoked
    assert setup.service.list_provisioners(UUID(other["host_id"])) == []


@pytest.mark.parametrize("unknown", [False, True])
def test_human_revoke_blocks_dispatch_without_repeating_or_rewriting_claim(setup, unknown):
    _, issued, token, claim = setup.claimed()
    dispatched = setup.service.begin_dispatch(token, begin(claim))
    if unknown:
        setup.service.mark_unknown(
            token,
            binding(
                claim,
                effect_request_id=dispatched["effect_request_id"],
                client_request_id=uuid4(),
            ),
        )
    with setup.db.transaction(write=False) as connection:
        claim_before = connection.execute(
            "SELECT * FROM provisioning_claims WHERE id=?", (claim["claim_id"],)
        ).fetchone()
        effect_before = connection.execute(
            "SELECT * FROM provisioning_effects WHERE effect_request_id=?",
            (dispatched["effect_request_id"],),
        ).fetchone()
    setup.service.revoke_provisioner(
        issued.provisioner_id,
        {"expected_revision": 1, "client_request_id": uuid4()},
        host_id=UUID(setup.host["host_id"]),
    )
    with pytest.raises(ProvisioningError, match="credentials_invalid"):
        setup.service.assert_current(token, binding(claim))
    with setup.db.transaction(write=False) as connection:
        assert (
            connection.execute(
                "SELECT * FROM provisioning_claims WHERE id=?", (claim["claim_id"],)
            ).fetchone()
            == claim_before
        )
        assert (
            connection.execute(
                "SELECT * FROM provisioning_effects WHERE effect_request_id=?",
                (dispatched["effect_request_id"],),
            ).fetchone()
            == effect_before
        )


def test_claim_has_no_consumption_and_expiration_without_intent_can_be_reclaimed(setup):
    plan, _, token, claim = setup.claimed()
    with setup.db.transaction(write=False) as connection:
        assert (
            connection.execute("SELECT consumed_claim_id FROM provisioning_authorizations").get
            is None
        )
    setup.clock[0] += 61
    setup.report()
    recovered = setup.service.recover_expired(UUID(claim["host_id"]), recovery_evidence=uuid4())
    assert recovered["status"] == "aborted"
    new_claim = setup.service.claim(
        token,
        {
            "plan_id": plan["plan_id"],
            "plan_hash": plan["plan_hash"],
            "owner_id": uuid4(),
            "client_request_id": uuid4(),
        },
    )
    assert new_claim["generation"] == claim["generation"] + 1
    with pytest.raises(ProvisioningError, match="claim_fenced"):
        setup.service.begin_dispatch(token, begin(claim))
    with setup.db.transaction(write=False) as connection:
        assert (
            connection.execute("SELECT consumed_claim_id FROM provisioning_authorizations").get
            is None
        )


def test_claim_race_one_host_and_begin_race_one_intent(setup):
    plan = setup.authorize(setup.prepare())
    _, token = setup.issuer()
    value = {
        "plan_id": plan["plan_id"],
        "plan_hash": plan["plan_hash"],
        "owner_id": uuid4(),
        "client_request_id": uuid4(),
    }
    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(lambda _: setup.service.claim(token, value), range(2)))
    assert claims[0] == claims[1]
    command = begin(claims[0])
    with ThreadPoolExecutor(max_workers=2) as pool:
        effects = list(pool.map(lambda _: setup.service.begin_dispatch(token, command), range(2)))
    assert sorted(effect["dispatch_allowed"] for effect in effects) == [False, True]
    assert len({effect["effect_request_id"] for effect in effects}) == 1
    with setup.db.transaction(write=False) as connection:
        assert connection.execute("SELECT count(*) FROM provisioning_effects").get == 1
        assert (
            connection.execute("SELECT consumed_claim_id FROM provisioning_authorizations").get
            == claims[0]["claim_id"]
        )
    assert setup.envs.get(setup.agent.id, setup.environment.id).status == "provisioning"


@pytest.mark.parametrize(
    "change",
    [
        "cancel",
        "revoke",
        "host",
        "credential",
        "catalog",
        "owner",
        "generation",
        "offline",
        "driver",
    ],
)
def test_before_effect_guards_fail_closed(setup, change):
    plan, issued, token, claim = setup.claimed()
    command = begin(claim)
    if change == "cancel":
        setup.envs.cancel(
            setup.agent.id,
            setup.environment.id,
            {"expected_revision": setup.environment.revision, "client_request_id": uuid4()},
        )
    elif change == "revoke":
        setup.service.revoke_authorization(plan["plan_id"], decision(plan))
    elif change == "host":
        setup.hosts.revoke(
            UUID(setup.host["host_id"]),
            {"expected_revision": setup.host["revision"], "client_request_id": uuid4()},
        )
    elif change == "credential":
        setup.service.revoke_provisioner(
            issued.provisioner_id, {"expected_revision": 1, "client_request_id": uuid4()}
        )
    elif change == "catalog":
        setup.service.catalog = DEFAULT_PROVISIONING_CATALOG
    elif change == "owner":
        command["owner_id"] = uuid4()
    elif change == "generation":
        command["generation"] += 1
    elif change == "offline":
        setup.clock[0] += 60
    else:
        setup.report(probe={"platform": True, "module": False, "service": True})
    with pytest.raises(ProvisioningError):
        setup.service.begin_dispatch(token, command)
    with setup.db.transaction(write=False) as connection:
        assert connection.execute("SELECT count(*) FROM provisioning_effects").get == 0
        assert (
            connection.execute("SELECT consumed_claim_id FROM provisioning_authorizations").get
            is None
        )


def test_sequence_receipts_publish_real_vmid_only_then_verify_keeps_unusable(setup):
    plan, _, token, claim = setup.claimed()
    vm_id = str(uuid4())
    with pytest.raises(ProvisioningError, match="operation_conflict"):
        setup.service.begin_dispatch(token, begin(claim, "create_vm"))
    for operation in OPERATIONS:
        command = begin(claim, operation)
        effect = setup.service.begin_dispatch(token, command)
        assert effect["dispatch_allowed"] and not effect["cached"]
        assert not setup.service.begin_dispatch(token, command)["dispatch_allowed"]
        current_vm = None if operation == "create_vhd" else vm_id
        extras = (
            {}
            if operation != "verify"
            else {
                "cpu_count": plan["plan"]["cpu_count"],
                "memory_bytes": plan["plan"]["memory_bytes"],
                "disk_bytes": plan["plan"]["disk_bytes"],
                "powered_off": True,
                "network_none": True,
                "image_iso_sha256": plan["plan"]["image_iso_sha256"],
            }
        )
        output = receipt(claim, effect, current_vm, **extras)
        result = setup.service.record_receipt(token, output)
        assert result["status"] == "confirmed" and not result["dispatch_allowed"]
        assert setup.service.record_receipt(token, output)["cached"]
    assert result["claim"]["status"] == "confirmed"
    current = setup.envs.get(setup.agent.id, setup.environment.id)
    assert current.status == "provisioning" and not setup.envs.view(current)["usable"]
    assert setup.service.get(plan["plan_id"])["status"] == "hardware_verified"


def test_unknown_retains_exclusive_host_even_after_ack_revoke_and_replacement_credential(setup):
    plan, issued, token, claim = setup.claimed()
    effect = setup.service.begin_dispatch(token, begin(claim))
    value = binding(claim, effect_request_id=effect["effect_request_id"], client_request_id=uuid4())
    unknown = setup.service.mark_unknown(token, value)
    assert unknown["status"] == "outcome_unknown"
    assert setup.service.mark_unknown(token, value) == unknown
    acknowledged = setup.service.acknowledge_unknown(
        UUID(claim["claim_id"]),
        {"expected_revision": unknown["revision"], "client_request_id": uuid4()},
    )
    assert acknowledged["status"] == "outcome_unknown"
    setup.service.revoke_provisioner(
        issued.provisioner_id, {"expected_revision": 1, "client_request_id": uuid4()}
    )
    _, replacement = setup.issuer()
    other_environment = setup.envs.request(
        setup.agent.id, {"name": "Outra", "client_request_id": uuid4()}
    )
    setup.environment = other_environment
    other_plan = setup.authorize(setup.prepare())
    with pytest.raises(ProvisioningError, match="host_quarantined"):
        setup.service.claim(
            replacement,
            {
                "plan_id": other_plan["plan_id"],
                "plan_hash": other_plan["plan_hash"],
                "owner_id": uuid4(),
                "client_request_id": uuid4(),
            },
        )
    assert setup.service.get(plan["plan_id"])["status"] == "outcome_unknown"


def test_expired_dispatch_is_unknown_and_renew_never_revives_old_owner(setup):
    _, _, token, claim = setup.claimed()
    effect = setup.service.begin_dispatch(token, begin(claim))
    setup.clock[0] += 30
    setup.report()
    renew_value = binding(claim, client_request_id=uuid4())
    renewed = setup.service.renew(token, renew_value)
    setup.clock[0] += 1
    assert (
        setup.service.renew(token, renew_value)["lease_expires_at"] == renewed["lease_expires_at"]
    )
    setup.clock[0] += 60
    setup.report()
    with pytest.raises(ProvisioningError, match="claim_fenced"):
        setup.service.renew(token, binding(claim, client_request_id=uuid4()))
    unknown = setup.service.recover_expired(UUID(claim["host_id"]), recovery_evidence=uuid4())
    assert unknown["status"] == "outcome_unknown"
    with pytest.raises(ProvisioningError, match="claim_fenced"):
        setup.service.record_receipt(token, receipt(claim, effect))
    assert setup.envs.get(setup.agent.id, setup.environment.id).status == "outcome_unknown"


def test_atomic_rollback_after_first_intent_conserves_authorization_and_environment(
    setup, monkeypatch
):
    plan, _, token, claim = setup.claimed()
    event = setup.service._event

    def fail(*args, **kwargs):
        if args[2] == "dispatch_started":
            raise RuntimeError("controlled failure after intent")
        return event(*args, **kwargs)

    monkeypatch.setattr(setup.service, "_event", fail)
    with pytest.raises(RuntimeError):
        setup.service.begin_dispatch(token, begin(claim))
    assert setup.envs.get(setup.agent.id, setup.environment.id).status == "awaiting_host"
    with setup.db.transaction(write=False) as connection:
        assert connection.execute("SELECT status FROM host_jobs").get == "awaiting_host"
        assert connection.execute("SELECT count(*) FROM provisioning_effects").get == 0
        assert (
            connection.execute("SELECT consumed_claim_id FROM provisioning_authorizations").get
            is None
        )
    assert setup.service.get(plan["plan_id"])["revision"] == plan["revision"]


@pytest.mark.parametrize(
    "changed",
    [
        {"path": "/tmp/root"},
        {"vm_id": uuid4()},
        {"image_iso_sha256": "f" * 64},
        {"shell": "command"},
        {"expected_host_revision": True},
        {"host_id": UUID(int=0)},
    ],
)
def test_human_plan_input_has_no_executor_authority(changed):
    with pytest.raises(ValidationError):
        PreparePlanInput.model_validate(
            {
                "host_id": uuid4(),
                "expected_environment_revision": 1,
                "expected_host_revision": 2,
                "client_request_id": uuid4(),
            }
            | changed
        )


def test_opaque_effect_arguments_and_invalid_inventory_rejected(setup):
    _, _, token, claim = setup.claimed()
    with pytest.raises(ValidationError):
        BeginDispatchInput.model_validate(begin(claim) | {"shell": "New-VM evil"})
    effect = setup.service.begin_dispatch(token, begin(claim))
    with pytest.raises(ProvisioningError, match="receipt_invalid"):
        setup.service.record_receipt(token, receipt(claim, effect, str(uuid4())))
    for changes in ({"verified": 1}, {"path": "private"}, {"vm_id": UUID(int=0)}):
        with pytest.raises(ValidationError):
            HardwareReceipt.model_validate({"verified": True, "vm_id": None} | changes)
    assert setup.service.assert_current(token, binding(claim))["status"] == "dispatch_started"


def test_stale_context_read_and_revoke_does_not_require_active_agent_or_host(setup):
    plan = setup.authorize(setup.prepare())
    setup.hosts.revoke(
        UUID(setup.host["host_id"]),
        {"expected_revision": setup.host["revision"], "client_request_id": uuid4()},
    )
    with StateStore(setup.db).transaction() as unit:
        agent = unit.agents.get(setup.agent.id)
        unit.agents.update(agent.model_copy(update={"status": "paused"}), agent.revision)
    stale = setup.service.get(plan["plan_id"])
    assert not stale["context_valid"] and stale["reason_code"] == "host_changed"
    revoked = setup.service.revoke_authorization(
        plan["plan_id"],
        decision(stale),
        agent_id=setup.agent.id,
        environment_id=setup.environment.id,
    )
    assert revoked["status"] == "revoked"


def test_current_schema_preserves_existing_environment_and_foreign_keys(setup):
    assert setup.db.schema_version() == 9
    with setup.db.transaction(write=False) as connection:
        assert connection.execute("PRAGMA integrity_check").get == "ok"
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert connection.execute("SELECT count(*) FROM environments").get == 1
        assert connection.execute("SELECT count(*) FROM provisioning_claims").get == 0


def test_actual_process_crash_after_durable_intent_never_reauthorizes_retry(setup):
    _, _, token, claim = setup.claimed()
    command = begin(claim)
    script = """
import json,os
from bees_core.provisioning import ProvisioningService,TrustedProvisioningCatalog
from bees_core.storage.database import Database
service=ProvisioningService(Database(os.environ['TEST_DB']),
    TrustedProvisioningCatalog.model_validate_json(os.environ['TEST_CATALOG']),
    clock=lambda:float(os.environ['TEST_NOW']))
service.begin_dispatch(os.environ['TEST_TOKEN'],json.loads(os.environ['TEST_COMMAND']))
os._exit(23)
"""
    env = os.environ | {
        "PYTHONPATH": str(Path(__file__).parents[1] / "src"),
        "TEST_DB": str(setup.db.path),
        "TEST_CATALOG": setup.catalog.model_dump_json(),
        "TEST_NOW": str(setup.clock[0]),
        "TEST_TOKEN": token,
        "TEST_COMMAND": json.dumps(command, default=str),
    }
    child = subprocess.run([sys.executable, "-c", script], env=env, capture_output=True, timeout=10)
    assert child.returncode == 23, "controlled crash did not reach intent"
    restarted = ProvisioningService(
        Database(setup.db.path), setup.catalog, clock=lambda: setup.clock[0]
    )
    replay = restarted.begin_dispatch(token, command)
    assert replay["cached"] and not replay["dispatch_allowed"]
    with pytest.raises(ProvisioningError, match="operation_conflict"):
        restarted.begin_dispatch(token, begin(claim))
    setup.clock[0] += 61
    setup.report()
    unknown = restarted.recover_expired(UUID(claim["host_id"]), recovery_evidence=uuid4())
    assert unknown["status"] == "outcome_unknown"


def test_two_different_owners_cannot_share_host_claim(setup):
    plan = setup.authorize(setup.prepare())
    _, token = setup.issuer()

    def acquire(_):
        try:
            return setup.service.claim(
                token,
                {
                    "plan_id": plan["plan_id"],
                    "plan_hash": plan["plan_hash"],
                    "owner_id": uuid4(),
                    "client_request_id": uuid4(),
                },
            )
        except ProvisioningError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(acquire, range(2)))
    assert sum(isinstance(value, dict) for value in results) == 1
    assert "provisioning_host_quarantined" in results


def test_refreshed_human_authorization_never_revalidates_old_claim(setup):
    setup.service.AUTHORIZATION_TTL = 30
    plan, _, token, claim = setup.claimed()
    setup.clock[0] += 31
    refreshed = setup.authorize(setup.service.get(plan["plan_id"]))
    assert refreshed["revision"] == plan["revision"] + 1
    with pytest.raises(ProvisioningError, match="authorization_changed"):
        setup.service.assert_current(token, binding(claim))
    with setup.db.transaction(write=False) as connection:
        assert connection.execute("SELECT count(*) FROM provisioning_effects").get == 0
    setup.clock[0] += 30
    setup.report()
    setup.service.recover_expired(UUID(claim["host_id"]), recovery_evidence=uuid4())
    setup.authorize(setup.service.get(plan["plan_id"]))
    next_claim = setup.service.claim(
        token,
        {
            "plan_id": plan["plan_id"],
            "plan_hash": plan["plan_hash"],
            "owner_id": uuid4(),
            "client_request_id": uuid4(),
        },
    )
    assert next_claim["generation"] == claim["generation"] + 1


def test_revoke_during_effect_blocks_next_effect_and_new_publication_without_losing_intent(setup):
    plan, _, token, claim = setup.claimed()
    effect = setup.service.begin_dispatch(token, begin(claim))
    current = setup.service.get(plan["plan_id"])
    revoked = setup.service.revoke_authorization(plan["plan_id"], decision(current))
    assert revoked["status"] == "revoked"
    setup.environment = setup.envs.get(setup.agent.id, setup.environment.id)
    with pytest.raises(ProvisioningError, match="environment_changed"):
        setup.prepare()
    for callback in (
        lambda: setup.service.record_receipt(token, receipt(claim, effect)),
        lambda: setup.service.begin_dispatch(token, begin(claim, "create_vm")),
        lambda: setup.service.renew(token, binding(claim, client_request_id=uuid4())),
    ):
        with pytest.raises(ProvisioningError, match="authorization_changed"):
            callback()
    with setup.db.transaction(write=False) as connection:
        assert (
            connection.execute("SELECT status FROM provisioning_effects").get == "dispatch_started"
        )
        assert (
            connection.execute("SELECT consumed_claim_id FROM provisioning_authorizations").get
            == claim["claim_id"]
        )
    setup.clock[0] += 61
    unknown = setup.service.recover_expired(UUID(claim["host_id"]), recovery_evidence=uuid4())
    assert unknown["status"] == "outcome_unknown"
    assert setup.service.get(plan["plan_id"])["status"] == "outcome_unknown"


def test_upgrade7_to8_is_additive_and_preserves_immutable_legacy_requests(tmp_path, monkeypatch):
    import bees_core.storage.database as module

    original = resources.files("bees_core.storage.migrations")
    directory = tmp_path / "migrations"
    directory.mkdir()
    for file in original.iterdir():
        if file.name.endswith(".sql") and int(file.name[:4]) <= 7:
            (directory / file.name).write_bytes(file.read_bytes())
    monkeypatch.setattr(module.resources, "files", lambda _: directory)
    database = Database(tmp_path / "legacy.sqlite3")
    database.initialize()
    with StateStore(database).transaction() as unit:
        agent = unit.agents.create(Agent(name="Legado preservado"))
    service = EnvironmentService(database)
    environment = service.request(
        agent.id, {"name": "Antes da migração", "client_request_id": uuid4()}
    )
    with database.transaction(write=False) as connection:
        before = connection.execute("SELECT * FROM environments").fetchall()
        schema = connection.execute("SELECT sql FROM sqlite_schema WHERE name='environments'").get
        history = connection.execute(
            "SELECT version,name,checksum FROM schema_migrations"
        ).fetchall()
    (directory / "0008_provisioning.sql").write_bytes(
        (original / "0008_provisioning.sql").read_bytes()
    )
    database.initialize()
    assert database.schema_version() == 8
    with database.transaction(write=False) as connection:
        assert connection.execute("SELECT * FROM environments").fetchall() == before
        assert (
            connection.execute("SELECT sql FROM sqlite_schema WHERE name='environments'").get
            == schema
        )
        assert (
            connection.execute(
                "SELECT version,name,checksum FROM schema_migrations WHERE version<=7"
            ).fetchall()
            == history
        )
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    assert service.get(agent.id, environment.id) == environment
    assert len(list(tmp_path.glob("*.backup-*.sqlite3"))) == 1


def test_new_logical_host_pairing_cannot_evade_old_installation_unknown(setup):
    _, _, token, claim = setup.claimed()
    effect = setup.service.begin_dispatch(token, begin(claim))
    setup.service.mark_unknown(
        token,
        binding(claim, effect_request_id=effect["effect_request_id"], client_request_id=uuid4()),
    )
    setup.hosts.revoke(
        UUID(setup.host["host_id"]),
        {"expected_revision": setup.host["revision"], "client_request_id": uuid4()},
    )
    invite = setup.hosts.issue_invite(uuid4())
    new_token = "bh_" + secrets.token_urlsafe(32)
    host = setup.hosts.pair(
        {
            "installation_id": invite.installation_id,
            "invite_token": invite.invite_token,
            "host_id": uuid4(),
            "host_credential": new_token,
            "client_request_id": uuid4(),
        }
    )
    setup.host = setup.hosts.confirm(
        UUID(host["host_id"]),
        {
            "expected_revision": host["revision"],
            "fingerprint": host["fingerprint"],
            "client_request_id": uuid4(),
        },
    )
    setup.diagnostic_token = new_token
    setup.report()
    setup.environment = setup.envs.request(
        setup.agent.id, {"name": "Outro host lógico", "client_request_id": uuid4()}
    )
    new_plan = setup.authorize(setup.prepare())
    _, credential = setup.issuer()
    with pytest.raises(ProvisioningError, match="host_quarantined"):
        setup.service.claim(
            credential,
            {
                "plan_id": new_plan["plan_id"],
                "plan_hash": new_plan["plan_hash"],
                "owner_id": uuid4(),
                "client_request_id": uuid4(),
            },
        )


def test_vmid_is_immutable_and_final_inventory_cannot_claim_powered_on_or_nic(setup):
    plan, _, token, claim = setup.claimed()
    vm_id = str(uuid4())
    for operation in OPERATIONS[:-1]:
        effect = setup.service.begin_dispatch(token, begin(claim, operation))
        if operation == "configure_vm":
            with pytest.raises(ProvisioningError, match="receipt_invalid"):
                setup.service.record_receipt(token, receipt(claim, effect, str(uuid4())))
        setup.service.record_receipt(
            token, receipt(claim, effect, None if operation == "create_vhd" else vm_id)
        )
    effect = setup.service.begin_dispatch(token, begin(claim, "verify"))
    extras = {
        "cpu_count": plan["plan"]["cpu_count"],
        "memory_bytes": plan["plan"]["memory_bytes"],
        "disk_bytes": plan["plan"]["disk_bytes"],
        "image_iso_sha256": plan["plan"]["image_iso_sha256"],
        "powered_off": True,
        "network_none": True,
    }
    for change in (
        {"powered_off": False},
        {"network_none": False},
        {"cpu_count": 4},
        {"image_iso_sha256": "f" * 64},
    ):
        with pytest.raises(ProvisioningError, match="receipt_invalid"):
            setup.service.record_receipt(token, receipt(claim, effect, vm_id, **(extras | change)))
    result = setup.service.record_receipt(token, receipt(claim, effect, vm_id, **extras))
    assert result["claim"]["status"] == "confirmed"
    with setup.db.transaction() as connection:
        with pytest.raises(apsw.ConstraintError, match="immutable"):
            connection.execute("UPDATE provisioning_vm_bindings SET vm_id=?", (str(uuid4()),))


def test_credential_issue_replay_and_unconfigured_identity_refuse_secret_recovery(setup, tmp_path):
    command = {"expected_host_revision": setup.host["revision"], "client_request_id": uuid4()}
    setup.service.issue_provisioner(UUID(setup.host["host_id"]), command)
    with pytest.raises(ProvisioningError, match="credential_already_issued"):
        setup.service.issue_provisioner(UUID(setup.host["host_id"]), command)
    missing = Database(tmp_path / "missing-identity.sqlite3")
    missing.initialize()
    with pytest.raises(ProvisioningError, match="identity_required"):
        ProvisioningService(missing).issue_provisioner(
            uuid4(), {"expected_host_revision": 1, "client_request_id": uuid4()}
        )


def test_heartbeat_only_changes_report_revision_never_human_authority_or_claim_lease(setup):
    _, _, token, claim = setup.claimed()
    original_host_revision = setup.host["revision"]
    setup.clock[0] += 30
    current = setup.report()
    assert current["revision"] == original_host_revision
    observed = setup.service.assert_current(token, binding(claim))
    assert observed["lease_expires_at"] == claim["lease_expires_at"]
    assert observed["revision"] == claim["revision"]
