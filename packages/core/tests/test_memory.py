import json
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from bees_core.memory import MemoryDetails, MemoryInput, MemoryService, MemoryUpdate, memory_details
from bees_core.models import Agent, Memory, Task
from bees_core.storage.database import Database
from bees_core.storage.store import (
    IntegrityError,
    NotFoundError,
    ReadOnlyTransaction,
    RevisionConflict,
    StateStore,
)


@pytest.fixture
def environment(tmp_path):
    database = Database(tmp_path / "bees.sqlite3")
    database.initialize()
    store = StateStore(database)
    with store.transaction() as unit:
        first = unit.agents.create(Agent(name="Pesquisa"))
        second = unit.agents.create(Agent(name="Redação"))
        task = unit.tasks.create(Task(agent_id=first.id, title="Relatório", objective="Fontes"))
        other_task = unit.tasks.create(Task(agent_id=first.id, title="Outro", objective="Outro"))
        foreign_task = unit.tasks.create(
            Task(agent_id=second.id, title="Privado", objective="Outro")
        )
    return database, store, MemoryService(database), first, second, task, other_task, foreign_task


def test_explicit_crud_provenance_reopen_and_deleted_ledger(environment):
    database, store, service, agent, *_ = environment
    original = service.create(
        MemoryInput(scope="agent", agent_id=agent.id, content="VALOR PRIVADO ORIGINAL")
    )
    instant = datetime(2026, 10, 5, 12, tzinfo=UTC)
    updated = service.update(
        original.id,
        MemoryUpdate(
            content="VALOR PRIVADO NOVO",
            kind="fact",
            source_ref="FONTE PRIVADA",
            observed_at=instant,
        ),
        expected_revision=1,
    )
    assert updated.id == original.id
    assert updated.created_at == original.created_at
    assert updated.revision == 2
    assert updated.source == "user"
    reopened = MemoryService(Database(database.path))
    assert reopened.get(updated.id) == updated
    assert memory_details(updated).observed_at == instant
    with store.transaction(write=False) as unit:
        assert unit.agents.get(agent.id).instructions == ""
    reopened.delete(updated.id, expected_revision=2)
    with store.transaction(write=False) as unit:
        assert unit.memories.get(updated.id) is None
        events = unit.events.list(entity_type="memory", entity_id=updated.id)
    assert [event.event_type for event in events] == ["created", "updated", "deleted"]
    assert events[-1].payload == {"revision": 3, "previous_revision": 2, "scope": "agent"}
    assert events[-1].actor == "user"
    assert events[-1].source == "memory_user"
    ledger = json.dumps([event.model_dump(mode="json") for event in events])
    assert "VALOR PRIVADO" not in ledger
    assert "FONTE PRIVADA" not in ledger
    assert service.select_context(agent.id, "VALOR").entries == []
    with pytest.raises(NotFoundError):
        reopened.get(updated.id)


@pytest.mark.parametrize("missing", ["source_ref", "observed_at"])
def test_facts_require_source_and_aware_observation(missing):
    values = {"kind": "fact", "source_ref": "Documento de teste", "observed_at": datetime.now(UTC)}
    del values[missing]
    with pytest.raises(ValidationError):
        MemoryInput(scope="user", content="Preço anunciado", **values)
    with pytest.raises(ValidationError):
        MemoryDetails(kind="fact", source_ref="Teste", observed_at="2026-10-05T12:00:00")


def test_sources_are_normalized_and_imported_details_are_validated(environment):
    _, store, _, *_ = environment
    details = MemoryDetails(
        kind="fact", source_ref="  Anotação explícita  ", observed_at="2026-10-05T09:00:00-03:00"
    )
    assert details.source_ref == "Anotação explícita"
    assert details.observed_at.hour == 12
    assert details.observed_at.tzinfo == UTC
    with store.transaction() as unit:
        with pytest.raises(ValidationError):
            unit.memories.create(
                Memory(scope="user", content="Fato inválido").model_copy(
                    update={"metadata": {"memory_details": {"kind": "fact"}}}
                )
            )
        assert unit.memories.list() == []
        assert unit.events.list(entity_type="memory") == []
    with pytest.raises(ValidationError):
        MemoryDetails(source_ref="Fonte\x00insegura")


def test_editing_only_content_preserves_fact_provenance(environment):
    _, _, service, _, *_ = environment
    fact = service.create(
        MemoryInput(
            scope="user",
            content="Preço do café",
            kind="fact",
            source_ref="Tabela declarada",
            observed_at="2026-10-05T12:00:00Z",
        )
    )
    changed = service.update(
        fact.id, MemoryUpdate(content="Preço do café corrigido"), expected_revision=1
    )
    assert memory_details(changed) == memory_details(fact)
    changed_again = service.update(fact.id, {"content": "Outra correção"}, expected_revision=2)
    assert memory_details(changed_again) == memory_details(fact)
    with pytest.raises(ValidationError):
        service.update(fact.id, {"content": "Sem fonte", "source_ref": None}, expected_revision=3)
    assert service.get(fact.id) == changed_again


@pytest.mark.parametrize(
    "overrides",
    [
        {"scope": "user", "agent_id": uuid4()},
        {"scope": "agent"},
        {"scope": "agent", "agent_id": uuid4(), "task_id": uuid4()},
        {"scope": "task", "agent_id": uuid4()},
        {"content": "   "},
        {"content": "\x00"},
        {"content": "x" * 8193},
    ],
)
def test_invalid_inputs_are_rejected_before_persist(environment, overrides):
    _, store, service, *_ = environment
    with pytest.raises(ValidationError):
        service.create({"scope": "user", "content": "Preferência", **overrides})
    with store.transaction(write=False) as unit:
        assert unit.memories.list() == []


def test_cross_agent_task_and_scope_transfer_refused(environment):
    _, _, service, agent, other, task, _, foreign_task = environment
    with pytest.raises(IntegrityError):
        service.create(
            MemoryInput(
                scope="task", agent_id=agent.id, task_id=foreign_task.id, content="Não vazar"
            )
        )
    with pytest.raises(NotFoundError):
        service.create(MemoryInput(scope="agent", agent_id=uuid4(), content="Ausente"))
    memory = service.create(MemoryInput(scope="agent", agent_id=agent.id, content="Só pesquisa"))
    with pytest.raises(IntegrityError):
        service.update(
            memory.id,
            MemoryInput(scope="agent", agent_id=other.id, content="Transferir"),
            expected_revision=1,
        )
    assert service.get(memory.id).revision == 1
    with pytest.raises(IntegrityError):
        service.select_context(other.id, "Pesquisa", task_id=task.id)


def test_listing_and_selection_keep_user_agent_task_isolation(environment):
    _, store, service, agent, other, task, another_task, foreign_task = environment
    personal = service.create(MemoryInput(scope="user", content="Prefiro português"))
    own = service.create(MemoryInput(scope="agent", agent_id=agent.id, content="Pesquisar fontes"))
    private = service.create(MemoryInput(scope="agent", agent_id=other.id, content="PRIVADO OUTRO"))
    scoped = service.create(
        MemoryInput(
            scope="task", agent_id=agent.id, task_id=task.id, content="Fonte da tarefa selecionada"
        )
    )
    different = service.create(
        MemoryInput(
            scope="task", agent_id=agent.id, task_id=another_task.id, content="PRIVADO OUTRA TAREFA"
        )
    )
    foreign = service.create(
        MemoryInput(
            scope="task", agent_id=other.id, task_id=foreign_task.id, content="PRIVADO OUTRA ABELHA"
        )
    )
    with store.transaction() as unit:
        archived = unit.memories.create(
            Memory(scope="user", content="ARQUIVADA", status="archived")
        )
        deleted = unit.memories.create(
            Memory(scope="user", content="LEGADO DELETED", deleted_at=datetime.now(UTC))
        )
    rows = service.list_for_agent(agent.id)
    assert {entry.id for entry in rows} == {
        personal.id,
        own.id,
        scoped.id,
        different.id,
        archived.id,
        deleted.id,
    }
    assert service.list(scope="user") == [personal, archived, deleted]
    assert service.list(scope="agent", agent_id=other.id) == [private]
    assert service.list(scope="task", agent_id=other.id, task_id=foreign_task.id) == [foreign]
    assert service.list_for_agent(agent.id, limit=1, offset=1) == rows[1:2]
    unscoped_context = service.select_context(agent.id, "fontes")
    assert {entry.id for entry in unscoped_context.entries} == {personal.id, own.id}
    context = service.select_context(agent.id, "fonte", task_id=task.id)
    assert {entry.id for entry in context.entries} == {personal.id, own.id, scoped.id}
    assert "PRIVADO" not in context.text
    assert "ARQUIVADA" not in context.text
    assert "LEGADO DELETED" not in context.text


def test_relevance_is_deterministic_accent_insensitive_and_preserves_evidence(environment):
    _, _, service, agent, *_ = environment
    generic = service.create(MemoryInput(scope="user", content="Prefiro resposta curta"))
    irrelevant = service.create(
        MemoryInput(
            scope="user",
            content="Cotação do dólar",
            kind="fact",
            source_ref="Tabela 1",
            observed_at=datetime.now(UTC),
        )
    )
    relevant = service.create(
        MemoryInput(
            scope="user",
            content="Preço do café atualizado",
            kind="fact",
            source_ref="Tabela 2",
            observed_at=datetime.now(UTC),
        )
    )
    first = service.select_context(agent.id, "PRECO CAFE", max_items=1)
    second = service.select_context(agent.id, "PRECO CAFE", max_items=1)
    assert first == second
    assert first.entries == [relevant]
    assert first.signature == ((str(relevant.id), 1),)
    assert first.omitted == 1
    assert "Tabela 2" in first.text
    assert "observed_at" in first.text
    assert "Tabela 1" not in first.text
    assert str(irrelevant.id) not in first.text
    assert str(generic.id) not in first.text


def test_context_budget_never_splits_memory_from_source(environment):
    _, _, service, agent, *_ = environment
    small = service.create(MemoryInput(scope="user", content="Texto curto"))
    service.create(MemoryInput(scope="user", content="Texto " + "x" * 8000))
    context = service.select_context(agent.id, "Texto", max_chars=400)
    assert context.entries == [small]
    assert len(context.text) <= 400
    assert context.omitted == 1
    assert service.select_context(agent.id, "Texto", max_chars=1).text == ""
    assert service.select_context(agent.id, "Texto", max_chars=1).omitted == 2


def test_compare_and_swap_rejects_concurrent_edit_or_delete(environment):
    database, store, service, agent, *_ = environment
    memory = service.create(MemoryInput(scope="agent", agent_id=agent.id, content="Inicial"))
    concurrent = MemoryService(Database(database.path))
    updated = concurrent.update(
        memory.id, MemoryUpdate(content="Outra revisão"), expected_revision=1
    )
    assert updated.revision == 2
    with pytest.raises(RevisionConflict):
        service.update(memory.id, MemoryUpdate(content="Obsoleto"), expected_revision=1)
    with pytest.raises(RevisionConflict):
        service.delete(memory.id, expected_revision=1)
    assert service.get(memory.id) == updated
    with store.transaction(write=False) as unit:
        assert len(unit.events.list(entity_type="memory", entity_id=memory.id)) == 2


def test_delete_ledger_failure_rolls_back_even_when_caller_catches(environment):
    _, store, service, agent, *_ = environment
    memory = service.create(MemoryInput(scope="agent", agent_id=agent.id, content="Preservar"))
    with store.database.transaction() as connection:
        connection.execute(
            "CREATE TRIGGER fail_memory_delete_ledger BEFORE INSERT ON domain_events "
            "WHEN NEW.event_type='deleted' BEGIN SELECT RAISE(ABORT,'controlled failure'); END"
        )
    with store.transaction() as unit:
        with pytest.raises(IntegrityError):
            unit.memories.delete(memory.id, expected_revision=1)
        assert unit.memories.get(memory.id) == memory
        unit.memories.create(Memory(scope="user", content="Outro registro continua"))
    assert service.get(memory.id) == memory
    with store.transaction(write=False) as unit:
        assert [
            item.event_type for item in unit.events.list(entity_type="memory", entity_id=memory.id)
        ] == ["created"]


def test_context_snapshot_changes_after_edit_delete_or_new_relevant_memory(environment):
    _, _, service, agent, *_ = environment
    memory = service.create(MemoryInput(scope="user", content="Prefiro café"))
    first = service.select_context(agent.id, "café")
    service.update(memory.id, MemoryUpdate(content="Prefiro café sem açúcar"), expected_revision=1)
    second = service.select_context(agent.id, "café")
    assert second.signature != first.signature
    assert second.text != first.text
    added = service.create(MemoryInput(scope="agent", agent_id=agent.id, content="Café orgânico"))
    third = service.select_context(agent.id, "café")
    assert third.signature != second.signature
    service.delete(added.id, expected_revision=1)
    assert service.select_context(agent.id, "café") == second


@pytest.mark.parametrize(
    "kwargs",
    [
        {"max_chars": 0},
        {"max_chars": True},
        {"max_items": 0},
        {"max_items": 101},
        {"max_items": True},
    ],
)
def test_invalid_context_limits_are_rejected(environment, kwargs):
    _, _, service, agent, *_ = environment
    with pytest.raises(ValueError):
        service.select_context(agent.id, "Consulta", **kwargs)


def test_repository_delete_respects_transaction_ownership_and_rollback(environment):
    _, store, service, agent, *_ = environment
    memory = service.create(MemoryInput(scope="agent", agent_id=agent.id, content="Preservar"))
    with store.transaction(write=False) as unit:
        with pytest.raises(ReadOnlyTransaction):
            unit.memories.delete(memory.id, expected_revision=1)
    with pytest.raises(RuntimeError):
        with store.transaction() as unit:
            unit.memories.delete(memory.id, expected_revision=1)
            raise RuntimeError("Falha posterior controlada")
    assert service.get(memory.id) == memory
    for revision in [True, 0, -1]:
        with pytest.raises(ValueError):
            service.delete(memory.id, expected_revision=revision)


def test_model_copy_cannot_bypass_source_or_scope_validation(environment):
    _, _, service, agent, *_ = environment
    invalid = MemoryInput(scope="user", content="Inicial").model_copy(update={"kind": "fact"})
    with pytest.raises(ValidationError):
        service.create(invalid)
    record = service.create(MemoryInput(scope="agent", agent_id=agent.id, content="Inicial"))
    invalid_update = MemoryUpdate(content="Inicial").model_copy(
        update={"observed_at": datetime.now()}
    )
    with pytest.raises(ValidationError):
        service.update(record.id, invalid_update, expected_revision=1)
