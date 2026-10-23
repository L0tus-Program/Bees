"""Equipe de abelhas: consulta entre abelhas e delegação com resultado na conversa de origem."""

import asyncio
import json
from uuid import uuid4

import httpx
import pytest
from test_agent_tools import tool_calls
from test_tasks_execution import answer, fixture_state

from bees_core.agent_tools.chat import submit_agent_message
from bees_core.agent_tools.cycle import AgentTaskService, AgentTaskWorker
from bees_core.agent_tools.service import AgentToolDecision, AgentToolService
from bees_core.models import Agent, Conversation
from bees_core.providers.contracts import ProviderConfig
from bees_core.tools import ToolService

CONFIG = ProviderConfig(
    kind="openai_compatible",
    endpoint="https://fixture.test/v1",
    model="fixture-agent",
    capabilities={"text": True, "tool_calls": True},
)


def grant_all(database, *agents):
    tools = ToolService(database)
    for manifest in tools.catalog()[1:]:
        plugin = tools.install({"client_request_id": uuid4(), "manifest": manifest})
        plugin = tools.update_plugin(plugin.id, {"expected_revision": 1, "enabled": True})
        for agent in agents:
            for tool in manifest["tools"]:
                tools.set_grant(
                    agent.id,
                    plugin.id,
                    tool["tool_name"],
                    {"expected_revision": 0, "enabled": True},
                )


@pytest.fixture
def team(tmp_path):
    database, store, coordinator, chat, _ = fixture_state(tmp_path, config=CONFIG)
    specialist = Agent(
        name="Pesquisadora",
        purpose="Pesquisa de mercado",
        provider_config=CONFIG.model_dump(mode="json"),
    )
    specialist_chat = Conversation(agent_id=specialist.id)
    with store.transaction() as unit:
        unit.agents.create(specialist)
        unit.conversations.create(specialist_chat)
    grant_all(database, coordinator, specialist)
    return database, store, coordinator, chat, specialist, specialist_chat


def worker(database, script, requests):
    def respond(request):
        requests.append(json.loads(request.content))
        return script.pop(0)

    return AgentTaskWorker(database, transport=httpx.MockTransport(respond))


def drain(work):
    for _ in range(30):
        if not asyncio.run(work.run_once()):
            return


def ask(database, store, agent, chat, text):
    with store.transaction() as unit:
        return submit_agent_message(database, unit, agent.id, chat.id, text, uuid4())


def test_coordinator_consults_specialist_and_answers(team):
    database, store, coordinator, chat, specialist, _ = team
    receipt = ask(database, store, coordinator, chat, "Qual o tamanho do mercado de mel?")
    requests = []
    # A consulta à especialista é uma geração própria intercalada no roteiro.
    script = [
        tool_calls(("t1", "bees_list", {})),
        tool_calls(("t2", "bees_ask", {"bee": "pesquisadora", "question": "Mercado de mel?"})),
        answer("Cerca de 10 bilhões de dólares."),
        answer("Segundo a Pesquisadora: cerca de 10 bilhões."),
    ]
    drain(worker(database, script, requests))
    names = {tool["function"]["name"] for tool in requests[0]["tools"]}
    assert {"bees_list", "bees_ask", "bees_delegate"} <= names
    assert "Pesquisadora" in json.dumps(requests[1]["messages"], ensure_ascii=False)
    consult_prompt = json.dumps(requests[2]["messages"], ensure_ascii=False)
    assert "Mercado de mel?" in consult_prompt and requests[2].get("tools") in (None, [])
    task = AgentTaskService(database).detail(coordinator.id, receipt["task_id"]).task
    assert task.status == "completed"
    with store.transaction(write=False) as unit:
        consults = [
            c
            for c in unit.conversations.list(agent_id=specialist.id, limit=10)
            if c.metadata.get("consult")
        ]
    assert len(consults) == 1


def test_delegation_requires_approval_and_result_returns_to_origin(team):
    database, store, coordinator, chat, specialist, specialist_chat = team
    ask(database, store, coordinator, chat, "Peça à Pesquisadora um relatório de mel")
    requests = []
    script = [
        tool_calls(("d1", "bees_delegate", {"bee": "Pesquisadora", "task": "Relatório sobre mel"})),
    ]
    work = worker(database, script, requests)
    drain(work)
    pending = AgentToolService(database).list(coordinator.id, status="pending")
    assert len(pending) == 1 and pending[0]["tool_name"] == "bees.delegate"
    AgentToolService(database).decide(
        coordinator.id,
        pending[0]["id"],
        AgentToolDecision(
            client_request_id=uuid4(),
            expected_revision=pending[0]["revision"],
            decision="allow_once",
        ),
    )
    script += [
        answer("Deleguei à Pesquisadora; o relatório chegará aqui."),
        answer("Relatório: o mercado de mel cresce 5% ao ano."),
    ]
    drain(work)
    assert script == []
    with store.transaction(write=False) as unit:
        origin = [m.content for m in unit.messages.list(conversation_id=chat.id)]
        target = [m.content for m in unit.messages.list(conversation_id=specialist_chat.id)]
        delegated = [
            t
            for t in unit.tasks.list(agent_id=specialist.id, limit=10)
            if t.metadata.get("delegated_from")
        ]
    assert len(delegated) == 1 and delegated[0].status == "completed"
    assert any("Relatório: o mercado de mel" in text for text in target)
    assert any(text.startswith("Resultado da abelha Pesquisadora") for text in origin)


def test_unknown_bee_fails_without_effect(team):
    database, store, coordinator, chat, _, _ = team
    ask(database, store, coordinator, chat, "Pergunte ao Zé")
    requests = []
    script = [
        tool_calls(("x1", "bees_ask", {"bee": "Zé", "question": "oi"})),
        answer("Não há abelha Zé."),
    ]
    drain(worker(database, script, requests))
    assert "bee_not_found" in json.dumps(requests[1]["messages"], ensure_ascii=False)
    assert "Pesquisadora" in json.dumps(requests[1]["messages"], ensure_ascii=False)
