"""Pareamento escopado, decisões humanas e diagnóstico sem autoridade de VM."""

import secrets
from concurrent.futures import ThreadPoolExecutor
from importlib import resources
from uuid import UUID, uuid4

import apsw
import pytest
from pydantic import ValidationError

from bees_core.environments import EnvironmentService
from bees_core.models import Agent
from bees_core.security.hosts import (
    HostConfirmInput,
    HostLinkError,
    HostPairInput,
    HostReportInput,
    HostService,
    pairing_fingerprint,
)
from bees_core.security.identity import IdentityService
from bees_core.storage import database as database_module
from bees_core.storage.database import Database
from bees_core.storage.store import NotFoundError, RevisionConflict, StateStore


@pytest.fixture
def setup(tmp_path):
    database = Database(tmp_path / "hosts.sqlite3")
    database.initialize()
    clock = [1900000000.0]
    identity = IdentityService(database, clock=lambda: clock[0])
    identity.setup(identity.issue_bootstrap(), "Pessoa de teste", "senha descartavel 123456")
    return database, clock, HostService(database, clock=lambda: clock[0])


def pair(service):
    invite = service.issue_invite(uuid4())
    value = HostPairInput(
        installation_id=invite.installation_id,
        invite_token=invite.invite_token,
        host_id=uuid4(),
        host_credential="bh_" + secrets.token_urlsafe(32),
        client_request_id=uuid4(),
    )
    return value, service.pair(value), invite


def confirm(service, dto):
    value = {
        "expected_revision": dto["revision"],
        "client_request_id": uuid4(),
        "fingerprint": dto["fingerprint"],
    }
    return service.confirm(UUID(dto["host_id"]), value), value


def report(dto, **changes):
    return {
        "host_id": dto["host_id"],
        "sequence": dto["last_report_sequence"] + 1,
        "expected_revision": dto["report_revision"],
        "client_request_id": uuid4(),
        "driver": "hyperv",
        "probe": {"platform": True, "module": False, "service": False},
    } | changes


def token(value):
    return value.host_credential.get_secret_value()


def test_issuer_requires_identity_and_never_stores_or_reveals_secrets(tmp_path, setup):
    missing = Database(tmp_path / "unconfigured.sqlite3")
    missing.initialize()
    with pytest.raises(HostLinkError) as rejected:
        HostService(missing).issue_invite(uuid4())
    assert rejected.value.code == "host_identity_required"
    database, _, service = setup
    value, dto, invite = pair(service)
    assert invite.invite_token.get_secret_value() not in repr(invite)
    assert token(value) not in repr(value)
    with database.transaction(write=False) as connection:
        contents = repr(connection.execute("SELECT * FROM host_link_invites").fetchall())
        contents += repr(connection.execute("SELECT * FROM host_links").fetchall())
        contents += repr(connection.execute("SELECT payload_json FROM domain_events").fetchall())
    assert token(value) not in contents and invite.invite_token.get_secret_value() not in contents
    assert not {"credential_hash", "invite_token", "request_hash", "host_credential"} & dto.keys()
    with StateStore(database).transaction(write=False) as unit:
        events = unit.events.list(entity_type="host_link")
        assert len(events) == 2
        assert "hash" not in repr([event.payload for event in events])


def test_issuer_retry_refuses_secret_recovery_and_new_invite_invalidates_unused(setup):
    _, _, service = setup
    request_id = uuid4()
    first = service.issue_invite(request_id)
    with pytest.raises(HostLinkError) as rejected:
        service.issue_invite(request_id)
    assert rejected.value.code == "host_invite_already_issued"
    second = service.issue_invite(uuid4())
    assert second.installation_id == first.installation_id
    value = {
        "installation_id": first.installation_id,
        "invite_token": first.invite_token,
        "host_id": uuid4(),
        "host_credential": "bh_" + secrets.token_urlsafe(32),
        "client_request_id": uuid4(),
    }
    with pytest.raises(HostLinkError) as rejected:
        service.pair(value)
    assert rejected.value.code == "host_invite_invalid"


def test_pair_restart_replay_and_fingerprint_bind_all_identifiers(setup):
    database, clock, service = setup
    value, dto, invite = pair(service)
    restored = HostService(Database(database.path), clock=lambda: clock[0])
    assert restored.pair(value) == dto
    assert restored.session(token(value)) == dto
    expected = pairing_fingerprint(
        invite.installation_id, invite.invite_id, value.host_id, token(value)
    )
    assert expected == dto["fingerprint"] and len(expected) == 19
    assert pairing_fingerprint(uuid4(), invite.invite_id, value.host_id, token(value)) != expected
    assert (
        pairing_fingerprint(invite.installation_id, uuid4(), value.host_id, token(value))
        != expected
    )
    assert (
        pairing_fingerprint(invite.installation_id, invite.invite_id, uuid4(), token(value))
        != expected
    )
    assert dto["status"] == "pending" and dto["confirmable"] and not dto["online"]
    assert not dto["provisionable"] and dto["diagnostic"] is None


@pytest.mark.parametrize(
    "field", ["host_id", "client_request_id", "host_credential", "installation_id"]
)
def test_pair_replay_divergence_never_creates_new_host(setup, field):
    _, _, service = setup
    value, _, _ = pair(service)
    changed = value.model_dump()
    changed[field] = "bh_" + secrets.token_urlsafe(32) if field == "host_credential" else uuid4()
    with pytest.raises(HostLinkError):
        service.pair(changed)
    assert len(service.list()) == 1


def test_concurrent_invite_exchange_only_consumes_once(setup):
    _, _, service = setup
    invite = service.issue_invite(uuid4())
    value = HostPairInput(
        installation_id=invite.installation_id,
        invite_token=invite.invite_token,
        host_id=uuid4(),
        host_credential="bh_" + secrets.token_urlsafe(32),
        client_request_id=uuid4(),
    )
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: service.pair(value), range(2)))
    assert results[0] == results[1] and len(service.list()) == 1


def test_invite_and_pending_expiry_are_separate_fail_closed_boundaries(setup):
    _, clock, service = setup
    value, dto, _ = pair(service)
    clock[0] += 300
    with pytest.raises(HostLinkError):
        service.pair(value)
    assert service.session(token(value))["status"] == "pending"
    clock[0] += 600
    assert not service.get(value.host_id)["confirmable"]
    with pytest.raises(HostLinkError) as rejected:
        confirm(service, dto)
    assert rejected.value.code == "host_pair_expired"
    with pytest.raises(HostLinkError):
        service.session(token(value))
    assert (
        service.revoke(value.host_id, {"expected_revision": 1, "client_request_id": uuid4()})[
            "status"
        ]
        == "revoked"
    )


def test_unconsumed_invite_expiry_does_not_register_host(setup):
    _, clock, service = setup
    invite = service.issue_invite(uuid4())
    clock[0] += 300
    with pytest.raises(HostLinkError):
        service.pair(
            {
                "installation_id": invite.installation_id,
                "invite_token": invite.invite_token,
                "host_id": uuid4(),
                "host_credential": "bh_" + secrets.token_urlsafe(32),
                "client_request_id": uuid4(),
            }
        )
    assert service.list() == []


def test_human_confirm_code_cas_replay_and_explicit_single_active(setup):
    _, _, service = setup
    value, dto, _ = pair(service)
    command = {
        "expected_revision": 1,
        "client_request_id": uuid4(),
        "fingerprint": "0000-0000-0000-0000",
    }
    with pytest.raises(HostLinkError) as rejected:
        service.confirm(value.host_id, command)
    assert rejected.value.code == "host_fingerprint_mismatch"
    with pytest.raises(RevisionConflict):
        service.confirm(value.host_id, command | {"expected_revision": 2})
    active, command = confirm(service, dto)
    assert active["revision"] == 2 and active["status"] == "active"
    assert service.confirm(value.host_id, command) == active
    with pytest.raises(HostLinkError):
        service.confirm(value.host_id, command | {"fingerprint": "0000-0000-0000-0000"})
    second, other, _ = pair(service)
    with pytest.raises(HostLinkError) as rejected:
        confirm(service, other)
    assert rejected.value.code == "host_active_exists"
    service.revoke(value.host_id, {"expected_revision": 2, "client_request_id": uuid4()})
    approved, _ = confirm(service, other)
    assert approved["host_id"] == str(second.host_id)


def test_pending_reports_and_user_or_invite_credentials_cannot_authorize_host(setup):
    _, _, service = setup
    value, dto, invite = pair(service)
    with pytest.raises(HostLinkError):
        service.report(token(value), report(dto))
    for wrong in [None, "x" * 43, invite.invite_token.get_secret_value(), "bh_" + "x" * 43]:
        with pytest.raises(HostLinkError):
            service.authenticate(wrong)
    with pytest.raises(HostLinkError):
        service.authenticate(token(value), active_only=True)
    assert service.authenticate(token(value))["status"] == "pending"


def test_report_freshness_server_clock_and_replay_do_not_resurrect_old_observation(setup):
    database, clock, service = setup
    value, dto, _ = pair(service)
    dto, _ = confirm(service, dto)
    payload = report(dto)
    reported = service.report(token(value), payload)
    assert reported["online"] and reported["diagnostic"]["status"] == "driver_unavailable"
    assert reported["revision"] == 2 and reported["report_revision"] == 1
    assert reported["last_report_request_id"] == str(payload["client_request_id"])
    seen = reported["last_seen"]
    clock[0] += 60
    restored = HostService(Database(database.path), clock=lambda: clock[0])
    stale = restored.report(token(value), payload)
    assert not stale["online"] and stale["diagnostic"] is None and stale["last_seen"] == seen
    assert restored.paired_status() is None
    current = restored.report(token(value), report(stale))
    assert current["online"] and current["last_seen"] != seen
    replay_old = restored.report(token(value), payload)
    assert replay_old == current
    with pytest.raises(HostLinkError):
        restored.report(
            token(value), report(current, client_request_id=payload["client_request_id"])
        )
    clock[0] -= 61
    assert not restored.get(value.host_id)["online"]


def test_report_divergence_sequence_cas_foreign_host_and_revocation_are_fenced(setup):
    _, _, service = setup
    value, dto, _ = pair(service)
    dto, _ = confirm(service, dto)
    payload = report(dto)
    current = service.report(token(value), payload)
    for changed in [
        payload | {"driver": "libvirt", "probe": {"platform": True, "kvm": True, "service": True}},
        report(current, sequence=1),
        report(current, expected_revision=0),
        report(current, host_id=uuid4()),
    ]:
        with pytest.raises(HostLinkError):
            service.report(token(value), changed)
    command = {"expected_revision": current["revision"], "client_request_id": uuid4()}
    revoked = service.revoke(value.host_id, command)
    assert service.revoke(value.host_id, command) == revoked
    assert revoked["status"] == "revoked" and revoked["diagnostic"] is None
    assert not revoked["online"] and not revoked["provisionable"]
    for method in [
        lambda: service.report(token(value), payload),
        lambda: service.session(token(value)),
        lambda: service.pair(value),
    ]:
        with pytest.raises(HostLinkError):
            method()


@pytest.mark.parametrize(
    "changes",
    [
        {"sequence": True},
        {"expected_revision": -1},
        {"sequence": 999999999999},
        {"driver": "shell"},
        {"probe": {"platform": "true", "module": True, "service": True}},
        {"probe": {"platform": True, "kvm": True, "service": True}},
        {"probe": {"platform": True, "module": True, "service": True, "secret": True}},
        {"hostname": "private-host"},
        {"provisionable": True},
    ],
)
def test_diagnostic_contract_rejects_unknown_large_or_coerced_parameters(changes):
    dto = {"host_id": str(uuid4()), "last_report_sequence": 0, "report_revision": 0}
    with pytest.raises(ValidationError):
        HostReportInput.model_validate(report(dto, **changes))


def test_confirm_requires_explicit_fingerprint_and_integer_revision():
    for changes in [{"expected_revision": True}, {"fingerprint": "x" * 19}, {"allow_vm": True}]:
        with pytest.raises(ValidationError):
            HostConfirmInput.model_validate(
                {
                    "expected_revision": 1,
                    "client_request_id": uuid4(),
                    "fingerprint": "0000-0000-0000-0000",
                }
                | changes
            )


def test_pair_validation_does_not_echo_secret_and_domain_is_not_generic_token():
    credential = "session-sensitive" + "x" * 43
    with pytest.raises(ValidationError) as error:
        HostPairInput(
            installation_id=uuid4(),
            invite_token="bi_" + "a" * 43,
            host_id=uuid4(),
            host_credential=credential,
            client_request_id=uuid4(),
        )
    assert credential not in str(error.value)


def test_environment_observes_only_fresh_confirmed_host_and_never_claims_jobs(setup):
    database, clock, service = setup
    env = EnvironmentService(database, host_service=service)
    value, dto, _ = pair(service)
    assert env.host_status()["driver"] == "none"
    dto, _ = confirm(service, dto)
    assert env.host_status()["driver"] == "none"
    service.report(
        token(value), report(dto, probe={"platform": True, "module": True, "service": True})
    )
    assert env.host_status() == {
        "driver": "hyperv",
        "probe": {"platform": True, "module": True, "service": True},
        "status": "guest_bridge_required",
        "provisionable": False,
    }
    with StateStore(database).transaction() as unit:
        agent = unit.agents.create(Agent(name="Computador preparado"))
    requested = env.request(agent.id, {"name": "Pedido", "client_request_id": uuid4()})
    assert requested.status == "awaiting_host" and not env.view(requested)["usable"]
    clock[0] += 60
    assert env.host_status()["driver"] == "none"
    with StateStore(database).transaction(write=False) as unit:
        assert unit.host_jobs.for_environment(requested.id).status == "awaiting_host"


def test_report_and_confirm_failure_roll_back_mutation_with_event(setup, monkeypatch):
    _, _, service = setup
    value, dto, _ = pair(service)
    original = service._event
    monkeypatch.setattr(
        service, "_event", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("falha controlada"))
    )
    with pytest.raises(RuntimeError):
        confirm(service, dto)
    assert service.get(value.host_id)["status"] == "pending"
    monkeypatch.setattr(service, "_event", original)
    dto, _ = confirm(service, dto)
    monkeypatch.setattr(
        service, "_event", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("falha controlada"))
    )
    with pytest.raises(RuntimeError):
        service.report(token(value), report(dto))
    assert service.get(value.host_id)["last_report_sequence"] == 0


def test_missing_host_pagination_and_revoked_database_terminal_guard(setup):
    database, _, service = setup
    with pytest.raises(NotFoundError):
        service.get(uuid4())
    value, dto, _ = pair(service)
    service.revoke(
        value.host_id, {"expected_revision": dto["revision"], "client_request_id": uuid4()}
    )
    assert len(service.list(limit=1, offset=0)) == 1
    assert service.list(limit=1, offset=1) == []
    with database.transaction() as connection:
        with pytest.raises(apsw.ConstraintError):
            connection.execute(
                "UPDATE host_links SET status='pending',revoked_at=NULL WHERE host_id=?",
                (str(value.host_id),),
            )


def test_upgrade_five_to_six_backs_up_without_touching_environment_jobs(tmp_path, monkeypatch):
    directory = tmp_path / "migrations"
    directory.mkdir()
    original = resources.files("bees_core.storage.migrations")
    for file in original.iterdir():
        if file.name.endswith(".sql") and not file.name.startswith("0006"):
            (directory / file.name).write_bytes(file.read_bytes())
    monkeypatch.setattr(database_module.resources, "files", lambda _: directory)
    database = Database(tmp_path / "upgrade.sqlite3")
    database.initialize()
    with StateStore(database).transaction() as unit:
        agent = unit.agents.create(Agent(name="Preservado"))
    # Schema5 não possui HostService; cadastre o pedido pelo registro canônico.
    from bees_core.models import Environment, HostJob

    with StateStore(database).transaction() as unit:
        environment = unit.environments.create(
            Environment(
                agent_id=agent.id, name="Original", client_request_id=uuid4(), request_hash="a" * 64
            )
        )
        job = unit.host_jobs.create(HostJob(environment_id=environment.id, correlation_id=uuid4()))
    (directory / "0006_hosts.sql").write_bytes((original / "0006_hosts.sql").read_bytes())
    database.initialize()
    assert database.schema_version() == 6
    backup = list(tmp_path.glob("*.backup-*.sqlite3"))
    assert len(backup) == 1 and Database(backup[0]).schema_version() == 5
    with StateStore(database).transaction(write=False) as unit:
        assert unit.environments.get(environment.id) == environment
        assert unit.host_jobs.get(job.id) == job
    assert HostService(database).list() == []
