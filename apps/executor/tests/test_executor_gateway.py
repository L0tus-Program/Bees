"""API real + executor por WebSocket + worker do agente operando o computador próprio."""

import asyncio
import json
import socket
import sys
import threading
import time
from uuid import uuid4

import httpx
import pytest
import uvicorn

from bees_api.app import create_app
from bees_api.config import Settings
from bees_core.agent_tools.cycle import AgentTaskInput, AgentTaskService, AgentTaskWorker
from bees_core.agent_tools.service import AgentToolService
from bees_core.artifacts import ArtifactStore
from bees_core.executors import ExecutorService
from bees_core.models import Agent
from bees_core.providers.contracts import ProviderConfig
from bees_core.storage.database import Database
from bees_core.storage.store import StateStore
from bees_core.tools import ToolService
from bees_executor.cli import ComputerRuntime, PersonalConnector
from bees_executor.client import CredentialRejected, ExecutorClient, exchange
from bees_executor.computers import SingleComputers
from bees_executor.journal import Journal
from bees_executor.tools import ToolHost

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="Shell bash do runtime Linux.")


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class Server:
    def __init__(self, tmp_path):
        self.port = free_port()
        settings = Settings(
            data_dir=tmp_path / "state", web_dist=tmp_path / "missing", port=self.port
        )
        self.app = create_app(settings)
        config = uvicorn.Config(self.app, host="127.0.0.1", port=self.port, log_level="warning")
        self.server = uvicorn.Server(config)
        self.thread = threading.Thread(target=self.server.run, daemon=True)
        self.thread.start()
        for _ in range(100):
            if self.server.started:
                break
            time.sleep(0.05)
        self.url = f"http://127.0.0.1:{self.port}"
        self.database = Database(tmp_path / "state" / "bees.sqlite3")

    def stop(self):
        self.server.should_exit = True
        self.thread.join(timeout=10)


def calls(*items):
    return httpx.Response(
        200,
        json={
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": identity,
                                "type": "function",
                                "function": {"name": name, "arguments": json.dumps(args)},
                            }
                            for identity, name, args in items
                        ],
                    },
                    "finish_reason": "tool_calls",
                }
            ]
        },
    )


def text(content):
    return httpx.Response(
        200,
        json={
            "choices": [
                {"message": {"role": "assistant", "content": content}, "finish_reason": "stop"}
            ]
        },
    )


@pytest.fixture
def server(tmp_path):
    server = Server(tmp_path)
    yield server
    server.stop()


def agent_with_tools(database, bundle):
    config = ProviderConfig(
        kind="openai_compatible",
        endpoint="https://fixture.test/v1",
        model="fixture",
        capabilities={"text": True, "tool_calls": True},
    )
    agent = Agent(name="Abelha", provider_config=config.model_dump(mode="json"))
    with StateStore(database).transaction() as unit:
        unit.agents.create(agent)
    tools = ToolService(database)
    manifest = next(item for item in tools.catalog() if item["plugin_key"] == bundle)
    plugin = tools.install({"client_request_id": uuid4(), "manifest": manifest})
    plugin = tools.update_plugin(plugin.id, {"expected_revision": 1, "enabled": True})
    for tool in manifest["tools"]:
        tools.set_grant(
            agent.id, plugin.id, tool["tool_name"], {"expected_revision": 0, "enabled": True}
        )
    return agent


async def wait_for(predicate, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.1)
    raise AssertionError("condição não atingida")


def test_computer_runtime_end_to_end(server, tmp_path):
    database = server.database
    service = ExecutorService(database)
    invite = service.invite({"kind": "computer_runtime", "name": "Runtime local"})
    with pytest.raises(CredentialRejected):
        exchange(server.url, "x" * 30, tmp_path / "runtime")
    credential = exchange(server.url, invite["token"], tmp_path / "runtime")
    with pytest.raises(CredentialRejected):
        exchange(server.url, invite["token"], tmp_path / "other")  # uso único
    agent = agent_with_tools(database, "bees.computer")
    runtime = ComputerRuntime(SingleComputers(tmp_path / "computers"))
    client = ExecutorClient(server.url, tmp_path / "runtime", "computer_runtime", runtime)

    script = [
        calls(
            (
                "c1",
                "computer_write_file",
                {"path": "relatorio/notas.md", "content": "# Notas\nabelhas polinizam"},
            )
        ),
        calls(("c2", "computer_shell", {"command": "wc -w relatorio/notas.md && echo feito"})),
        calls(("c3", "computer_read_file", {"path": "relatorio/notas.md"})),
        calls(("c4", "computer_publish_file", {"path": "relatorio/notas.md"})),
        calls(("c5", "computer_read_file", {"path": "../../etc/passwd"})),
        text("Pronto: notas criadas e publicadas."),
    ]
    worker = AgentTaskWorker(
        database,
        transport=httpx.MockTransport(lambda request: script.pop(0)),
        artifacts=ArtifactStore(database, tmp_path / "artifacts", actor="worker"),
    )
    approvals = AgentToolService(database)

    async def scenario():
        connection = asyncio.create_task(client.run_forever(credential))
        await wait_for(lambda: service.list("computer_runtime")[0]["online"])
        computer = service.create_computer(agent.id, {"client_request_id": uuid4()})
        await wait_for(lambda: service.computers(agent.id)[0]["status"] == "ready")
        task = AgentTaskService(database).create(
            agent.id,
            AgentTaskInput(client_request_id=uuid4(), title="Notas", objective="Crie notas."),
        )
        for _ in range(30):
            await worker.run_once()
            detail = AgentTaskService(database).detail(agent.id, task.id)
            if detail.task.status == "waiting_approval":
                (pending,) = approvals.list(agent.id, task_id=task.id, status="pending")
                assert pending["tool_name"] == "computer.shell.run"
                approvals.decide(
                    agent.id,
                    pending["id"],
                    {
                        "client_request_id": uuid4(),
                        "expected_revision": 1,
                        "decision": "allow_once",
                    },
                )
            if detail.task.status == "completed":
                break
        client.stopped.set()
        connection.cancel()
        await asyncio.gather(connection, return_exceptions=True)
        return computer, task

    computer, task = asyncio.run(scenario())
    detail = AgentTaskService(database).detail(agent.id, task.id)
    assert detail.task.status == "completed"
    results = [json.loads(m.content) for m in detail.messages if m.role == "tool"]
    write, shell, read, publish, escape = results
    assert write["status"] == "confirmed" and write["result"]["path"] == "relatorio/notas.md"
    assert shell["result"]["exit_code"] == 0 and "feito" in shell["result"]["stdout"]
    assert "abelhas polinizam" in read["result"]["content"]
    assert (
        publish["result"]["name"] == "notas.md"
        and publish["result"]["media_type"] == "text/markdown"
    )
    assert escape["status"] == "failed" and escape["code"] == "path_outside_roots"
    workspace = tmp_path / "computers" / computer["id"] / "workspace"
    assert (workspace / "relatorio" / "notas.md").read_text() == "# Notas\nabelhas polinizam"
    # Cada comando executou uma única vez (journal local) e não restou comando pendente.
    with StateStore(database).transaction(write=False) as unit:
        statuses = unit.agents._context.connection.execute(
            "SELECT status, count(*) FROM executor_commands GROUP BY status"
        ).fetchall()
    assert dict(statuses) == {"confirmed": 4, "failed": 1}


def test_offline_executor_expires_without_effect_and_revocation(server, tmp_path):
    database = server.database
    service = ExecutorService(database)
    invite = service.invite({"kind": "computer_runtime", "name": "Runtime"})
    credential = exchange(server.url, invite["token"], tmp_path / "runtime")
    agent = agent_with_tools(database, "bees.computer")
    runtime = ComputerRuntime(SingleComputers(tmp_path / "computers"))
    client = ExecutorClient(server.url, tmp_path / "runtime", "computer_runtime", runtime)

    async def scenario():
        connection = asyncio.create_task(client.run_forever(credential))
        await wait_for(lambda: service.list("computer_runtime")[0]["online"])
        service.create_computer(agent.id, {"client_request_id": uuid4()})
        await wait_for(lambda: service.computers(agent.id)[0]["status"] == "ready")
        executor = service.list("computer_runtime")[0]
        service.revoke(executor["id"], {"expected_revision": executor["revision"]})
        await wait_for(lambda: connection.done(), timeout=20)
        return connection

    connection = asyncio.run(scenario())
    assert connection.exception() is not None  # credencial revogada encerra o cliente
    assert service.computers(agent.id)[0]["status"] == "failed"
    from bees_core.agent_tools.catalog import spec
    from bees_core.tools import ToolError

    with StateStore(database).transaction(write=False) as unit, pytest.raises(ToolError):
        from bees_core.executors import resolve_target

        resolve_target(unit, agent.id, spec("computer.files.list"))


def human_session(server):
    human = httpx.Client(base_url=server.url, headers={"Origin": server.url}, timeout=60)
    setup = human.post(
        "/api/v1/auth/setup",
        json={
            "bootstrap_token": server.app.state.identity.issue_bootstrap(),
            "name": "Teste",
            "password": "senha teste reinicio 123456",
        },
    )
    assert setup.status_code == 200, setup.text
    human.headers["X-Bees-CSRF"] = human.get("/api/v1/auth/status").json()["csrf_token"]
    return human


def test_human_restart_reaches_the_runtime_once(server, tmp_path):
    database = server.database
    service = ExecutorService(database)
    invite = service.invite({"kind": "computer_runtime", "name": "Runtime"})
    credential = exchange(server.url, invite["token"], tmp_path / "runtime")
    agent = agent_with_tools(database, "bees.computer")
    computers = SingleComputers(tmp_path / "computers")
    restarts = []
    original = computers.restart

    async def counted(computer_id):
        restarts.append(computer_id)
        await original(computer_id)

    computers.restart = counted
    client = ExecutorClient(
        server.url, tmp_path / "runtime", "computer_runtime", ComputerRuntime(computers)
    )
    human = human_session(server)

    async def scenario():
        connection = asyncio.create_task(client.run_forever(credential))
        await wait_for(lambda: service.list("computer_runtime")[0]["online"])
        computer = service.create_computer(agent.id, {"client_request_id": uuid4()})
        await wait_for(lambda: service.computers(agent.id)[0]["status"] == "ready")
        path = f"/api/v1/agents/{agent.id}/computers/{computer['id']}/restart"
        body = {"client_request_id": str(uuid4())}
        first = await asyncio.to_thread(human.post, path, json=body)
        replay = await asyncio.to_thread(human.post, path, json=body)
        forged = await asyncio.to_thread(
            httpx.post,
            server.url + path,
            json={"client_request_id": str(uuid4())},
            headers={"Origin": server.url, "X-Bees-CSRF": "x"},
            cookies=human.cookies,
        )
        other = await asyncio.to_thread(
            human.post,
            f"/api/v1/agents/{uuid4()}/computers/{computer['id']}/restart",
            json={"client_request_id": str(uuid4())},
        )
        client.stopped.set()
        connection.cancel()
        await asyncio.gather(connection, return_exceptions=True)
        return computer, first, replay, forged, other

    computer, first, replay, forged, other = asyncio.run(scenario())
    human.close()
    assert first.status_code == 200, first.text
    assert first.json()["restarted"] is True and first.json()["id"] == computer["id"]
    # Mesmo pedido repetido devolve o mesmo recibo sem reiniciar de novo.
    assert replay.status_code == 200 and restarts == [computer["id"]]
    assert forged.status_code == 403
    assert other.status_code == 404


def test_personal_connector_scope_and_roots(server, tmp_path):
    database = server.database
    service = ExecutorService(database)
    invite = service.invite({"kind": "personal", "name": "Notebook"})
    state = tmp_path / "personal"
    credential = exchange(server.url, invite["token"], state)
    documents = tmp_path / "Documentos"
    documents.mkdir()
    (documents / "contrato.txt").write_text("Cláusula 1: prazo de 30 dias.")
    secret = tmp_path / "Privado"
    secret.mkdir()
    (secret / "senha.txt").write_text("não ler")
    host = ToolHost(
        {"Documentos": documents, "Privado": secret},
        tmp_path,
        Journal(state / "journal.sqlite3"),
        named=True,
    )
    client = ExecutorClient(
        server.url, state, "personal", PersonalConnector(host), roots=["Documentos", "Privado"]
    )
    agent = agent_with_tools(database, "bees.personal")
    script = [
        calls(("p1", "personal_list_files", {"path": "."})),
        calls(("p2", "personal_read_file", {"path": "Documentos/contrato.txt"})),
        calls(("p3", "personal_read_file", {"path": "Privado/senha.txt"})),
        text("Resumo: prazo de 30 dias."),
    ]
    worker = AgentTaskWorker(database, transport=httpx.MockTransport(lambda r: script.pop(0)))

    async def scenario():
        connection = asyncio.create_task(client.run_forever(credential))
        await wait_for(lambda: service.list("personal")[0]["online"])
        executor = service.list("personal")[0]
        assert executor["capabilities"] == [] or isinstance(executor["capabilities"], list)
        service.set_grant(
            executor["id"],
            agent.id,
            {"expected_revision": 0, "enabled": True, "roots": ["Documentos"]},
        )
        task = AgentTaskService(database).create(
            agent.id,
            AgentTaskInput(
                client_request_id=uuid4(), title="Contrato", objective="Leia o contrato."
            ),
        )
        for _ in range(20):
            await worker.run_once()
            if AgentTaskService(database).detail(agent.id, task.id).task.status == "completed":
                break
        client.stopped.set()
        connection.cancel()
        await asyncio.gather(connection, return_exceptions=True)
        return task

    task = asyncio.run(scenario())
    detail = AgentTaskService(database).detail(agent.id, task.id)
    assert detail.task.status == "completed"
    listing, read, denied = [json.loads(m.content) for m in detail.messages if m.role == "tool"]
    assert listing["result"]["entries"] == [{"name": "Documentos", "type": "root"}]
    assert "30 dias" in read["result"]["content"]
    assert denied["status"] == "failed" and denied["code"] == "path_outside_roots"
