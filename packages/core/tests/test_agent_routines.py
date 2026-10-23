"""Skill com ferramentas do registro e rotina em modo agente (agenda e manual)."""

import asyncio
import json
from datetime import timedelta
from uuid import uuid4

import httpx
import pytest
from test_agent_tools import PAGE, public, tool_calls
from test_tasks_execution import answer, fixture_state

from bees_core.agent_tools.cycle import AgentTaskWorker
from bees_core.agent_tools.web import WebClient
from bees_core.models import Conversation
from bees_core.providers.contracts import ProviderConfig
from bees_core.routine_manual import ManualRoutineInput, ManualRoutineService
from bees_core.routine_scheduler import RoutineScheduler
from bees_core.routines import CreateRoutine, RoutineDefinition, RoutineService
from bees_core.skill_context import SkillTaskInput
from bees_core.skill_tasks import AgentSkillTaskService, SkillTaskService
from bees_core.skills import CreateSkill, EnableSkill, SkillDefinition, SkillService
from bees_core.tasks import TaskError
from bees_core.tools import ToolService


def page(request):
    return httpx.Response(200, text=PAGE, headers={"content-type": "text/html"})


@pytest.fixture
def setup(tmp_path):
    config = ProviderConfig(
        kind="openai_compatible",
        endpoint="https://fixture.test/v1",
        model="fixture-agent",
        capabilities={"text": True, "tool_calls": True},
    )
    database, store, agent, _, _ = fixture_state(tmp_path, config=config)
    tools = ToolService(database)
    for manifest in tools.catalog()[1:]:
        plugin = tools.install({"client_request_id": uuid4(), "manifest": manifest})
        tools.update_plugin(plugin.id, {"expected_revision": 1, "enabled": True})
        for tool in manifest["tools"]:
            tools.set_grant(
                agent.id, plugin.id, tool["tool_name"], {"expected_revision": 0, "enabled": True}
            )
    with store.transaction() as unit:
        destination = unit.conversations.create(Conversation(agent_id=agent.id))
    skills = SkillService(database)
    skill = skills.create(
        agent.id,
        CreateSkill(
            client_request_id=uuid4(),
            definition=SkillDefinition(
                name="Resumo de notícias",
                purpose="Ler uma fonte e resumir.",
                inputs=[{"name": "url", "description": "Fonte"}],
                steps=[{"title": "Ler", "instruction": "Leia a fonte e resuma em 3 linhas."}],
                resources={"environment": "none", "tools": ["web.fetch"]},
                output="Resumo com fonte.",
                completion="Fonte citada.",
            ),
        ),
    )
    skills.enable(
        agent.id,
        skill.id,
        EnableSkill(client_request_id=uuid4(), expected_revision=1, version=1, enabled=True),
    )
    return database, store, agent, destination, skill


def worker(database, script):
    return AgentTaskWorker(
        database,
        transport=httpx.MockTransport(lambda request: script.pop(0)),
        web=WebClient(transport=httpx.MockTransport(page), resolver=public),
    )


def drain(work):
    for _ in range(20):
        if not asyncio.run(work.run_once()):
            return


def test_agent_skill_only_offers_declared_tools_and_text_path_refuses(setup):
    database, store, agent, _, skill = setup
    # O coordenador de texto recusa a skill antes de criar qualquer tarefa.
    with store.transaction() as unit, pytest.raises(TaskError, match="unsupported_capability"):
        SkillTaskService.create_skill_in_unit(
            None,
            unit,
            agent.id,
            SkillTaskInput(
                client_request_id=uuid4(),
                skill_id=skill.id,
                skill_version=1,
                inputs={"url": "https://example.org/a"},
            ),
        )
    task = AgentSkillTaskService(database).create(
        agent.id,
        {
            "client_request_id": uuid4(),
            "skill_id": skill.id,
            "skill_version": 1,
            "inputs": {"url": "https://example.org/a"},
        },
    )
    requests = []
    script = [
        tool_calls(("f1", "web_fetch", {"url": "https://example.org/a"})),
        answer("Resumo: abelhas polinizam. Fonte: https://example.org/a"),
    ]

    def respond(request):
        requests.append(json.loads(request.content))
        return script.pop(0)

    work = AgentTaskWorker(
        database,
        transport=httpx.MockTransport(respond),
        web=WebClient(transport=httpx.MockTransport(page), resolver=public),
    )
    drain(work)
    detail = AgentSkillTaskService(database).detail(agent.id, task.id)
    assert detail.task.status == "completed"
    assert [tool["function"]["name"] for tool in requests[0]["tools"]] == ["web_fetch"]
    assert "Leia a fonte" in json.dumps(requests[0]["messages"], ensure_ascii=False)


def test_agent_routine_scheduled_and_manual_deliver_to_destination(setup):
    from test_routines import instant

    database, store, agent, destination, skill = setup
    now = instant("2026-01-01T09:00:15+00:00")
    routines = RoutineService(database, clock=lambda: now - timedelta(hours=1))
    definition = RoutineDefinition(
        name="Resumo diário",
        instructions="Resuma a fonte.",
        schedule={"frequency": "daily", "time": "09:00"},
        timezone="UTC",
        skill_id=skill.id,
        skill_version=1,
        inputs={"url": "https://example.org/a"},
        environment="none",
        destination={"conversation_id": destination.id},
        notification="always",
        execution_mode="agent",
    )
    routine = routines.create(
        agent.id, CreateRoutine(client_request_id=uuid4(), definition=definition, active=True)
    )
    assert routine.definition.runtime_binding is None
    scheduler = RoutineScheduler(database, clock=lambda: now)
    assert scheduler.tick() >= 1
    occurrence = next(x for x in routines.occurrences(agent.id, routine.id) if x.task_id)
    drain(
        worker(
            database,
            [
                tool_calls(("f1", "web_fetch", {"url": "https://example.org/a"})),
                answer("Resumo diário pronto."),
            ],
        )
    )
    task = AgentSkillTaskService(database).detail(agent.id, occurrence.task_id).task
    assert task.status == "completed" and task.metadata["execution_kind"] == "agent_task"
    scheduler.synchronize(now=now)
    with store.transaction(write=False) as unit:
        delivered = [
            m.content
            for m in unit.messages.list(conversation_id=destination.id)
            if m.role == "assistant"
        ]
    assert "Resumo diário pronto." in delivered
    manual = ManualRoutineService(database, clock=lambda: now + timedelta(minutes=5))
    receipt = manual.execute(
        agent.id,
        routine.id,
        ManualRoutineInput(client_request_id=uuid4(), expected_revision=routine.revision),
    )
    assert receipt.runtime_binding is None and receipt.occurrence.task_id
    drain(worker(database, [answer("Execução manual concluída.")]))
    assert (
        AgentSkillTaskService(database).detail(agent.id, receipt.occurrence.task_id).task.status
        == "completed"
    )


def test_agent_routine_without_news_stays_quiet(setup):
    from test_routines import instant

    database, store, agent, destination, skill = setup
    now = instant("2026-01-02T09:00:15+00:00")
    routines = RoutineService(database, clock=lambda: now - timedelta(hours=1))
    routine = routines.create(
        agent.id,
        CreateRoutine(
            client_request_id=uuid4(),
            definition=RoutineDefinition(
                name="Radar",
                instructions="Avise só se houver novidade.",
                schedule={"frequency": "daily", "time": "09:00"},
                timezone="UTC",
                skill_id=skill.id,
                skill_version=1,
                inputs={"url": "https://example.org/a"},
                environment="none",
                destination={"conversation_id": destination.id},
                notification="always",
                execution_mode="agent",
            ),
            active=True,
        ),
    )
    scheduler = RoutineScheduler(database, clock=lambda: now)
    scheduler.tick()
    requests = []

    def respond(request):
        requests.append(json.loads(request.content))
        return answer("SEM_NOVIDADES")

    drain(AgentTaskWorker(database, transport=httpx.MockTransport(respond)))
    assert "SEM_NOVIDADES" in json.dumps(requests[0]["messages"], ensure_ascii=False)
    scheduler.synchronize(now=now)
    occurrence = next(x for x in routines.occurrences(agent.id, routine.id) if x.task_id)
    task = AgentSkillTaskService(database).detail(agent.id, occurrence.task_id).task
    assert task.status == "completed"
    with store.transaction(write=False) as unit:
        assistant = [
            m for m in unit.messages.list(conversation_id=destination.id) if m.role == "assistant"
        ]
    assert assistant == []
