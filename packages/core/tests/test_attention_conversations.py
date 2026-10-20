"""Consultas por conversa mantêm o vínculo canônico e não concedem autoridade."""

from uuid import UUID, uuid4

import pytest
from test_attention import snapshot
from test_execution_approvals import approve, waiting
from test_tasks_execution import fixture_state

from bees_core.attention import AttentionService, PreferenceInput, ReadInput
from bees_core.models import Agent, Conversation, Task
from bees_core.storage.database import Database
from bees_core.storage.store import NotFoundError


def notice(store, agent_id, conversation_id, title="Falha própria", **metadata):
    with store.transaction() as unit:
        return unit.tasks.create(
            Task(
                agent_id=agent_id,
                conversation_id=conversation_id,
                title=title,
                objective="Objetivo privado",
                status="failed",
                metadata=metadata,
            )
        )


def test_scoped_count_cursor_restart_and_read_only_ignore_origin_metadata(tmp_path):
    db, store, agent, chat, _ = fixture_state(tmp_path)
    other = Conversation(agent_id=agent.id, title="Outra conversa")
    with store.transaction() as unit:
        unit.conversations.create(other)
    selected = []
    for i in range(3):
        selected.append(notice(store, agent.id, chat.id, f"Falha A{i}"))
        if i < 2:
            notice(
                store,
                agent.id,
                other.id,
                f"Falha B{i}",
                delegation={"origin_conversation_id": str(chat.id)},
            )
    service = AttentionService(db)
    before = snapshot(db)
    first = service.list(agent.id, conversation_id=chat.id, limit=2)
    assert first.conversation_id == chat.id and first.unread_count == 3
    assert [item.task_id for item in first.items] == [selected[2].id, selected[1].id]
    assert snapshot(db) == before
    notice(store, agent.id, chat.id, "Nova falha depois da primeira página")
    before = snapshot(db)
    second = AttentionService(Database(db.path)).list(
        agent.id, conversation_id=chat.id, limit=2, before=first.next_before
    )
    assert [item.task_id for item in second.items] == [selected[0].id]
    assert second.next_before is None and second.unread_count == 4
    assert service.list(agent.id).unread_count == 6
    assert "conversation_id" not in service.list(agent.id).model_dump()
    assert snapshot(db) == before
    with db.transaction(write=False) as connection:
        foreign_cursor = connection.execute(
            "SELECT i.seq FROM attention_items i JOIN tasks t ON t.id=i.task_id "
            "WHERE t.conversation_id=? LIMIT 1",
            (str(other.id),),
        ).get
    with pytest.raises(NotFoundError):
        service.list(agent.id, conversation_id=chat.id, before=foreign_cursor)


def test_scoped_approval_count_uses_current_validity_without_creating_events(tmp_path):
    db, store, agent, tasks, _, _, task, approvals, pending = waiting(tmp_path)
    other = Conversation(agent_id=agent.id)
    with store.transaction() as unit:
        unit.conversations.create(other)
    notice(store, agent.id, other.id)
    service = AttentionService(db)
    assert service.list(agent.id, conversation_id=task.conversation_id).unread_count == 1
    assert service.list(agent.id, conversation_id=other.id).unread_count == 1
    approve(approvals, agent, pending)
    before = snapshot(db)
    page = service.list(agent.id, conversation_id=task.conversation_id)
    assert page.unread_count == 0 and not page.items[0].requires_attention
    assert service.list(agent.id).unread_count == 1
    assert snapshot(db) == before and tasks.detail(agent.id, task.id).task.calls_started == 0


def test_scope_is_checked_before_read_replay_and_global_preferences_stay_global(tmp_path):
    db, store, agent, chat, _ = fixture_state(tmp_path)
    other = Conversation(agent_id=agent.id)
    foreign_agent = Agent(name="Outra abelha")
    foreign_chat = Conversation(agent_id=foreign_agent.id)
    with store.transaction() as unit:
        unit.conversations.create(other)
        unit.agents.create(foreign_agent)
        unit.conversations.create(foreign_chat)
    notice(store, agent.id, chat.id)
    notice(store, agent.id, other.id)
    service = AttentionService(db)
    item = service.list(agent.id, conversation_id=chat.id).items[0]
    value = ReadInput(client_request_id=uuid4(), expected_revision=1, read=True)
    saved = service.set_read(agent.id, item.id, value, conversation_id=chat.id)
    assert saved.read and saved.revision == 2
    before = snapshot(db)
    for rejected in (other.id, foreign_chat.id, uuid4(), UUID(int=0)):
        with pytest.raises(NotFoundError):
            service.set_read(agent.id, item.id, value, conversation_id=rejected)
    for rejected in (foreign_chat.id, uuid4(), UUID(int=0)):
        with pytest.raises(NotFoundError):
            service.list(agent.id, conversation_id=rejected)
    assert snapshot(db) == before
    assert service.set_read(agent.id, item.id, value) == saved
    assert service.list(agent.id, conversation_id=other.id).unread_count == 1
    service.set_preference(
        agent.id,
        "task_failed",
        PreferenceInput(client_request_id=uuid4(), expected_revision=0, enabled=False),
    )
    assert service.list(agent.id).unread_count == 0
    assert service.list(agent.id, conversation_id=other.id).items[0].muted


def test_catalog_is_bounded_owned_and_read_only_even_with_long_private_metadata(tmp_path):
    db, store, agent, _, _ = fixture_state(tmp_path)
    other = Agent(name="Outra")
    with store.transaction() as unit:
        unit.agents.create(other)
        for i in range(55):
            unit.conversations.create(
                Conversation(
                    agent_id=agent.id,
                    title="é" * 210 if i == 0 else f"Conversa {i}",
                    metadata={"private": "Não retornar"},
                )
            )
        unit.conversations.create(Conversation(agent_id=other.id, title="Conversa estrangeira"))
    service = AttentionService(db)
    before = snapshot(db)
    first = service.conversations(agent.id)
    assert len(first.items) == 50 and first.next_offset == 50
    second = service.conversations(agent.id, offset=first.next_offset)
    assert len(second.items) == 6 and second.next_offset is None
    items = first.items + second.items
    assert len({item.id for item in items}) == 56
    assert all(len(item.title) <= 200 for item in items)
    assert all(set(item.model_dump()) == {"id", "title", "created_at"} for item in items)
    assert "Não retornar" not in first.model_dump_json() + second.model_dump_json()
    assert "Conversa estrangeira" not in first.model_dump_json() + second.model_dump_json()
    assert snapshot(db) == before
    with pytest.raises(NotFoundError):
        service.conversations(uuid4())


@pytest.mark.parametrize(
    "values", [{"limit": 0}, {"limit": True}, {"offset": -1}, {"offset": False}, {"offset": 2**63}]
)
def test_catalog_refuses_invalid_page_bounds(tmp_path, values):
    db, _, agent, _, _ = fixture_state(tmp_path)
    with pytest.raises(ValueError):
        AttentionService(db).conversations(agent.id, **values)
