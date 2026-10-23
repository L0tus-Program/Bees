"""Conectores MCP: protocolo (JSON e SSE), catálogo validado e chamada com aprovação."""

import asyncio
import json
from uuid import uuid4

import httpx
import pytest
from test_agent_tools import tool_calls
from test_tasks_execution import answer, fixture_state

from bees_core.agent_tools.cycle import AgentTaskInput, AgentTaskService, AgentTaskWorker
from bees_core.agent_tools.service import AgentToolDecision, AgentToolService
from bees_core.mcp import CreateServer, McpClient, McpError, McpService, result_text
from bees_core.providers.contracts import ProviderConfig
from bees_core.tools import ToolService

TOOLS = [
    {
        "name": "create_issue",
        "description": "Cria uma tarefa",
        "inputSchema": {
            "type": "object",
            "properties": {"title": {"type": "string"}},
            "required": ["title"],
        },
    },
    {"name": "bad", "inputSchema": {"type": 5}},
]


class Server:
    def __init__(self, sse=False):
        self.sse = sse
        self.calls = []
        self.sessions = []

    def __call__(self, request: httpx.Request):
        assert request.headers["authorization"] == "Bearer segredo"
        body = json.loads(request.content)
        self.sessions.append(request.headers.get("mcp-session-id"))
        if "id" not in body:
            return httpx.Response(202)
        if body["method"] == "initialize":
            result = {"protocolVersion": "2025-06-18", "serverInfo": {"name": "Tarefas"}}
        elif body["method"] == "tools/list":
            result = {"tools": TOOLS}
        elif body["method"] == "tools/call":
            self.calls.append(body["params"])
            result = {"content": [{"type": "text", "text": "Criada #7"}], "isError": False}
        else:
            return httpx.Response(400)
        message = {"jsonrpc": "2.0", "id": body["id"], "result": result}
        headers = {"mcp-session-id": "s1"}
        if self.sse:
            return httpx.Response(
                200,
                content=f"event: message\ndata: {json.dumps(message)}\n\n".encode(),
                headers=headers | {"content-type": "text/event-stream"},
            )
        return httpx.Response(200, json=message, headers=headers)


class Secrets:
    def resolve(self, ref):
        from pydantic import SecretStr

        assert ref == "vault:mcp"
        return SecretStr("segredo")


@pytest.mark.parametrize("sse", [False, True])
def test_client_lists_valid_tools_and_calls_with_session(sse):
    server = Server(sse=sse)
    client = McpClient(
        "https://mcp.example.org/mcp", "segredo", transport=httpx.MockTransport(server)
    )
    info, tools = asyncio.run(client.list_tools())
    assert info["name"] == "Tarefas" and [tool["name"] for tool in tools] == ["create_issue"]
    assert "s1" in server.sessions
    result = asyncio.run(client.call("create_issue", {"title": "x"}))
    assert result_text(result)["text"] == "Criada #7"


def test_url_rules():
    with pytest.raises(ValueError):
        McpClient("http://mcp.example.org/mcp")
    McpClient("http://127.0.0.1:3000/mcp")


@pytest.fixture
def setup(tmp_path):
    config = ProviderConfig(
        kind="openai_compatible",
        endpoint="https://fixture.test/v1",
        model="fixture-agent",
        capabilities={"text": True, "tool_calls": True},
    )
    database, store, agent, chat, _ = fixture_state(tmp_path, config=config)
    tools = ToolService(database)
    for manifest in tools.catalog()[1:]:
        plugin = tools.install({"client_request_id": uuid4(), "manifest": manifest})
        tools.update_plugin(plugin.id, {"expected_revision": 1, "enabled": True})
        for tool in manifest["tools"]:
            tools.set_grant(
                agent.id, plugin.id, tool["tool_name"], {"expected_revision": 0, "enabled": True}
            )
    server = Server()
    service = McpService(database, Secrets(), transport=httpx.MockTransport(server))
    info, listed = asyncio.run(
        McpClient(
            "https://mcp.example.org/mcp", "segredo", transport=httpx.MockTransport(server)
        ).list_tools()
    )
    row = service.create(
        agent.id,
        CreateServer(client_request_id=uuid4(), name="tarefas", url="https://mcp.example.org/mcp"),
        secret_ref="vault:mcp",
        server=info,
        tools=listed,
    )
    return database, agent, server, service, row


def test_service_validates_arguments_and_unknown_tools(setup):
    _, agent, server, service, row = setup
    with pytest.raises(McpError, match="mcp_unknown_tool"):
        asyncio.run(service.call(agent.id, "tarefas", "delete_all", {}))
    with pytest.raises(McpError, match="mcp_invalid_arguments"):
        asyncio.run(service.call(agent.id, "tarefas", "create_issue", {"title": 3}))
    with pytest.raises(McpError, match="mcp_server_unavailable"):
        asyncio.run(service.call(agent.id, "outro", "create_issue", {"title": "x"}))
    assert server.calls == []
    disabled = service.set_enabled(agent.id, row["id"], row["revision"], False)
    with pytest.raises(McpError, match="mcp_server_unavailable"):
        asyncio.run(service.call(agent.id, "tarefas", "create_issue", {"title": "x"}))
    service.remove(agent.id, row["id"], disabled["revision"])
    assert service.list(agent.id) == []


def test_agent_lists_then_calls_after_human_approval(setup):
    database, agent, server, service, row = setup
    task = AgentTaskService(database).create(
        agent.id,
        AgentTaskInput(
            client_request_id=uuid4(), title="Criar tarefa", objective="Crie a tarefa X"
        ),
    )
    script = [
        tool_calls(("c1", "mcp_list_tools", {})),
        tool_calls(
            (
                "c2",
                "mcp_call",
                {"server": "tarefas", "tool": "create_issue", "arguments": {"title": "X"}},
            )
        ),
        answer("Tarefa criada: #7"),
    ]
    requests = []

    def provider(request):
        requests.append(json.loads(request.content))
        return script.pop(0)

    worker = AgentTaskWorker(
        database,
        transport=httpx.MockTransport(provider),
        mcp_transport=httpx.MockTransport(server),
        resolver=Secrets(),
    )

    def drain():
        for _ in range(20):
            if not asyncio.run(worker.run_once()):
                return

    drain()
    names = {tool["function"]["name"] for tool in requests[0]["tools"]}
    assert {"mcp_list_tools", "mcp_call"} <= names
    assert "create_issue" in json.dumps(requests[1]["messages"])
    # Efeito externo aguarda decisão humana; nada foi chamado ainda.
    assert server.calls == []
    pending = AgentToolService(database).list(agent.id, status="pending")
    assert len(pending) == 1 and pending[0]["tool_name"] == "mcp.call"
    AgentToolService(database).decide(
        agent.id,
        pending[0]["id"],
        AgentToolDecision(
            client_request_id=uuid4(),
            expected_revision=pending[0]["revision"],
            decision="allow_once",
        ),
    )
    drain()
    assert server.calls == [{"name": "create_issue", "arguments": {"title": "X"}}]
    detail = AgentTaskService(database).detail(agent.id, task.id)
    assert detail.task.status == "completed"
