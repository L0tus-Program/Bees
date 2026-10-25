"""Pareamento, computadores, concessões pessoais e fila de comandos dos executores."""

from datetime import timedelta
from uuid import uuid4

import pytest
from test_tasks_execution import fixture_state

from bees_core.agent_tools.catalog import spec
from bees_core.executors import (
    CommandQueue,
    ExecutorError,
    ExecutorService,
    require_online,
    resolve_target,
)
from bees_core.models import utc_now
from bees_core.storage.store import RevisionConflict
from bees_core.tools import ToolError


class Clock:
    def __init__(self):
        self.now = utc_now()

    def __call__(self):
        return self.now


@pytest.fixture
def state(tmp_path):
    database, store, agent, _, _ = fixture_state(tmp_path)
    clock = Clock()
    return database, store, agent, ExecutorService(database, clock=clock), clock


def pair(service, kind="computer_runtime"):
    invite = service.invite({"kind": kind, "name": "Executor"})
    paired = service.exchange({"token": invite["token"]})
    service.touch(paired["executor_id"], "conn-1")
    return paired


def test_invite_single_use_expiry_and_hashed_credential(state):
    database, store, _, service, clock = state
    invite = service.invite({"kind": "computer_runtime", "name": "Runtime"})
    paired = service.exchange({"token": invite["token"]})
    assert paired["credential"].startswith("be_")
    with pytest.raises(ExecutorError, match="executor_invite_invalid"):
        service.exchange({"token": invite["token"]})
    late = service.invite({"kind": "personal", "name": "Notebook"})
    clock.now += timedelta(minutes=16)
    with pytest.raises(ExecutorError, match="executor_invite_invalid"):
        service.exchange({"token": late["token"]})
    with store.transaction(write=False) as unit:
        stored = unit.agents._context.connection.execute(
            "SELECT credential_hash FROM executors"
        ).fetchall()
    assert paired["credential"] not in str(stored)
    assert service.authenticate(paired["credential"])["id"] == paired["executor_id"]
    with pytest.raises(ExecutorError):
        service.authenticate("be_errado")


def test_computer_requires_online_runtime_and_is_unique_per_agent(state):
    _, _, agent, service, clock = state
    with pytest.raises(ExecutorError, match="computer_runtime_offline"):
        service.create_computer(agent.id, {"client_request_id": uuid4()})
    paired = pair(service)
    request = uuid4()
    computer = service.create_computer(agent.id, {"client_request_id": request})
    assert computer["status"] == "provisioning"
    assert service.create_computer(agent.id, {"client_request_id": request})["id"] == computer["id"]
    with pytest.raises(ExecutorError, match="computer_exists"):
        service.create_computer(agent.id, {"client_request_id": uuid4()})
    with pytest.raises(ExecutorError, match="executor_protocol_invalid"):
        service.report_computer("outro", computer["id"], "ready")
    ready = service.report_computer(paired["executor_id"], computer["id"], "ready")
    assert ready["status"] == "ready"
    clock.now += timedelta(minutes=2)
    assert service.computers(agent.id)[0]["online"] is False


def test_control_cas_blocks_browser_but_not_shell(state):
    database, store, agent, service, _ = state
    paired = pair(service)
    computer = service.create_computer(agent.id, {"client_request_id": uuid4()})
    service.report_computer(paired["executor_id"], computer["id"], "ready")
    taken = service.set_control(
        agent.id, computer["id"], {"expected_revision": 1, "holder": "human"}
    )
    assert taken["control_holder"] == "human"
    with pytest.raises(RevisionConflict):
        service.set_control(agent.id, computer["id"], {"expected_revision": 1, "holder": "agent"})
    with store.transaction(write=False) as unit:
        target = resolve_target(unit, agent.id, spec("computer.browser.click"))
        with pytest.raises(ToolError) as blocked:
            require_online(unit, agent.id, spec("computer.browser.click"), target)
        assert blocked.value.code == "human_control"
        require_online(unit, agent.id, spec("computer.shell.run"), target)


def test_revocation_expires_queued_commands_and_fails_computers(state):
    database, store, agent, service, clock = state
    paired = pair(service)
    computer = service.create_computer(agent.id, {"client_request_id": uuid4()})
    service.report_computer(paired["executor_id"], computer["id"], "ready")
    queue = CommandQueue(database, clock=clock)
    executor = service.list("computer_runtime")[0]
    service.revoke(executor["id"], {"expected_revision": executor["revision"]})
    assert service.computers(agent.id)[0]["status"] == "failed"
    assert queue.claim(paired["executor_id"], "conn-1") == []
    with pytest.raises(ExecutorError):
        service.authenticate(paired["credential"])


def test_personal_target_requires_explicit_scope(state):
    _, store, agent, service, _ = state
    paired = pair(service, "personal")
    with store.transaction(write=False) as unit, pytest.raises(ToolError):
        resolve_target(unit, agent.id, spec("personal.files.read"))
    service.set_grant(
        paired["executor_id"],
        agent.id,
        {"expected_revision": 0, "enabled": True, "roots": ["Docs"]},
    )
    with store.transaction(write=False) as unit:
        assert resolve_target(unit, agent.id, spec("personal.files.read")).kind == "personal"
        for name in ("personal.shell.run", "personal.files.write", "personal.browser.open"):
            with pytest.raises(ToolError):
                resolve_target(unit, agent.id, spec(name))
    with pytest.raises(RevisionConflict):
        service.set_grant(
            paired["executor_id"], agent.id, {"expected_revision": 0, "enabled": False}
        )
    other = pair(service)
    with pytest.raises(ExecutorError, match="executor_unavailable"):
        service.set_grant(other["executor_id"], agent.id, {"expected_revision": 0, "enabled": True})


def queued(store, executor_id, agent, tool, *, computer_id=None, scope=None, generation=1):
    from bees_core.models import Action, Run, Task

    with store.transaction() as unit:
        task = unit.tasks.create(Task(agent_id=agent.id, title="T", objective="O"))
        run = unit.runs.create(Run(task_id=task.id))
        action = unit.actions.create(Action(run_id=run.id, tool_name=tool))
    command_id = str(action.id)
    payload = {"arguments": {}, "guard": {"generation": generation}}
    if scope is not None:
        payload["scope"] = scope
    now = utc_now().isoformat()
    deadline = (utc_now() + timedelta(minutes=5)).isoformat()
    with store.transaction() as unit:
        unit.agents._context.connection.execute(
            "INSERT INTO executor_commands(id,created_at,updated_at,executor_id,computer_id,"
            "agent_id,tool_name,arguments_json,deadline_at,status) "
            "VALUES(?,?,?,?,?,?,?,?,?,'queued')",
            (
                command_id,
                now,
                now,
                executor_id,
                computer_id,
                str(agent.id),
                tool,
                __import__("json").dumps(payload),
                deadline,
            ),
        )
    return command_id


def status(queue, command_id):
    row = queue.get(command_id)
    return row["status"], row["error_code"]


def test_claim_rechecks_stop_control_and_personal_grant(state):
    from bees_core.safety import SafetyService

    database, store, agent, service, clock = state
    queue = CommandQueue(database, clock=clock)
    runtime = pair(service)
    computer = service.create_computer(agent.id, {"client_request_id": uuid4()})
    service.report_computer(runtime["executor_id"], computer["id"], "ready")
    with store.transaction(write=False) as unit:
        generation = unit.safety.state().generation
    shell = queued(
        store,
        runtime["executor_id"],
        agent,
        "computer.shell.run",
        computer_id=computer["id"],
        generation=generation,
    )
    click = queued(
        store,
        runtime["executor_id"],
        agent,
        "computer.browser.click",
        computer_id=computer["id"],
        generation=generation,
    )
    service.set_control(agent.id, computer["id"], {"expected_revision": 1, "holder": "human"})
    sent = queue.claim(runtime["executor_id"], "conn-1")
    # Controle humano tomado depois do início: clique não sai; shell segue.
    assert [row["id"] for row in sent] == [shell]
    assert "guard" not in sent[0]["arguments_json"]
    assert status(queue, click) == ("expired", "human_control")
    # Geração diferente (parada e retomada no meio) também não sai.
    stale = queued(
        store,
        runtime["executor_id"],
        agent,
        "computer.shell.run",
        computer_id=computer["id"],
        generation=generation - 1,
    )
    assert queue.claim(runtime["executor_id"], "conn-1") == []
    assert status(queue, stale) == ("expired", "global_stop")
    # Parada global expira pendentes imediatamente e bloqueia novos computadores.
    pending = queued(
        store,
        runtime["executor_id"],
        agent,
        "computer.shell.run",
        computer_id=computer["id"],
        generation=generation,
    )
    safety = SafetyService(database)
    current = safety.current()
    safety.command(
        __import__("bees_core.safety", fromlist=["SafetyCommand"]).SafetyCommand(
            client_request_id=uuid4(), kind="stop", expected_revision=current["revision"]
        )
    )
    assert status(queue, pending) == ("expired", "global_stop")
    assert safety.current()["in_flight"]["executor_commands"] == 1  # shell já enviado
    current_computer = service.computers(agent.id)[0]
    service.remove_computer(
        agent.id, computer["id"], {"expected_revision": current_computer["revision"]}
    )
    with pytest.raises(ExecutorError, match="global_stop"):
        service.create_computer(agent.id, {"client_request_id": uuid4()})


def test_claim_rechecks_personal_grant_and_report_connection(state):
    database, store, agent, service, clock = state
    queue = CommandQueue(database, clock=clock)
    personal = pair(service, "personal")
    grant = service.set_grant(
        personal["executor_id"],
        agent.id,
        {"expected_revision": 0, "enabled": True, "roots": ["Docs"], "write": True},
    )
    with store.transaction(write=False) as unit:
        generation = unit.safety.state().generation
    scope = grant["scope"]
    first = queued(
        store,
        personal["executor_id"],
        agent,
        "personal.files.write",
        scope=scope,
        generation=generation,
    )
    narrowed = service.set_grant(
        personal["executor_id"],
        agent.id,
        {"expected_revision": grant["revision"], "enabled": True, "roots": ["Docs"]},
    )
    assert status(queue, first) == ("expired", "grant_changed")
    second = queued(
        store,
        personal["executor_id"],
        agent,
        "personal.files.write",
        scope=scope,
        generation=generation,
    )
    assert queue.claim(personal["executor_id"], "conn-1") == []
    assert status(queue, second) == ("expired", "grant_changed")
    ok = queued(
        store,
        personal["executor_id"],
        agent,
        "personal.files.read",
        scope=narrowed["scope"],
        generation=generation,
    )
    assert [row["id"] for row in queue.claim(personal["executor_id"], "conn-1")] == [ok]
    # Conexão antiga não fecha o comando enviado pela conexão atual.
    with pytest.raises(ExecutorError, match="executor_protocol_invalid"):
        queue.report(personal["executor_id"], ok, "confirmed", {}, connection_id="conn-velha")
    assert queue.report(personal["executor_id"], ok, "confirmed", {}, connection_id="conn-1")


def test_resume_closes_queue_and_late_report_is_only_evidence(state):
    from bees_core.executors import close_for_resume

    database, store, agent, service, clock = state
    queue = CommandQueue(database, clock=clock)
    runtime = pair(service)
    computer = service.create_computer(agent.id, {"client_request_id": uuid4()})
    service.report_computer(runtime["executor_id"], computer["id"], "ready")
    with store.transaction(write=False) as unit:
        generation = unit.safety.state().generation
    waiting = queued(
        store,
        runtime["executor_id"],
        agent,
        "computer.shell.run",
        computer_id=computer["id"],
        generation=generation,
    )
    with store.transaction() as unit:
        assert close_for_resume(unit, waiting) == "expired"
    assert queue.claim(runtime["executor_id"], "conn-1") == []
    sent = queued(
        store,
        runtime["executor_id"],
        agent,
        "computer.shell.run",
        computer_id=computer["id"],
        generation=generation,
    )
    assert [row["id"] for row in queue.claim(runtime["executor_id"], "conn-1")] == [sent]
    with store.transaction() as unit:
        assert close_for_resume(unit, sent) == "unknown"
    assert queue.report(runtime["executor_id"], sent, "confirmed", {"ok": True}) is False
    row = queue.get(sent)
    assert row["status"] == "unknown" and row["error_code"] == "late_confirmed"
    assert '"late_status": "confirmed"' in row["result_json"]
