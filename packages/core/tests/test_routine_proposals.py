"""Sugestão declarativa sem autoridade, fonte privada e confirmação humana."""

import asyncio
import json
from uuid import uuid4

import pytest
from test_delegation import state as chat_state
from test_delegation import text_answer
from test_routines import state as state

from bees_core.models import Message
from bees_core.providers.errors import ProviderError
from bees_core.routine_proposals import (
    PROPOSAL_INSTRUCTION,
    RoutineProposalService,
    parse_suggestion,
)
from bees_core.routines import CreateRoutine, SetRoutineStatus
from bees_core.storage.store import IntegrityError, NotFoundError, RevisionConflict, StateStore


def suggestion(**updates):
    value = {
        "name": "Resumo sugerido",
        "instructions": "Conferir fontes sem assumir permissões.",
        "schedule": {"frequency": "daily", "time": "09:00", "weekdays": []},
        "timezone": "America/Sao_Paulo",
        "inputs": {"tema": "Ciência"},
        "notification": "always",
        "grace_seconds": 60,
    } | updates
    return (
        "Sugestão para revisão humana.\n```bees-routine\n"
        + json.dumps(value, ensure_ascii=False)
        + "\n```"
    )


def message(state, *, role="assistant", content=None):
    db, _, _, _, definition, _ = state
    with StateStore(db).transaction() as unit:
        return unit.messages.create(
            Message(
                conversation_id=definition.destination.conversation_id,
                role=role,
                content=content or suggestion(),
            )
        )


def test_read_proposal_is_private_and_has_no_task_routine_command_grant_or_notice(state):
    db, agent, other, _, definition, _ = state
    msg = message(state)
    service = RoutineProposalService(db)
    with StateStore(db).transaction(write=False) as unit:
        tables = (
            "routines",
            "routine_commands",
            "routine_occurrences",
            "tasks",
            "tool_grants",
            "messages",
        )
        before = [
            unit.agents._context.connection.execute(f"SELECT count(*) FROM {t}").get for t in tables
        ]
    result = service.get(agent.id, msg.conversation_id, msg.id)
    assert result.proposal.timezone == "America/Sao_Paulo"
    assert result.source.message_id == msg.id and len(result.source.content_hash) == 64
    with StateStore(db).transaction(write=False) as unit:
        after = [
            unit.agents._context.connection.execute(f"SELECT count(*) FROM {t}").get for t in tables
        ]
    assert before == after
    for a, c, m in (
        (other.id, msg.conversation_id, msg.id),
        (agent.id, uuid4(), msg.id),
        (agent.id, msg.conversation_id, uuid4()),
    ):
        with pytest.raises(NotFoundError):
            service.get(a, c, m)
    user = message(state, role="user")
    with pytest.raises(NotFoundError):
        service.get(agent.id, user.conversation_id, user.id)


@pytest.mark.parametrize(
    "extra",
    [
        {"active": True},
        {"execution_mode": "scheduled"},
        {"grants": ["allow"]},
        {"skill_id": str(uuid4())},
        {"environment": "personal"},
        {"destination": {}},
        {"timezone": "Invalid/Zone"},
        {"grace_seconds": True},
        {"notification": "always\n"},
        {"inputs": {"texto": "sk-or-v1-" + "x" * 30}},
    ],
)
def test_source_rejects_authority_extra_fields_bad_timezone_and_secret(extra):
    with pytest.raises(ValueError):
        parse_suggestion(suggestion(**extra))


def test_duplicate_keys_multiple_blocks_non_json_and_unbounded_nesting_are_rejected():
    for content in (
        suggestion() + "\n" + suggestion(),
        suggestion().replace('"name":', '"name":"forged","name":', 1),
        "```bees-routine\n" + "[" * 1100 + "0" + "]" * 1100 + "\n```",
        "```bees-routine\nnot json\n```",
        "```json\n{}\n```",
        None,
    ):
        with pytest.raises(ValueError):
            parse_suggestion(content)


def test_human_creation_is_paused_records_source_and_replays_after_source_changes(state):
    db, agent, _, _, definition, routines = state
    msg = message(state)
    source = RoutineProposalService(db).get(agent.id, msg.conversation_id, msg.id).source
    command = CreateRoutine(
        client_request_id=uuid4(), definition=definition, proposal_source=source
    )
    saved = routines.create(agent.id, command)
    assert saved.status == "paused" and saved.definition.execution_mode == "prepare"
    assert saved.metadata["proposal_source"] == source.model_dump(mode="json")
    with StateStore(db).transaction() as unit, pytest.raises(IntegrityError):
        unit.messages.update(
            msg.model_copy(update={"content": suggestion(name="Alterada")}), msg.revision
        )
    routines.set_status(
        agent.id,
        saved.id,
        SetRoutineStatus(client_request_id=uuid4(), expected_revision=1, status="cancelled"),
    )
    assert routines.create(agent.id, command) == saved
    with pytest.raises(RevisionConflict):
        routines.create(
            agent.id,
            command.model_copy(
                update={
                    "client_request_id": uuid4(),
                    "proposal_source": source.model_copy(update={"content_hash": "0" * 64}),
                }
            ),
        )
    with pytest.raises(ValueError):
        CreateRoutine(
            client_request_id=uuid4(), definition=definition, proposal_source=source, active=True
        )
    with StateStore(db).transaction(write=False) as unit:
        assert unit.agents._context.connection.execute("SELECT count(*) FROM tasks").get == 0


def test_proposal_context_is_explicit_text_only_and_replay_does_not_generate(tmp_path):
    requests = []

    def respond(request):
        requests.append(json.loads(request.content))
        return text_answer(suggestion())

    db, store, agent, conversation, service = chat_state(tmp_path, respond, tool_calls=False)
    request_id = uuid4()
    kwargs = {"allow_routine_proposal": True, "client_request_id": request_id}
    first = asyncio.run(service.chat(agent.id, conversation.id, "Sugira uma agenda.", **kwargs))
    assert (
        asyncio.run(service.chat(agent.id, conversation.id, "Sugira uma agenda.", **kwargs))
        == first
    )
    assert len(requests) == 1 and not requests[0].get("tools")
    assert any(
        m["role"] == "system" and m["content"] == PROPOSAL_INSTRUCTION
        for m in requests[0]["messages"]
    )
    with pytest.raises(ProviderError) as error:
        asyncio.run(
            service.chat(
                agent.id, conversation.id, "Sugira uma agenda.", client_request_id=request_id
            )
        )
    assert error.value.code == "state_conflict"
    with store.transaction(write=False) as unit:
        for table in (
            "routines",
            "routine_commands",
            "routine_occurrences",
            "tasks",
            "tool_grants",
        ):
            assert unit.agents._context.connection.execute(f"SELECT count(*) FROM {table}").get == 0
