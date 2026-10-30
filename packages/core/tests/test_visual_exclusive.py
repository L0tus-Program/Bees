"""Tela/navegador do computador da abelha: uma tarefa viva por vez; a outra aguarda."""

import asyncio
import json
from uuid import uuid4

import httpx
import pytest
from test_agent_tools import tool_calls
from test_executors import pair
from test_tasks_execution import answer, fixture_state
from test_team import CONFIG, grant_all

from bees_core.agent_tools.cycle import AgentTaskInput, AgentTaskService, AgentTaskWorker
from bees_core.agent_tools.runners import Outcome
from bees_core.agent_tools.service import AgentToolDecision, AgentToolService
from bees_core.executors import ExecutorService, GatewayRunner


@pytest.fixture
def setup(tmp_path, monkeypatch):
    database, store, agent, _, _ = fixture_state(tmp_path, config=CONFIG)
    grant_all(database, agent)
    service = ExecutorService(database)
    runtime = pair(service)
    computer = service.create_computer(agent.id, {"client_request_id": uuid4()})
    service.report_computer(runtime["executor_id"], computer["id"], "ready")

    async def confirmed(self, spec, arguments, context, target):
        return Outcome("confirmed", {"ok": True})

    # O comando nasce no início (on_begin) como no executor real; o recibo é imediato.
    monkeypatch.setattr(GatewayRunner, "run", confirmed)
    return database, store, agent


def worker(database, scripts, requests):
    def respond(request):
        body = json.loads(request.content)
        requests.append(body)
        text = json.dumps(body["messages"], ensure_ascii=False)
        return scripts["A" if "Tarefa A" in text else "B"].pop(0)

    return AgentTaskWorker(database, transport=httpx.MockTransport(respond))


def drain(work, rounds=12):
    for _ in range(rounds):
        if not asyncio.run(work.run_once()):
            return


def status(database, agent, task):
    return AgentTaskService(database).detail(agent.id, task.id).task.status


def test_second_task_waits_for_the_browser_without_calling_the_model(setup):
    database, store, agent = setup
    tasks = AgentTaskService(database)
    first = tasks.create(
        agent.id, AgentTaskInput(client_request_id=uuid4(), title="Tarefa A", objective="A")
    )
    scripts = {
        "A": [
            tool_calls(("a1", "computer_browser_open", {"url": "https://example.com"})),
            # Segunda ação pede decisão humana: A continua viva e com o navegador.
            tool_calls(("a2", "computer_delete_file", {"path": "rascunho.txt"})),
        ],
        "B": [tool_calls(("b1", "computer_browser_open", {"url": "https://example.org"}))],
    }
    requests = []
    work = worker(database, scripts, requests)
    drain(work)
    assert status(database, agent, first) == "waiting_approval"
    second = tasks.create(
        agent.id, AgentTaskInput(client_request_id=uuid4(), title="Tarefa B", objective="B")
    )
    drain(work)
    assert status(database, agent, second) == "waiting_resource"
    calls_before = len(requests)
    drain(work)
    # Esperando: nenhuma geração nova e nenhum comando visual da tarefa B.
    assert len(requests) == calls_before
    with store.transaction(write=False) as unit:
        browser = unit.agents._context.connection.execute(
            "SELECT count(*) FROM executor_commands WHERE tool_name='computer.browser.open'"
        ).fetchone()[0]
        run = unit.runs.list(task_id=second.id, newest_first=True)[0]
    assert browser == 1
    assert run.checkpoint["waiting_for"]["task_id"] == str(first.id)
    # A termina (recusa a remoção e responde): B volta à fila e usa o navegador.
    approvals = AgentToolService(database)
    (pending,) = approvals.list(agent.id, task_id=first.id, status="pending")
    approvals.decide(
        agent.id,
        pending["id"],
        AgentToolDecision(
            client_request_id=uuid4(), expected_revision=pending["revision"], decision="deny"
        ),
    )
    scripts["A"].append(answer("Tarefa A pronta."))
    scripts["B"].append(answer("Tarefa B pronta."))
    drain(work)
    assert status(database, agent, first) == "completed"
    assert status(database, agent, second) == "completed"
    with store.transaction(write=False) as unit:
        browser = unit.agents._context.connection.execute(
            "SELECT count(*) FROM executor_commands WHERE tool_name='computer.browser.open'"
        ).fetchone()[0]
    assert browser == 2 and scripts == {"A": [], "B": []}


def test_non_visual_tools_run_in_parallel_tasks(setup):
    database, store, agent = setup
    tasks = AgentTaskService(database)
    first = tasks.create(
        agent.id, AgentTaskInput(client_request_id=uuid4(), title="Tarefa A", objective="A")
    )
    scripts = {
        "A": [
            tool_calls(("a1", "computer_browser_open", {"url": "https://example.com"})),
            tool_calls(("a2", "computer_delete_file", {"path": "rascunho.txt"})),
        ],
        "B": [
            tool_calls(("b1", "computer_list_files", {"path": "."})),
            answer("Arquivos listados."),
        ],
    }
    work = worker(database, scripts, [])
    drain(work)
    second = tasks.create(
        agent.id, AgentTaskInput(client_request_id=uuid4(), title="Tarefa B", objective="B")
    )
    drain(work)
    assert status(database, agent, first) == "waiting_approval"
    assert status(database, agent, second) == "completed"


def test_personal_browser_is_shared_by_bees_but_owned_by_one_task(tmp_path):
    from test_executors import queued

    from bees_core.agent_tools.service import ToolTarget
    from bees_core.executors import visual_holder

    database, store, agent, _, _ = fixture_state(tmp_path, config=CONFIG)
    service = ExecutorService(database)
    personal = pair(service, "personal")
    command = queued(store, personal["executor_id"], agent, "personal.browser.open")
    target = ToolTarget("personal", "env", "res", "id", personal["executor_id"])
    with store.transaction() as unit:
        owner = unit.runs.get(unit.actions.get(__import__("uuid").UUID(command)).run_id).task_id
        assert visual_holder(unit, uuid4(), target, "personal.browser.click") == str(owner)
        assert visual_holder(unit, owner, target, "personal.browser.click") is None
        assert visual_holder(unit, uuid4(), target, "personal.files.read") is None
        other = ToolTarget("personal", "env", "res", "id", str(uuid4()))
        assert visual_holder(unit, uuid4(), other, "personal.browser.click") is None
        task = unit.tasks.get(owner)
        unit.tasks.update(task.model_copy(update={"status": "cancelled"}), task.revision)
    with store.transaction(write=False) as unit:
        assert visual_holder(unit, uuid4(), target, "personal.browser.click") is None
