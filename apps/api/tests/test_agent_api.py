"""API do agente: ferramentas recomendadas, mensagem no chat, decisão e resposta."""

import asyncio
import json
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from bees_api.app import create_app
from bees_api.config import Settings
from bees_core.agent_tools.cycle import AgentTaskWorker
from bees_core.models import Agent, Conversation
from bees_core.providers.contracts import ProviderConfig

ORIGIN = "http://127.0.0.1:8000"
PASSWORD = "senha teste agente 123456"


def headers(client):
    return {"Origin": ORIGIN, "X-Bees-CSRF": client.get("/api/v1/auth/status").json()["csrf_token"]}


@pytest.fixture
def state(tmp_path):
    settings = Settings(data_dir=tmp_path / "state", web_dist=tmp_path / "missing")
    with TestClient(create_app(settings), base_url=ORIGIN, raise_server_exceptions=False) as client:
        assert (
            client.post(
                "/api/v1/auth/setup",
                json={
                    "bootstrap_token": client.app.state.identity.issue_bootstrap(),
                    "name": "Teste",
                    "password": PASSWORD,
                },
                headers={"Origin": ORIGIN},
            ).status_code
            == 200
        )
        config = ProviderConfig(
            kind="openai_compatible",
            endpoint="https://fixture.invalid/v1",
            model="fixture-agent",
            capabilities={"text": True, "tool_calls": True},
        )
        agent = Agent(name="Teste", provider_config=config.model_dump(mode="json"))
        conversation = Conversation(agent_id=agent.id)
        with client.app.state.store.transaction() as unit:
            unit.agents.create(agent)
            unit.conversations.create(conversation)
        yield client, agent, conversation


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


def test_recommended_tools_chat_message_approval_and_reply(state):
    client, agent, conversation = state
    base = f"/api/v1/agents/{agent.id}"
    before = client.get(f"{base}/agent/capabilities").json()
    assert before["tool_calls_supported"] and not before["available"]
    assert client.post(f"{base}/agent/recommended-tools", json={}).status_code == 403
    request_id = str(uuid4())
    response = client.post(
        f"{base}/agent/recommended-tools",
        json={"client_request_id": request_id},
        headers=headers(client),
    )
    assert response.status_code == 200
    again = client.post(
        f"{base}/agent/recommended-tools",
        json={"client_request_id": str(uuid4())},
        headers=headers(client),
    )
    assert again.status_code == 200
    after = client.get(f"{base}/agent/capabilities").json()
    assert after["available"] and after["recommended_installed"]
    assert {tool["function"] for tool in after["tools"]} >= {"web_search", "memory_save"}

    message_id = str(uuid4())
    body = {"client_request_id": message_id, "content": "Lembre que prefiro respostas curtas."}
    created = client.post(
        f"{base}/conversations/{conversation.id}/agent-messages", json=body, headers=headers(client)
    )
    assert created.status_code == 200, created.text
    task_id = created.json()["task_id"]
    replay = client.post(
        f"{base}/conversations/{conversation.id}/agent-messages", json=body, headers=headers(client)
    )
    assert replay.json()["task_id"] == task_id
    conflict = client.post(
        f"{base}/conversations/{conversation.id}/agent-messages",
        json=body | {"content": "Outra coisa"},
        headers=headers(client),
    )
    assert conflict.status_code == 409

    script = [
        calls(("m1", "memory_save", {"content": "Prefere respostas curtas."})),
        text("Anotado: respostas curtas."),
    ]
    worker = AgentTaskWorker(
        client.app.state.database,
        transport=httpx.MockTransport(lambda request: script.pop(0)),
    )
    for _ in range(3):
        asyncio.run(worker.run_once())
    task = client.get(f"{base}/tasks/{task_id}").json()["task"]
    assert task["status"] == "waiting_approval"
    assert task["execution_kind"] == "agent_task" and task["agent_approvals_available"]
    pending = client.get(f"{base}/agent-tool-approvals", params={"status": "pending"}).json()
    (approval,) = pending["approvals"]
    detail = client.get(f"{base}/agent-tool-approvals/{approval['id']}").json()
    assert detail["arguments_preview"] == {"content": "Prefere respostas curtas."}
    decided = client.post(
        f"{base}/agent-tool-approvals/{approval['id']}/decision",
        json={"client_request_id": str(uuid4()), "expected_revision": 1, "decision": "allow_once"},
        headers=headers(client),
    )
    assert decided.status_code == 200 and decided.json()["receipt"]["confirmed"]
    for _ in range(4):
        asyncio.run(worker.run_once())
    task = client.get(f"{base}/tasks/{task_id}").json()["task"]
    assert task["status"] == "completed"
    assert [step["tool"] for step in task["agent_steps"]] == ["memory_save"]
    with client.app.state.store.transaction(write=False) as unit:
        chat = unit.messages.list(conversation_id=conversation.id)
    assert [(m.role, m.content) for m in chat] == [
        ("user", "Lembre que prefiro respostas curtas."),
        ("assistant", "Anotado: respostas curtas."),
    ]


def test_recommended_tools_upgrade_an_outdated_computer_bundle(state):
    from bees_core.agent_tools.catalog import BUNDLES, manifest_for
    from bees_core.tools import ToolService

    client, agent, _ = state
    bundle = next(item for item in BUNDLES if item["plugin_key"] == "bees.computer")
    # Instalação anterior ao reinício: manifesto imutável sem computer.restart.
    outdated = bundle | {
        "version": "1.0.0",
        "tools": tuple(name for name in bundle["tools"] if name != "computer.restart"),
    }
    tools = ToolService(client.app.state.database)
    old = tools.install({"client_request_id": uuid4(), "manifest": manifest_for(outdated)})
    # A pessoa desligou o terminal na versão antiga: a atualização não religa.
    tools.set_grant(
        agent.id, old.id, "computer.shell.run", {"expected_revision": 0, "enabled": False}
    )
    base = f"/api/v1/agents/{agent.id}"
    response = client.post(
        f"{base}/agent/recommended-tools",
        json={"client_request_id": str(uuid4())},
        headers=headers(client),
    )
    assert response.status_code == 200, response.text
    assert "computer.restart" in response.json()["granted"]
    assert client.get(f"{base}/agent/capabilities").json()["recommended_installed"]
    granted = {row["tool_name"] for row in tools.list_tools(agent.id, limit=1000) if row["granted"]}
    assert {"computer.restart", "computer.browser.open"} <= granted
    assert "computer.shell.run" not in granted
    assert len(tools.list_plugins(limit=100)) == len(BUNDLES) + 1


def test_agent_task_requires_tool_capable_model(state):
    client, agent, _ = state
    with client.app.state.store.transaction() as unit:
        current = unit.agents.get(agent.id)
        config = ProviderConfig(
            kind="openai_compatible",
            endpoint="https://fixture.invalid/v1",
            model="fixture-text",
            capabilities={"text": True},
        )
        unit.agents.update(
            current.model_copy(update={"provider_config": config.model_dump(mode="json")}),
            current.revision,
        )
    response = client.post(
        f"/api/v1/agents/{agent.id}/agent-tasks",
        json={"client_request_id": str(uuid4()), "title": "x", "objective": "y"},
        headers=headers(client),
    )
    assert response.status_code == 422
