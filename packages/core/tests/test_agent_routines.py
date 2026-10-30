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

    from bees_core.attention import AttentionService

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
    assert not [
        notice
        for notice in AttentionService(database).list(agent.id).items
        if notice.task_id == task.id and notice.kind == "task_completed"
    ]


def scheduled_case(setup):
    from test_routines import instant

    database, _, agent, destination, skill = setup
    now = instant("2026-01-02T09:00:15+00:00")
    routines = RoutineService(database, clock=lambda: now - timedelta(hours=1))
    routine = routines.create(
        agent.id,
        CreateRoutine(
            client_request_id=uuid4(),
            active=True,
            definition=RoutineDefinition(
                name="Radar",
                instructions="Avise somente com novidade.",
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
        ),
    )
    scheduler = RoutineScheduler(database, clock=lambda: now)
    scheduler.tick()
    task_id = next(o.task_id for o in routines.occurrences(agent.id, routine.id) if o.task_id)
    return routines, routine, scheduler, now, task_id


@pytest.mark.parametrize("source", ["same", "delayed", "changed", "none", "revised", "controlled"])
def test_routine_attention_and_chat_agree_on_verified_source_changes(setup, source):
    from bees_core.attention import AttentionService

    database, store, agent, destination, _ = setup
    routines, routine, scheduler, now, first_id = scheduled_case(setup)
    ids = [first_id]
    for index in range(2):
        if index:
            if source == "delayed":
                # Completion is visible before the previous worker publishes
                # research metadata. Confirmed source actions remain durable.
                with store.transaction() as unit:
                    unit.agents._context.connection.execute(
                        "UPDATE runs SET checkpoint_json=json_remove(checkpoint_json,'$.research') "
                        "WHERE task_id=?",
                        (str(ids[0]),),
                    )
            current = routines.get(agent.id, routine.id)
            if source == "revised":
                from bees_core.routines import ReviseRoutine

                current = routines.revise(
                    agent.id,
                    routine.id,
                    ReviseRoutine(
                        client_request_id=uuid4(),
                        expected_revision=current.revision,
                        definition=current.definition.model_copy(
                            update={"instructions": "Nova análise das mesmas fontes."}
                        ),
                    ),
                )
            receipt = ManualRoutineService(
                database, clock=lambda: now + timedelta(minutes=5)
            ).execute(
                agent.id,
                routine.id,
                ManualRoutineInput(client_request_id=uuid4(), expected_revision=current.revision),
            )
            ids.append(receipt.occurrence.task_id)
        with store.transaction() as unit:
            task = unit.tasks.get(ids[-1])
            unit.tasks.update(
                task.model_copy(
                    update={
                        "metadata": task.metadata | {"research": True},
                        "control_revision": 1 if source == "controlled" and index else 0,
                    }
                ),
                task.revision,
            )
        script = [answer(f"Relatório {index}. Fonte: https://example.org/a")]
        if source != "none":
            script.insert(0, tool_calls(("read", "web_fetch", {"url": "https://example.org/a"})))
        content = PAGE.replace("75%", "80%") if source == "changed" and index else PAGE
        work = AgentTaskWorker(
            database,
            transport=httpx.MockTransport(lambda request, script=script: script.pop(0)),
            web=WebClient(
                transport=httpx.MockTransport(
                    lambda request, content=content: httpx.Response(
                        200, text=content, headers={"content-type": "text/html"}
                    )
                ),
                resolver=public,
            ),
        )
        drain(work)
        scheduler.synchronize(now=now + timedelta(minutes=5 * index))
        assert AgentSkillTaskService(database).detail(agent.id, ids[-1]).task.status == "completed"
    quiet_ids = {
        item.task_id
        for item in AttentionService(database).list(agent.id).items
        if item.kind == "task_completed"
    }
    assert ids[0] in quiet_ids
    assert (ids[1] not in quiet_ids) == (source in {"same", "delayed"})
    with store.transaction(write=False) as unit:
        from bees_core.routine_results import quiet_completion

        # A subsequent completion must not retroactively silence the first.
        first = unit.tasks.get(ids[0])
        assert not quiet_completion(
            unit.agents._context.connection, first, unit.runs.list(task_id=first.id)[-1].id
        )
        if source == "same":
            latest = unit.tasks.get(ids[1])
            malformed = latest.model_copy(
                update={"metadata": latest.metadata | {"routine_execution": {"revision": True}}}
            )
            assert not quiet_completion(
                unit.agents._context.connection,
                malformed,
                unit.runs.list(task_id=latest.id)[-1].id,
            )
        delivered = [
            m for m in unit.messages.list(conversation_id=destination.id) if m.role == "assistant"
        ]
        assert len(delivered) == (1 if source in {"same", "delayed"} else 2)
        assert all(unit.tasks.get(task_id).status == "completed" for task_id in ids)
        assert len(unit.runs.list(task_id=ids[1])) == 1


def test_quiet_routine_result_does_not_hide_a_later_empty_response(setup):
    from bees_core.attention import AttentionService

    database, _, agent, _, _ = setup
    routines, routine, scheduler, now, first_id = scheduled_case(setup)
    drain(worker(database, [answer("SEM_NOVIDADES")]))
    scheduler.synchronize(now=now)
    receipt = ManualRoutineService(database, clock=lambda: now + timedelta(minutes=5)).execute(
        agent.id,
        routine.id,
        ManualRoutineInput(client_request_id=uuid4(), expected_revision=routine.revision),
    )

    drain(worker(database, [answer("")]))
    failed = AgentSkillTaskService(database).detail(agent.id, receipt.occurrence.task_id)
    assert failed.task.status == "paused" and failed.runs[-1].error == "empty_response"
    notices = AttentionService(database).list(agent.id).items
    assert not [i for i in notices if i.task_id == first_id and i.kind == "task_completed"]
    assert [
        i
        for i in notices
        if i.task_id == receipt.occurrence.task_id
        and i.kind == "task_failed"
        and i.requires_attention
    ]


def test_quiet_marker_in_an_ordinary_task_is_not_silenced(tmp_path):
    from test_agent_tools import Case

    from bees_core.attention import AttentionService

    case = Case(tmp_path, [answer("SEM_NOVIDADES")])
    case.drain()
    assert case.detail().task.status == "completed"
    assert [
        i
        for i in AttentionService(case.database).list(case.agent.id).items
        if i.task_id == case.task.id and i.kind == "task_completed"
    ]


@pytest.mark.parametrize("binding", [None, {"revision": True}, {"revision": "1"}])
def test_missing_or_malformed_routine_revision_cannot_silence_another_task(binding):
    from bees_core.agent_tools.research import relevance_key
    from bees_core.models import Task

    task = Task(
        agent_id=uuid4(),
        routine_id=uuid4(),
        title="Legacy",
        objective="Check data",
        metadata={"routine_execution": binding},
    )
    other = task.model_copy(update={"id": uuid4()})
    assert relevance_key(task) != relevance_key(other)
    assert relevance_key(task) == relevance_key(task.model_copy())


def test_human_controlled_routine_marker_still_creates_a_completion_notice(setup):
    from bees_core.attention import AttentionService

    database, store, agent, destination, _ = setup
    _, _, scheduler, now, task_id = scheduled_case(setup)
    with store.transaction() as unit:
        task = unit.tasks.get(task_id)
        unit.tasks.update(task.model_copy(update={"control_revision": 1}), task.revision)
    drain(worker(database, [answer("SEM_NOVIDADES")]))
    scheduler.synchronize(now=now)
    assert [
        i
        for i in AttentionService(database).list(agent.id).items
        if i.task_id == task_id and i.kind == "task_completed"
    ]
    with store.transaction(write=False) as unit:
        assert [
            m.content
            for m in unit.messages.list(conversation_id=destination.id)
            if m.role == "assistant"
        ] == ["SEM_NOVIDADES"]
