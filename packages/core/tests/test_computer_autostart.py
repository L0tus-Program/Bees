"""A abelha liga o próprio computador quando precisa, sem passo manual."""

import asyncio
import json
from uuid import uuid4

import httpx
from test_agent_tools import tool_calls
from test_executors import pair
from test_tasks_execution import answer, fixture_state
from test_team import CONFIG, grant_all

from bees_core.agent_tools.cycle import (
    AgentTaskInput,
    AgentTaskService,
    AgentTaskWorker,
    available_tools,
)
from bees_core.agent_tools.runners import Outcome
from bees_core.executors import ExecutorService, GatewayRunner


def test_agent_turns_on_its_computer_and_waits_until_ready(tmp_path, monkeypatch):
    database, store, agent, _, _ = fixture_state(tmp_path, config=CONFIG)
    grant_all(database, agent)
    service = ExecutorService(database)
    with store.transaction(write=False) as unit:
        assert not [s for s, _ in available_tools(unit, agent.id) if s.target == "computer"]
    runtime = pair(service)
    with store.transaction(write=False) as unit:
        offered = {spec.name for spec, _ in available_tools(unit, agent.id)}
    # Runtime online e nenhum computador: as ferramentas do computador já são oferecidas.
    assert "computer.shell.run" in offered and "computer.files.list" in offered

    async def confirmed(self, spec, arguments, context, target):
        return Outcome("confirmed", {"entries": []})

    monkeypatch.setattr(GatewayRunner, "run", confirmed)
    script = [tool_calls(("c1", "computer_list_files", {"path": "."})), answer("Pasta vazia.")]
    requests = []

    def respond(request):
        requests.append(json.loads(request.content))
        return script.pop(0)

    worker = AgentTaskWorker(database, transport=httpx.MockTransport(respond))
    task = AgentTaskService(database).create(
        agent.id, AgentTaskInput(client_request_id=uuid4(), title="Arquivos", objective="Liste")
    )

    async def runtime_reports_ready():
        # O runtime real recebe o pedido pelo WebSocket e responde "pronto".
        for _ in range(200):
            computers = service.computers(agent.id)
            if computers and computers[0]["status"] == "provisioning":
                service.report_computer(runtime["executor_id"], computers[0]["id"], "ready")
                return computers[0]["id"]
            await asyncio.sleep(0.05)
        raise AssertionError("computador não foi pedido")

    async def scenario():
        reporter = asyncio.create_task(runtime_reports_ready())
        for _ in range(10):
            await worker.run_once()
            if AgentTaskService(database).detail(agent.id, task.id).task.status == "completed":
                break
        return await reporter

    computer_id = asyncio.run(scenario())
    detail = AgentTaskService(database).detail(agent.id, task.id)
    assert detail.task.status == "completed" and script == []
    (result,) = [json.loads(m.content) for m in detail.messages if m.role == "tool"]
    assert result["status"] == "confirmed"
    assert [c["id"] for c in service.computers(agent.id)] == [computer_id]
    with store.transaction(write=False) as unit:
        command = unit.agents._context.connection.execute(
            "SELECT computer_id FROM executor_commands WHERE tool_name='computer.files.list'"
        ).fetchone()
    assert command[0] == computer_id


def test_no_runtime_means_no_computer_tools_and_no_silent_creation(tmp_path):
    database, store, agent, _, _ = fixture_state(tmp_path, config=CONFIG)
    grant_all(database, agent)
    service = ExecutorService(database)
    runtime = pair(service)
    service.revoke(runtime["executor_id"], {"expected_revision": service.list()[0]["revision"]})
    with store.transaction(write=False) as unit:
        assert not [s for s, _ in available_tools(unit, agent.id) if s.target == "computer"]
    assert service.computers(agent.id) == []
