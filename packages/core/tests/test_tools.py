"""Contratos locais, revogação, CAS e journal sem repetir efeitos."""

import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from uuid import uuid4

import apsw
import pytest
from pydantic import ValidationError

from bees_core.models import Agent, Run, Task, ToolGrant, utc_now
from bees_core.policies import PolicyService
from bees_core.storage.database import Database
from bees_core.storage.store import IntegrityError, NotFoundError, RevisionConflict, StateStore
from bees_core.tools import ToolError, ToolService


@pytest.fixture
def setup(tmp_path):
    database = Database(tmp_path / "bees.sqlite3")
    database.initialize()
    store = StateStore(database)
    with store.transaction() as unit:
        agent = unit.agents.create(Agent(name="Teste de contratos"))
        other = unit.agents.create(Agent(name="Outra abelha"))
        task = unit.tasks.create(Task(agent_id=agent.id, title="Ferramenta", objective="Texto"))
        unit.runs.create(Run(task_id=task.id))
        claim = unit.execution.claim_next(uuid4(), now=utc_now(), ttl_seconds=300)
        task, run = claim.task, claim.run
    service = ToolService(database)
    # Claim real obtido pela fila, compartilhado pelos helpers deste teste.
    service.test_claim = claim
    plugin = service.install({"manifest": service.catalog()[0], "client_request_id": uuid4()})
    plugin = service.update_plugin(plugin.id, {"enabled": True, "expected_revision": 1})
    grant = service.set_grant(
        agent.id, plugin.id, "text.normalize", {"enabled": True, "expected_revision": 0}
    )
    return database, store, service, agent, other, task, run, plugin, grant


def invocation(setup, **changes):
    *_, plugin, _ = setup
    return {
        "plugin_id": plugin.id,
        "tool_name": "text.normalize",
        "version": "1",
        "arguments": {"text": "  Olá Bees  ", "operation": "trim"},
        "client_request_id": uuid4(),
    } | changes


def prepare(setup, value=None):
    _, _, service, agent, _, _, run, *_ = setup
    value = invocation(setup) if value is None else value
    return service.prepare(agent.id, run.id, value, claim=service.test_claim), value


def execute(setup, action, value):
    _, _, service, agent, *_ = setup
    return service.execute(
        agent.id,
        action.id,
        value["arguments"],
        expected_revision=action.revision,
        claim=service.test_claim,
    )


@pytest.mark.parametrize(
    "operation,expected",
    [("trim", "Olá Bees"), ("upper", "  OLÁ BEES  "), ("lower", "  olá bees  ")],
)
def test_builtin_real_output_and_replay_no_new_effect(setup, monkeypatch, operation, expected):
    from bees_core import tools

    action, value = prepare(
        setup, invocation(setup, arguments={"text": "  Olá Bees  ", "operation": operation})
    )
    result = execute(setup, action, value)
    assert result.status == "confirmed" and result.result == {"text": expected}
    monkeypatch.setattr(
        tools, "_execute_builtin", lambda args: pytest.fail("Replay executou novamente")
    )
    assert execute(setup, action, value) == result
    _, _, service, agent, _, _, run, *_ = setup
    assert service.prepare(agent.id, run.id, value, claim=service.test_claim) == result
    assert ToolService(setup[0]).get_invocation(agent.id, action.id) == result


def test_content_excluded_from_intent_snapshot_policy_audit_and_errors(setup, monkeypatch):
    from bees_core import tools

    secret = "segredo-de-teste-nunca-no-journal"
    action, value = prepare(
        setup, invocation(setup, arguments={"text": secret, "operation": "trim"})
    )
    assert secret not in action.model_dump_json()

    def fail(args):
        raise RuntimeError(secret)

    monkeypatch.setattr(tools, "_execute_builtin", fail)
    result = execute(setup, action, value)
    assert result.status == "outcome_unknown"
    assert secret not in result.model_dump_json()
    with setup[1].transaction(write=False) as unit:
        assert secret not in json.dumps(
            [event.model_dump(mode="json") for event in unit.events.list()], ensure_ascii=False
        )
    monkeypatch.setattr(
        tools, "_execute_builtin", lambda args: pytest.fail("Unknown repetiu efeito")
    )
    assert execute(setup, action, value).status == "outcome_unknown"


@pytest.mark.parametrize(
    "change",
    [
        {"shell": "whoami"},
        {"environment_id": "personal"},
        {"identity": "admin"},
        {"resource": "/etc/passwd"},
        {"text": 1},
        {"text": "x" * 4097},
        {"text": "\x00"},
        {"operation": "network"},
        {"text": "😀" * 3000},
    ],
)
def test_invalid_arguments_authority_and_limits_are_rejected(setup, change):
    value = invocation(setup)
    value["arguments"].update(change)
    with pytest.raises(ToolError, match="Entrada"):
        prepare(setup, value)
    with setup[1].transaction(write=False) as unit:
        assert unit.actions.list() == []


@pytest.mark.parametrize(
    "change",
    [
        {"manifest_version": True},
        {"manifest_version": 1.0},
        {"manifest_version": 2},
        {"url": "https://malicious.test/plugin.py"},
        {"code": "import os"},
        {"tools": [{"tool_name": "shell", "version": "1", "executor": "python:os.system"}]},
        {
            "tools": [
                {
                    "tool_name": "text.normalize",
                    "version": "2",
                    "executor": "builtin:text.normalize@1",
                }
            ]
        },
        {"tools": []},
        {
            "tools": [
                {
                    "tool_name": "text.normalize",
                    "version": "1",
                    "executor": "builtin:text.normalize@1",
                    "default_effect": "allow",
                }
            ]
        },
    ],
)
def test_manifest_cannot_install_arbitrary_code_or_override_contract(setup, change):
    service = setup[2]
    with pytest.raises(ToolError, match="Manifesto"):
        service.install({"manifest": service.catalog()[0] | change, "client_request_id": uuid4()})


def test_install_replay_default_disabled_immutable_and_catalog_copy(setup):
    _, store, service, *_ = setup
    value = {"manifest": service.catalog()[0], "client_request_id": uuid4()}
    plugin = service.install(value)
    assert not plugin.enabled and plugin.revision == 1
    assert service.install(value) == plugin
    with pytest.raises(ToolError, match="Identificador"):
        service.install(value | {"manifest": value["manifest"] | {"name": "Outro nome"}})
    with store.transaction() as unit:
        with pytest.raises(IntegrityError):
            unit.plugins.update(
                plugin.model_copy(update={"manifest": plugin.manifest | {"name": "Editado"}}), 1
            )
    catalog = service.catalog()
    catalog[0]["tools"][0]["executor"] = "evil"
    assert service.catalog()[0]["tools"][0]["executor"] == "builtin:text.normalize@1"


@pytest.mark.parametrize(
    "kind",
    [
        "disabled",
        "revoked",
        "grant_edited",
        "plugin_edited",
        "agent_edited",
        "paused",
        "context_changed",
    ],
)
def test_revalidate_current_availability_and_revisions_before_effect(setup, monkeypatch, kind):
    from bees_core import tools

    _, store, service, agent, _, task, _, plugin, grant = setup
    action, value = prepare(setup)
    if kind == "disabled":
        service.update_plugin(plugin.id, {"enabled": False, "expected_revision": plugin.revision})
    elif kind in ("revoked", "grant_edited"):
        service.set_grant(
            agent.id,
            plugin.id,
            "text.normalize",
            {"enabled": kind != "revoked", "expected_revision": grant.revision},
        )
    elif kind == "plugin_edited":
        service.update_plugin(plugin.id, {"enabled": True, "expected_revision": plugin.revision})
    else:
        with store.transaction() as unit:
            if kind == "agent_edited":
                unit.agents.update(
                    agent.model_copy(update={"instructions": "Novas instruções"}), agent.revision
                )
            elif kind == "paused":
                unit.tasks.update(
                    task.model_copy(update={"desired_state": "paused"}), task.revision
                )
            else:
                unit.tasks.update(task.model_copy(update={"control_revision": 1}), task.revision)
    monkeypatch.setattr(
        tools, "_execute_builtin", lambda args: pytest.fail("Revogação iniciou efeito")
    )
    with pytest.raises(ToolError):
        execute(setup, action, value)
    assert service.get_invocation(agent.id, action.id).status == "ready"


@pytest.mark.parametrize("effect", ["deny", "ask"])
def test_current_policy_overrides_grant_between_prepare_and_dispatch(setup, monkeypatch, effect):
    from bees_core import tools

    action, value = prepare(setup)
    policy = PolicyService(setup[0]).create(
        setup[3].id,
        {
            "name": "Revisão",
            "effect": effect,
            "scope": {"tool_name": "text.normalize", "action": "transform"},
        },
    )
    monkeypatch.setattr(
        tools, "_execute_builtin", lambda args: pytest.fail("Política iniciou efeito")
    )
    with pytest.raises(ToolError) as exc:
        execute(setup, action, value)
    assert exc.value.code == ("tool_policy_ask" if effect == "ask" else "tool_policy_denied")
    with pytest.raises(ToolError):
        prepare(setup)
    assert policy.status == "active"


def test_hash_mismatch_rejects_mutated_arguments_and_snapshot(setup):
    action, value = prepare(setup)
    service, agent = setup[2:4]
    with pytest.raises(ToolError, match="Parâmetros"):
        service.execute(
            agent.id,
            action.id,
            value["arguments"] | {"text": "Alterado"},
            expected_revision=action.revision,
            claim=service.test_claim,
        )
    with setup[1].transaction() as unit:
        changed = unit.actions.update(
            action.model_copy(update={"metadata": action.metadata | {"intent_hash": "a" * 64}}),
            action.revision,
        )
    with pytest.raises(ToolError, match="Autoridade"):
        execute(setup, changed, value)


def test_owner_separation_grants_disabled_and_unknown_tool(setup):
    _, _, service, agent, other, _, run, plugin, _ = setup
    with pytest.raises(NotFoundError):
        service.prepare(other.id, run.id, invocation(setup), claim=service.test_claim)
    action, _ = prepare(setup)
    with pytest.raises(NotFoundError):
        service.get_invocation(other.id, action.id)
    with pytest.raises(ToolError, match="Ferramenta"):
        service.set_grant(agent.id, plugin.id, "shell", {"enabled": True, "expected_revision": 0})
    rows = service.list_tools(other.id)
    assert rows[0]["grant_revision"] == 0 and not rows[0]["available"]


def test_grant_integrity_and_cas_concurrent_writers(setup):
    _, store, service, agent, _, _, _, plugin, grant = setup
    with store.transaction() as unit:
        with pytest.raises(IntegrityError):
            unit.tool_grants.create(
                ToolGrant(agent_id=agent.id, plugin_id=plugin.id, tool_name="shell")
            )

    def update(_):
        try:
            return service.set_grant(
                agent.id,
                plugin.id,
                "text.normalize",
                {"enabled": False, "expected_revision": grant.revision},
            )
        except RevisionConflict:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(update, range(2)))
    assert sum(result is not None for result in results) == 1
    with pytest.raises(RevisionConflict):
        service.update_plugin(plugin.id, {"enabled": False, "expected_revision": 1})
    with pytest.raises(ValidationError):
        service.update_plugin(plugin.id, {"enabled": False, "expected_revision": True})


def test_events_preserve_safe_grant_scope_and_disabled_history_after_restart(setup):
    db, store, service, agent, _, _, _, plugin, grant = setup
    service.set_grant(
        agent.id,
        plugin.id,
        "text.normalize",
        {"enabled": False, "expected_revision": grant.revision},
    )
    service.update_plugin(plugin.id, {"enabled": False, "expected_revision": plugin.revision})
    rows = ToolService(db).list_tools(agent.id)
    assert not rows[0]["available"] and not rows[0]["granted"] and rows[0]["grant_revision"] == 2
    with store.transaction(write=False) as unit:
        events = unit.events.list()
    grant_events = [event for event in events if event.entity_type == "tool_grant"]
    assert len(grant_events) == 2
    assert all(event.actor == "user" for event in grant_events)
    assert grant_events[-1].payload == {
        "revision": 2,
        "agent_id": str(agent.id),
        "plugin_id": str(plugin.id),
        "tool_name": "text.normalize",
        "enabled": False,
        "previous_enabled": True,
    }


def test_dispatch_started_process_crash_recovers_unknown_without_retry(setup):
    db, _, service, agent, *_ = setup
    action, value = prepare(setup)
    script = """
import os, json
from uuid import UUID
from bees_core.storage.database import Database
from bees_core import tools
from bees_core.models import ExecutionClaim
tools._execute_builtin = lambda args: os._exit(23)
tools.ToolService(Database(os.environ["BEES_TEST_DB"])).execute(
    UUID(os.environ["BEES_TEST_AGENT"]), UUID(os.environ["BEES_TEST_ACTION"]),
    json.loads(os.environ["BEES_TEST_INPUT"]),
    expected_revision=int(os.environ["BEES_TEST_REVISION"]),
    claim=ExecutionClaim.model_validate_json(os.environ["BEES_TEST_CLAIM"]))
"""
    env = os.environ | {
        "BEES_TEST_DB": str(db.path),
        "BEES_TEST_AGENT": str(agent.id),
        "BEES_TEST_ACTION": str(action.id),
        "BEES_TEST_INPUT": json.dumps(value["arguments"]),
        "BEES_TEST_REVISION": str(action.revision),
        "BEES_TEST_CLAIM": service.test_claim.model_dump_json(),
    }
    child = subprocess.run([sys.executable, "-c", script], env=env, capture_output=True, timeout=15)
    assert child.returncode == 23, child.stderr.decode()
    interrupted = service.get_invocation(agent.id, action.id)
    assert interrupted.status == "dispatch_started"
    assert execute(setup, action, value).status == "dispatch_started"
    with setup[1].transaction() as unit:
        unit.execution.release(service.test_claim)
    recovered = service.mark_unknown(
        agent.id,
        action.id,
        expected_revision=interrupted.revision,
        evidence_ref="process:terminated:23",
        owner_id=service.test_claim.owner_id,
    )
    assert recovered.status == "outcome_unknown"
    assert execute(setup, action, value) == recovered


@pytest.mark.parametrize("kind", ["old_run", "task_paused"])
def test_stale_run_or_task_state_cannot_authorize_dispatch(setup, kind):
    _, store, _, _, _, task, *_ = setup
    action, value = prepare(setup)
    with store.transaction() as unit:
        if kind == "old_run":
            unit.runs.create(Run(task_id=task.id, status="running"))
        else:
            unit.tasks.update(task.model_copy(update={"status": "paused"}), task.revision)
    with pytest.raises(ToolError, match="Execução"):
        execute(setup, action, value)


def test_concurrent_dispatch_started_is_read_only_no_second_effect(setup, monkeypatch):
    from bees_core import tools

    started, release = Event(), Event()
    calls = []

    def delayed(arguments):
        calls.append(arguments.operation)
        started.set()
        assert release.wait(10)
        return {"text": "Resultado controlado"}

    monkeypatch.setattr(tools, "_execute_builtin", delayed)
    action, value = prepare(setup)
    with ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(execute, setup, action, value)
        assert started.wait(10)
        try:
            assert execute(setup, action, value).status == "dispatch_started"
            assert calls == ["trim"]
        finally:
            release.set()
        assert first.result(timeout=10).status == "confirmed"


def test_output_validation_and_recovery_cas_fail_closed(setup, monkeypatch):
    from bees_core import tools

    monkeypatch.setattr(tools, "_execute_builtin", lambda args: {"text": 5, "extra": "wrong"})
    next_action, _ = prepare(setup)
    with setup[1].transaction() as unit:
        dispatched = unit.actions.update(
            next_action.model_copy(update={"status": "dispatch_started"}), next_action.revision
        )
    service, agent = setup[2:4]
    with setup[1].transaction() as unit:
        unit.execution.release(service.test_claim)
    with pytest.raises(ValueError):
        service.mark_unknown(
            agent.id,
            dispatched.id,
            expected_revision=dispatched.revision,
            evidence_ref="",
            owner_id=service.test_claim.owner_id,
        )
    with pytest.raises(RevisionConflict):
        service.mark_unknown(
            agent.id,
            dispatched.id,
            expected_revision=dispatched.revision - 1,
            evidence_ref="process:stopped",
            owner_id=service.test_claim.owner_id,
        )
    with pytest.raises(ValueError):
        service.mark_unknown(
            agent.id,
            dispatched.id,
            expected_revision=True,
            evidence_ref="process:stopped",
            owner_id=service.test_claim.owner_id,
        )
    assert service.get_invocation(agent.id, dispatched.id).status == "dispatch_started"


def test_upgrade_three_to_four_backup_preserves_actions_and_grants_integrity(tmp_path, monkeypatch):
    from importlib import resources

    from bees_core.storage import database as module

    path = tmp_path / "migrations"
    path.mkdir()
    original = resources.files("bees_core.storage.migrations")
    for file in original.iterdir():
        if file.name.endswith(".sql") and file.name.startswith(("0001", "0002", "0003")):
            (path / file.name).write_bytes(file.read_bytes())
    monkeypatch.setattr(module.resources, "files", lambda _: path)
    db = Database(tmp_path / "upgrade.sqlite3")
    db.initialize()
    with StateStore(db).transaction() as unit:
        agent = unit.agents.create(Agent(name="Existente"))
    (path / "0004_tools.sql").write_bytes((original / "0004_tools.sql").read_bytes())
    db.initialize()
    assert db.schema_version() == 4
    backups = list(tmp_path.glob("*.backup-*.sqlite3"))
    assert len(backups) == 1
    connection = apsw.Connection(str(backups[0]))
    try:
        assert connection.execute("SELECT max(version) FROM schema_migrations").get == 3
        assert (
            connection.execute("SELECT name FROM agents WHERE id=?", (str(agent.id),)).get
            == "Existente"
        )
        assert connection.execute("PRAGMA integrity_check").get == "ok"
    finally:
        connection.close()
    with db.transaction(write=False) as connection:
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
