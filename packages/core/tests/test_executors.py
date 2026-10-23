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
