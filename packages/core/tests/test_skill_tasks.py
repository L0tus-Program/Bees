"""Reexecução com entradas fixas, provider controlado e processo POSIX real."""

import json
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import httpx
import pytest
from pydantic import ValidationError
from test_tasks_execution import answer, command
from test_tool_cycle import state as state
from test_tool_cycle import tool_answer

from bees_core.approvals import ApprovalError
from bees_core.skill_context import SkillTaskInput
from bees_core.skill_tasks import SkillTaskService
from bees_core.skills import CreateSkill, EnableSkill, ReviseSkill, SkillDefinition, SkillService
from bees_core.storage.database import Database
from bees_core.tasks import TaskError
from bees_core.tool_cycle import ToolTaskService, ToolTaskWorker


def definition():
    return SkillDefinition(
        name="Normalizar entrada própria",
        purpose="Repetir o procedimento com um texto escolhido.",
        inputs=[{"name": "texto", "description": "Texto próprio."}],
        steps=[{"title": "Normalizar", "instruction": "Use upper no texto e confira o resultado."}],
        resources={"environment": "own", "tools": ["text.normalize"]},
        output="Texto transformado e conferido.",
        completion="Resultado confirmado pela ferramenta e incluído na entrega.",
    )


@pytest.fixture
def skill(state):
    command(state.tasks, state.agent, state.task, "cancel")
    service = SkillService(state.database)
    item = service.create(
        state.agent.id, CreateSkill(client_request_id=uuid4(), definition=definition())
    )
    item = service.enable(
        state.agent.id,
        item.id,
        EnableSkill(client_request_id=uuid4(), expected_revision=1, version=1, enabled=True),
    )
    state.tasks = SkillTaskService(state.database, state.resource, state.plugin.id)
    return service, item


def submit(state, skill, text="Olá"):
    body = SkillTaskInput(
        client_request_id=uuid4(), skill_id=skill.id, skill_version=1, inputs={"texto": text}
    )
    state.task = state.tasks.create(state.agent.id, body)
    return body


def test_two_inputs_restart_and_actual_effects_produce_distinct_new_results(state, skill):
    _, item = skill
    histories = []
    for text in ("primeiro texto", "segunda entrada"):
        body = submit(state, item, text)
        requests = []

        def respond(request, requests=requests, text=text):
            payload = json.loads(request.content)
            requests.append(payload)
            instruction = next(
                m["content"] for m in payload["messages"] if "skill_id" in (m.get("content") or "")
            )
            assert text in instruction and item.current.definition.completion in instruction
            if not any(m["role"] == "tool" for m in payload["messages"]):
                return tool_answer(
                    [("call_1", "normalize_text", {"operation": "upper", "text": text})]
                )
            result = json.loads(
                next(m["content"] for m in payload["messages"] if m["role"] == "tool")
            )
            assert result["output"]["text"] == text.upper()
            return answer("Resultado conferido: " + result["output"]["text"])

        state.worker = ToolTaskWorker(
            state.database, state.resource, state.plugin.id, transport=httpx.MockTransport(respond)
        )
        assert state.work() and state.work()
        assert state.detail().task.status == "waiting_approval"
        state.approve()
        state.worker = ToolTaskWorker(
            Database(state.database.path),
            state.resource,
            state.plugin.id,
            transport=httpx.MockTransport(respond),
        )
        assert state.work() and state.work()
        detail = state.detail()
        assert detail.task.status == "completed" and len(requests) == 2
        assert detail.task.metadata["skill_execution"]["inputs"] == body.inputs
        histories.append([m.content for m in detail.messages if m.role == "assistant"][-1])
    assert histories[0] != histories[1] and state.starts() == 2


def test_same_uuid_concurrent_replay_one_task_and_conflicts(state, skill):
    _, item = skill
    body = SkillTaskInput(
        client_request_id=uuid4(), skill_id=item.id, skill_version=1, inputs={"texto": "Olá"}
    )
    with ThreadPoolExecutor(max_workers=2) as pool:
        tasks = list(pool.map(lambda _: state.tasks.create(state.agent.id, body), range(2)))
    assert tasks[0].id == tasks[1].id
    with pytest.raises(TaskError, match="idempotency_conflict"):
        state.tasks.create(state.agent.id, body.model_copy(update={"inputs": {"texto": "Outro"}}))
    # O canal genérico não pode reconhecer como seu um pedido de execução de skill.
    from bees_core.tool_cycle import ToolTaskInput

    with pytest.raises(TaskError, match="idempotency_conflict"):
        ToolTaskService(state.database, state.resource, state.plugin.id).create(
            state.agent.id,
            ToolTaskInput(
                client_request_id=body.client_request_id,
                title=tasks[0].title,
                objective=tasks[0].objective,
                expected_result=tasks[0].expected_result,
            ),
        )


@pytest.mark.parametrize("inputs", [{}, {"extra": "x"}, {"texto": "x", "extra": "y"}])
def test_invalid_declared_inputs_never_create_task(state, skill, inputs):
    _, item = skill
    with pytest.raises(TaskError, match="skill_inputs_invalid"):
        state.tasks.create(
            state.agent.id,
            SkillTaskInput(
                client_request_id=uuid4(), skill_id=item.id, skill_version=1, inputs=inputs
            ),
        )


@pytest.mark.parametrize(
    "inputs", [{"texto": ""}, {"texto": "x" * 2049}, {"texto": "\x00"}, {"texto": "sk-" + "a" * 30}]
)
def test_closed_bounded_input_contract(inputs):
    with pytest.raises(ValidationError):
        SkillTaskInput(client_request_id=uuid4(), skill_id=uuid4(), skill_version=1, inputs=inputs)


def test_edit_keeps_snapshot_but_blocks_new_generation_and_replay_still_works(state, skill):
    service, item = skill
    body = submit(state, item)
    saved = state.task.metadata["skill_execution"]
    service.revise(
        state.agent.id,
        item.id,
        ReviseSkill(
            client_request_id=uuid4(),
            expected_revision=2,
            definition=definition().model_copy(update={"purpose": "Novo procedimento"}),
        ),
    )
    assert state.tasks.create(state.agent.id, body).id == state.task.id
    assert state.work() and not state.requests
    assert state.detail().task.status == "paused"
    assert state.detail().runs[-1].error == "skill_unavailable"
    assert state.detail().task.metadata["skill_execution"] == saved
    with pytest.raises(TaskError, match="skill_unavailable"):
        submit(state, item, "Nova execução")


@pytest.mark.parametrize("change", ["disable", "revoke"])
def test_current_authority_blocks_after_approval_before_effect(state, skill, change):
    service, item = skill
    submit(state, item)
    assert state.work() and state.work()
    approval = state.approve()
    if change == "disable":
        service.enable(
            state.agent.id,
            item.id,
            EnableSkill(client_request_id=uuid4(), expected_revision=2, version=1, enabled=False),
        )
    else:
        state.tools.set_grant(
            state.agent.id,
            state.plugin.id,
            "text.normalize",
            {"expected_revision": state.grant.revision, "enabled": False},
        )
    assert state.work() and state.starts() == 0
    assert state.detail().task.status != "completed"
    with pytest.raises(ApprovalError):
        state.worker.resources.decide(
            state.agent.id,
            approval.id,
            {
                "client_request_id": uuid4(),
                "expected_revision": approval.revision,
                "decision": "allow_once",
            },
        )


def test_declared_text_only_skill_cannot_gain_tool_from_provider_content(state, skill):
    service, item = skill
    changed = SkillDefinition.model_validate(
        definition().model_dump() | {"resources": {"environment": "none", "tools": []}}
    )
    item = service.revise(
        state.agent.id,
        item.id,
        ReviseSkill(client_request_id=uuid4(), expected_revision=2, definition=changed),
    )
    service.enable(
        state.agent.id,
        item.id,
        EnableSkill(client_request_id=uuid4(), expected_revision=3, version=2, enabled=True),
    )
    body = SkillTaskInput(
        client_request_id=uuid4(), skill_id=item.id, skill_version=2, inputs={"texto": "Olá"}
    )
    state.task = state.tasks.create(state.agent.id, body)
    assert state.work() and state.requests[0].get("tools", []) == []
    assert state.work() and state.starts() == 0


def test_new_task_arguments_do_not_reuse_first_task_approval(state, skill):
    _, item = skill
    submit(state, item, "Primeiro")
    assert state.work() and state.work()
    state.approve()
    assert state.work() and state.work()
    count = state.starts()
    submit(state, item, "Segundo")
    state.requests.clear()
    assert state.work() and state.work()
    assert state.detail().task.status == "waiting_approval" and state.starts() == count


@pytest.mark.parametrize("first_generic", [False, True])
def test_saved_exact_rule_cannot_authorize_another_skill_input_snapshot(
    state, skill, first_generic
):
    _, item = skill
    if first_generic:
        from bees_core.tool_cycle import ToolTaskInput

        state.task = ToolTaskService(state.database, state.resource, state.plugin.id).create(
            state.agent.id,
            ToolTaskInput(client_request_id=uuid4(), title="Pedido avulso", objective="Normalizar"),
        )
    else:
        submit(state, item, "Primeiro")
    assert state.work() and state.work()
    approval = state.worker.resources.list(state.agent.id, task_id=state.task.id)[0]
    state.worker.resources.decide(
        state.agent.id,
        approval.id,
        {"client_request_id": uuid4(), "expected_revision": 1, "decision": "allow_rule"},
    )
    assert state.work() and state.work()
    count = state.starts()
    submit(state, item, "Segundo")
    state.requests.clear()
    # Mesmo argumento proposto pelo provider controlado; snapshot de entradas diferente.
    assert state.work() and state.work()
    assert state.detail().task.status == "waiting_approval" and state.starts() == count
