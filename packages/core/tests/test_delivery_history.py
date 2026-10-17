"""Histórico profundo em SQLite real, vínculos exatos e nenhuma mutação/replay."""

from datetime import timedelta
from uuid import uuid4

import pytest

from bees_core.delivery_history import DeliveryHistory, DeliveryQueryError
from bees_core.models import Agent, Artifact, Conversation, Message, Run, Task, utc_now
from bees_core.storage.database import Database
from bees_core.storage.store import NotFoundError, StateStore


@pytest.fixture
def history(tmp_path):
    database = Database(tmp_path / "state.sqlite3")
    database.initialize()
    store = StateStore(database)
    first, other = Agent(name="Primeira"), Agent(name="Outra")
    conversation = Conversation(agent_id=first.id, title="Conversa antiga")
    task = Task(
        agent_id=first.id,
        conversation_id=conversation.id,
        title="Relatório REGIÃO antigo",
        objective="Consolidar dados",
        status="completed",
    )
    created = utc_now() - timedelta(days=400)
    with store.transaction() as unit:
        unit.agents.create(first)
        unit.agents.create(other)
        unit.conversations.create(conversation)
        unit.tasks.create(task)
        # Mais de uma janela de mensagens, com empate de data entre os dois tipos.
        for i in range(151):
            date = created + timedelta(seconds=i)
            message = unit.messages.create(
                Message(
                    conversation_id=conversation.id,
                    role="assistant",
                    content=f"Resultado {i}",
                    source="task",
                    created_at=date,
                    updated_at=date,
                )
            )
            unit.runs.create(
                Run(
                    task_id=task.id,
                    status="completed",
                    checkpoint={
                        "result_message_id": str(message.id),
                        "deliverables": {"partial": i == 0},
                    },
                    created_at=date,
                    updated_at=date,
                )
            )
            unit.artifacts.create(
                Artifact(
                    agent_id=first.id,
                    task_id=task.id,
                    conversation_id=conversation.id,
                    name=f"Entrega {i}.txt",
                    status="draft",
                    created_at=date,
                    updated_at=date,
                )
            )
    return database, store, first, other, conversation, task, DeliveryHistory(database)


def snapshot(database):
    with database.transaction(write=False) as connection:
        return [
            list(connection.execute(f"SELECT * FROM {table} ORDER BY id"))
            for table in ("tasks", "runs", "messages", "artifacts", "domain_events")
        ]


def test_paginate_all_older_deliveries_and_exact_result_without_writes(history):
    database, _, agent, _, _, task, reader = history
    before = snapshot(database)
    page = reader.list(agent.id, limit=37)
    until = page["until"]
    items = list(page["items"])
    while page["next_cursor"]:
        page = reader.list(agent.id, limit=37, cursor=page["next_cursor"])
        assert page["until"] == until
        items.extend(page["items"])
    assert len(items) == len({(item["kind"], item["id"]) for item in items}) == 302
    assert all(item["task_id"] == str(task.id) for item in items)
    assert items == sorted(
        items, key=lambda item: (item["created_at"], item["kind"] + ":" + item["id"]), reverse=True
    )
    oldest_result = next(item for item in reversed(items) if item["kind"] == "result")
    detail = reader.result(agent.id, oldest_result["id"], oldest_result["run_id"])
    assert detail["content"] == "Resultado 0" and detail["item"]["partial"] is True
    assert detail["item"]["version"] == 1
    assert snapshot(database) == before


def test_filters_exact_ids_unicode_and_literal_search(history):
    _, _, agent, _, conversation, task, reader = history
    assert len(reader.list(agent.id, query="região", limit=100)["items"]) == 100
    assert reader.list(agent.id, query="%_")["items"] == []
    result = reader.list(agent.id, query="região")["items"][0]
    assert reader.list(agent.id, query=result["id"])["items"] == [result]
    assert len(reader.list(agent.id, query=str(task.id), limit=100)["items"]) == 100
    page = reader.list(agent.id, task_id=task.id, conversation_id=conversation.id)
    assert len(page["items"]) == 50
    assert page["scope"]["task_id"] == str(task.id)


def test_agent_scope_and_run_binding_are_enforced(history):
    _, _, first, other, conversation, task, reader = history
    result = reader.list(first.id, query="região")["items"][0]
    assert reader.list(other.id)["items"] == []
    for agent, message, run in [
        (other.id, result["id"], result["run_id"]),
        (first.id, result["id"], uuid4()),
    ]:
        with pytest.raises(NotFoundError):
            reader.result(agent, message, run)
    for kwargs in [{"conversation_id": conversation.id}, {"task_id": task.id}]:
        with pytest.raises(NotFoundError):
            reader.list(other.id, **kwargs)
    with pytest.raises(NotFoundError):
        reader.list(uuid4())


def test_cursor_binding_invalid_limits_and_new_deliveries_do_not_shift_pages(history):
    _, store, first, other, conversation, task, reader = history
    page = reader.list(first.id, limit=10)
    cursor = page["next_cursor"]
    for agent, kwargs in [
        (other.id, {}),
        (first.id, {"query": "changed"}),
        (first.id, {"task_id": task.id}),
    ]:
        with pytest.raises(DeliveryQueryError):
            reader.list(agent, cursor=cursor, **kwargs)
    for invalid in ["bad", "e30=", "", "a" * 2049]:
        with pytest.raises(DeliveryQueryError):
            reader.list(first.id, cursor=invalid)
    for invalid in [True, 0, -1, 101]:
        with pytest.raises(DeliveryQueryError):
            reader.list(first.id, limit=invalid)
    expected = reader.list(first.id, cursor=cursor, limit=10)
    with store.transaction() as unit:
        unit.artifacts.create(
            Artifact(agent_id=first.id, conversation_id=conversation.id, name="Nova entrega.txt")
        )
    assert reader.list(first.id, cursor=cursor, limit=10) == expected


def test_deleted_and_partial_artifacts_remain_honest_and_no_cross_conversation_result(history):
    _, store, agent, _, _, task, reader = history
    with store.transaction() as unit:
        other_conversation = unit.conversations.create(Conversation(agent_id=agent.id))
        unrelated = unit.messages.create(
            Message(
                conversation_id=other_conversation.id,
                role="assistant",
                content="Não pertence a essa tarefa",
            )
        )
        unit.runs.create(
            Run(
                task_id=task.id,
                status="completed",
                checkpoint={"result_message_id": str(unrelated.id)},
            )
        )
        deleted = unit.artifacts.create(
            Artifact(
                agent_id=agent.id,
                conversation_id=other_conversation.id,
                name="Removido.txt",
                status="deleted",
            )
        )
    assert reader.list(agent.id, query=str(unrelated.id))["items"] == []
    with pytest.raises(NotFoundError):
        reader.result(agent.id, unrelated.id, uuid4())
    item = reader.list(agent.id, query=str(deleted.id))["items"][0]
    assert item["status"] == "deleted" and item["partial"] is False
    assert reader.list(agent.id, query="Entrega 0.txt")["items"][0]["partial"] is True
