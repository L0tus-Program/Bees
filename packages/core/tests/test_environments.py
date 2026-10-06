"""Pedidos duráveis, ausência de VM falsa e journal conservador do host."""

import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from importlib import resources
from uuid import uuid4

import apsw
import pytest
from pydantic import ValidationError

from bees_core import environments
from bees_core.environments import (
    EnvironmentCancelInput,
    EnvironmentCreateInput,
    EnvironmentError,
    EnvironmentService,
    HostConfiguration,
)
from bees_core.models import Agent, HostJob, utc_now
from bees_core.storage import database as database_module
from bees_core.storage.database import Database
from bees_core.storage.store import (
    IntegrityError,
    InvalidTransition,
    NotFoundError,
    RevisionConflict,
    StateStore,
)


@pytest.fixture
def setup(tmp_path):
    database = Database(tmp_path / "environments.sqlite3")
    database.initialize()
    store = StateStore(database)
    with store.transaction() as unit:
        agent = unit.agents.create(Agent(name="Dona do computador"))
        other = unit.agents.create(Agent(name="Outra abelha"))
    return database, store, EnvironmentService(database), agent, other


def request(**changes):
    return {"name": "Computador de teste", "client_request_id": uuid4()} | changes


def test_request_survives_restart_and_replay_without_claim_or_effect(setup, monkeypatch):
    database, store, service, agent, _ = setup
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: pytest.fail("Pedido iniciou processo"))
    value = request()
    result = service.request(agent.id, value)
    assert result.status == "awaiting_host" and result.reason_code == "host_setup_required"
    restarted = EnvironmentService(Database(database.path))
    assert restarted.request(agent.id, value) == result
    with store.transaction(write=False) as unit:
        operation = unit.host_jobs.for_environment(result.id)
        assert operation.status == "awaiting_host" and operation.owner_id is None
        assert len(unit.environments.list(agent.id)) == 1
        assert len(unit.events.list(entity_id=operation.id)) == 1
    dto = restarted.view(result)
    assert dto["operation_id"] == str(operation.id) and dto["usable"] is False
    assert dto["operation_status"] == "awaiting_host"
    assert not {"metadata", "request_hash", "client_request_id"} & dto.keys()
    assert "ready" not in json.dumps(dto)


def test_concurrent_same_request_creates_exactly_one_environment_and_operation(setup):
    _, store, service, agent, _ = setup
    value = request()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: service.request(agent.id, value), range(2)))
    assert results[0] == results[1]
    with store.transaction(write=False) as unit:
        assert len(unit.environments.list(agent.id)) == 1
        assert len(unit.events.list(entity_type="host_job")) == 1


def test_changed_payload_same_uuid_conflicts_without_new_job(setup):
    _, store, service, agent, _ = setup
    value = request()
    result = service.request(agent.id, value)
    with pytest.raises(EnvironmentError, match="outros dados"):
        service.request(agent.id, value | {"cpu_count": 3})
    with store.transaction(write=False) as unit:
        assert unit.environments.get(result.id) == result
        assert len(unit.events.list(entity_type="host_job")) == 1


@pytest.mark.parametrize(
    "changes",
    [
        {"cpu_count": True},
        {"cpu_count": 8},
        {"memory_mib": 1024},
        {"disk_gib": 101},
        {"driver": "hyperv"},
        {"image_url": "https://fixture.invalid/image"},
        {"name": " espaço"},
        {"name": "controle\n"},
        {"template_id": "shell"},
        {"client_request_id": "arbitrary"},
    ],
)
def test_untrusted_request_cannot_change_host_or_escape_resource_limits(changes):
    with pytest.raises(ValidationError):
        EnvironmentCreateInput.model_validate(request(**changes))


def test_only_active_agent_can_create_but_original_replay_is_preserved(setup):
    _, store, service, agent, _ = setup
    value = request()
    result = service.request(agent.id, value)
    with store.transaction() as unit:
        unit.agents.update(agent.model_copy(update={"status": "paused"}), agent.revision)
    assert service.request(agent.id, value).id == result.id
    with pytest.raises(EnvironmentError) as rejected:
        service.request(agent.id, request())
    assert rejected.value.code == "environment_agent_inactive"


def test_foreign_agent_and_missing_agent_never_observe_or_cancel_record(setup):
    _, _, service, agent, other = setup
    result = service.request(agent.id, request())
    assert service.list(other.id) == []
    for method in [
        lambda: service.get(other.id, result.id),
        lambda: service.cancel(
            other.id, result.id, {"expected_revision": 1, "client_request_id": uuid4()}
        ),
        lambda: service.request(uuid4(), request()),
    ]:
        with pytest.raises(NotFoundError):
            method()


def test_cancel_cas_replay_and_ledger_are_atomic(setup):
    _, store, service, agent, _ = setup
    result = service.request(agent.id, request())
    command = {"expected_revision": 1, "client_request_id": uuid4()}
    with pytest.raises(RevisionConflict):
        service.cancel(agent.id, result.id, command | {"expected_revision": 2})
    cancelled = service.cancel(agent.id, result.id, command)
    assert cancelled.status == "cancelled" and cancelled.revision == 2
    assert service.cancel(agent.id, result.id, command) == cancelled
    with pytest.raises(EnvironmentError):
        service.cancel(agent.id, result.id, command | {"expected_revision": 2})
    with store.transaction(write=False) as unit:
        assert unit.host_jobs.for_environment(result.id).status == "cancelled"
        events = unit.events.list(entity_id=result.id)
        assert [e.payload["status"] for e in events] == ["awaiting_host", "cancelled"]
        assert events[1].correlation_id == str(command["client_request_id"])
        assert events[0].payload["cpu_count"] == 2
    with pytest.raises(ValidationError):
        EnvironmentCancelInput(expected_revision=True, client_request_id=uuid4())


def test_cancel_failure_rolls_back_job_update(setup, monkeypatch):
    _, store, service, agent, _ = setup
    result = service.request(agent.id, request())
    from bees_core.storage.store import Environments

    monkeypatch.setattr(
        Environments,
        "update",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("falha controlada")),
    )
    with pytest.raises(RuntimeError):
        service.cancel(agent.id, result.id, {"expected_revision": 1, "client_request_id": uuid4()})
    with store.transaction(write=False) as unit:
        assert unit.host_jobs.for_environment(result.id).status == "awaiting_host"
        assert unit.environments.get(result.id).revision == 1


def seed_future_dispatch(setup):
    """Só simula o journal de coordenador futuro, nunca disponibilidade real."""
    _, store, service, agent, _ = setup
    result = service.request(agent.id, request())
    owner = uuid4()
    with store.transaction(actor="trusted_host", source="test") as unit:
        job = unit.host_jobs.for_environment(result.id)
        unit.environments.update(
            result.model_copy(update={"status": "provisioning"}), result.revision
        )
        job = unit.host_jobs.update(
            job.model_copy(
                update={
                    "status": "dispatch_started",
                    "owner_id": owner,
                    "dispatched_at": utc_now(),
                }
            ),
            job.revision,
        )
    return result, job, owner


def test_recovery_requires_interrupted_owner_and_never_requeues_unknown(setup):
    database, store, service, agent, _ = setup
    result, job, owner = seed_future_dispatch(setup)
    proof = uuid4()
    with pytest.raises(EnvironmentError):
        service.recover_interrupted(
            job.id, expected_revision=job.revision, owner_id=uuid4(), recovery_evidence=proof
        )
    recovered = service.recover_interrupted(
        job.id, expected_revision=job.revision, owner_id=owner, recovery_evidence=proof
    )
    restarted = EnvironmentService(Database(database.path))
    assert (
        restarted.recover_interrupted(
            job.id, expected_revision=job.revision, owner_id=owner, recovery_evidence=proof
        )
        == recovered
    )
    assert restarted.get(agent.id, result.id).status == "outcome_unknown"
    with pytest.raises(EnvironmentError):
        restarted.cancel(
            agent.id, result.id, {"expected_revision": 3, "client_request_id": uuid4()}
        )
    with store.transaction() as unit:
        with pytest.raises(InvalidTransition):
            unit.host_jobs.update(
                recovered.model_copy(
                    update={
                        "status": "awaiting_host",
                        "owner_id": None,
                        "dispatched_at": None,
                        "recovery_evidence": None,
                    }
                ),
                recovered.revision,
            )
        assert unit.events.list(entity_id=recovered.id)[-1].payload["recovery_evidence"] == str(
            proof
        )


def test_host_job_evidence_and_immutable_request_match_sql_guards(setup):
    _, store, service, agent, _ = setup
    result = service.request(agent.id, request())
    for changes in [
        {"owner_id": uuid4()},
        {"dispatched_at": utc_now()},
        {"recovery_evidence": uuid4()},
    ]:
        with pytest.raises(ValidationError):
            HostJob(environment_id=result.id, correlation_id=uuid4(), **changes)
    with store.transaction() as unit:
        with pytest.raises(IntegrityError):
            unit.environments.update(
                result.model_copy(update={"cpu_count": 3, "status": "cancelled"}), 1
            )
    with service.store.database.transaction() as connection:
        with pytest.raises(apsw.ConstraintError):
            connection.execute("UPDATE environments SET cpu_count=3 WHERE id=?", (str(result.id),))


def test_catalog_is_target_and_no_bridge_can_claim_availability(setup, monkeypatch):
    _, _, service, _, _ = setup
    catalog = service.catalog()
    assert catalog["templates"][0]["applications"] == ["chromium", "libreoffice"]
    assert catalog["templates"][0]["status"] == "planned"
    assert catalog["templates"][0]["provisionable"] is False
    assert catalog["host"] == {
        "driver": "none",
        "status": "host_setup_required",
        "probe": None,
        "provisionable": False,
    }
    with pytest.raises(ValidationError):
        HostConfiguration(guest_bridge_ready=True)
    monkeypatch.setattr(environments.shutil, "which", lambda _: None)
    result = environments.preflight_host(HostConfiguration(driver="hyperv"))
    assert result["status"] == "driver_unavailable" and result["provisionable"] is False


@pytest.mark.parametrize(
    "output",
    [None, "segredo remoto", '{"module":"yes","service":true}', '{"module":true,"service":true}'],
)
def test_hyperv_probe_sanitizes_and_never_provisions(monkeypatch, output):
    monkeypatch.setattr(environments.os, "name", "nt")
    monkeypatch.setattr(environments.shutil, "which", lambda _: "powershell.exe")
    calls = []

    def probe(argv):
        calls.append(argv)
        return output

    monkeypatch.setattr(environments, "_read_command", probe)
    result = environments.preflight_host(HostConfiguration(driver="hyperv"))
    assert result["provisionable"] is False
    assert "segredo" not in json.dumps(result)
    assert result["status"] == (
        "guest_bridge_required"
        if output == '{"module":true,"service":true}'
        else "driver_unavailable"
    )
    assert "New-VM" not in calls[0][-1] and "Enable-" not in calls[0][-1]


def test_libvirt_probe_static_local_uri_and_read_only_command(monkeypatch):
    monkeypatch.setattr(environments.os, "name", "posix")
    monkeypatch.setattr(environments.os, "access", lambda *a: True)
    monkeypatch.setattr(environments.shutil, "which", lambda _: "/usr/bin/virsh")
    calls = []
    monkeypatch.setattr(
        environments, "_read_command", lambda argv: calls.append(argv) or "qemu:///system\n"
    )
    result = environments.preflight_host(HostConfiguration(driver="libvirt"))
    assert result["status"] == "guest_bridge_required" and not result["provisionable"]
    assert calls == [["/usr/bin/virsh", "--connect", "qemu:///system", "uri"]]


def test_read_only_probe_timeout_missing_binary_and_large_output_are_bounded():
    assert (
        environments._read_command(
            [sys.executable, "-c", "import time;time.sleep(2)"], timeout=0.01
        )
        is None
    )
    assert environments._read_command(["bees-nonexistent-probe-7b38"]) is None
    assert environments._read_command([sys.executable, "-c", "print('x'*9000)"]) is None
    assert environments._read_command([sys.executable, "-c", "print('ok')"]).strip() == "ok"


def test_upgrade_four_to_five_backs_up_and_preserves_state(tmp_path, monkeypatch):
    directory = tmp_path / "migrations"
    directory.mkdir()
    original = resources.files("bees_core.storage.migrations")
    for file in original.iterdir():
        if file.name.endswith(".sql") and file.name.startswith(("0001", "0002", "0003", "0004")):
            (directory / file.name).write_bytes(file.read_bytes())
    monkeypatch.setattr(database_module.resources, "files", lambda _: directory)
    database = Database(tmp_path / "upgrade.sqlite3")
    database.initialize()
    with StateStore(database).transaction() as unit:
        agent = unit.agents.create(Agent(name="Estado preservado"))
    (directory / "0005_environments.sql").write_bytes(
        (original / "0005_environments.sql").read_bytes()
    )
    database.initialize()
    assert database.schema_version() == 5
    backups = list(tmp_path.glob("*.backup-*.sqlite3"))
    assert len(backups) == 1
    assert Database(backups[0]).schema_version() == 4
    with StateStore(database).transaction(write=False) as unit:
        assert unit.agents.get(agent.id).name == "Estado preservado"
    with database.transaction(write=False) as connection:
        assert connection.execute("PRAGMA integrity_check").get == "ok"
        assert not connection.execute("PRAGMA foreign_key_check").fetchall()


def test_real_process_crash_preserves_ambiguous_dispatch_without_retry(setup):
    database, store, service, agent, _ = setup
    result = service.request(agent.id, request())
    code = """
import os,sys
from uuid import UUID,uuid4
from bees_core.models import utc_now
from bees_core.storage.database import Database
from bees_core.storage.store import StateStore
store=StateStore(Database(sys.argv[1]))
with store.transaction(actor="trusted_host",source="crash_test") as unit:
 env=unit.environments.get(UUID(sys.argv[2]))
 job=unit.host_jobs.for_environment(env.id)
 unit.environments.update(env.model_copy(update={"status":"provisioning"}),env.revision)
 unit.host_jobs.update(job.model_copy(update={"status":"dispatch_started","owner_id":uuid4(),"dispatched_at":utc_now()}),job.revision)
os._exit(19)
"""
    process = subprocess.run(
        [sys.executable, "-c", code, str(database.path), str(result.id)],
        check=False,
        timeout=10,
        capture_output=True,
    )
    assert process.returncode == 19
    with store.transaction(write=False) as unit:
        job = unit.host_jobs.for_environment(result.id)
    service.recover_interrupted(
        job.id, expected_revision=job.revision, owner_id=job.owner_id, recovery_evidence=uuid4()
    )
    assert service.get(agent.id, result.id).status == "outcome_unknown"
    assert service.view(result)["usable"] is False
