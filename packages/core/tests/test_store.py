import json
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from bees_core.models import (
    Action,
    Agent,
    Approval,
    Artifact,
    Conversation,
    Memory,
    Message,
    Policy,
    Routine,
    Run,
    Task,
)
from bees_core.storage.database import Database
from bees_core.storage.store import (
    IntegrityError,
    InvalidTransition,
    ReadOnlyTransaction,
    RevisionConflict,
    StateStore,
    TransactionClosed,
)


@pytest.fixture
def store(tmp_path: Path) -> StateStore:
    database = Database(tmp_path / "state.sqlite3")
    database.initialize()
    return StateStore(database)


def populate(store: StateStore) -> dict[str, UUID]:
    with store.transaction(correlation_id="test-flow") as uow:
        agent = uow.agents.create(Agent(name="Pesquisadora", purpose="fontes"))
        conversation = uow.conversations.create(Conversation(agent_id=agent.id, title="Relatório"))
        message = uow.messages.create(
            Message(conversation_id=conversation.id, role="user", content="CONTEÚDO PRIVADO")
        )
        routine = uow.routines.create(
            Routine(
                agent_id=agent.id,
                name="Semanal",
                instructions="Preparar relatório",
                timezone="America/Sao_Paulo",
                schedule={"kind": "weekly", "weekday": 1},
            )
        )
        task = uow.tasks.create(
            Task(
                agent_id=agent.id,
                conversation_id=conversation.id,
                routine_id=routine.id,
                title="Comparar fontes",
                objective="Comparar fontes de teste",
            )
        )
        run = uow.runs.create(Run(task_id=task.id, checkpoint={"step": 0}))
        action = uow.actions.create(
            Action(
                run_id=run.id,
                tool_name="files.read",
                parameters={"path": "CONTEÚDO PRIVADO"},
            )
        )
        policy = uow.policies.create(
            Policy(
                agent_id=agent.id,
                name="Pasta teste",
                effect="ask",
                scope={"path": "test"},
            )
        )
        approval = uow.approvals.create(Approval(action_id=action.id, policy_id=policy.id))
        memory = uow.memories.create(
            Memory(
                agent_id=agent.id,
                task_id=task.id,
                scope="task",
                content="Preferência de teste",
            )
        )
        artifact = uow.artifacts.create(
            Artifact(
                task_id=task.id,
                run_id=run.id,
                name="relatorio.txt",
                storage_key="artifact/test",
                sha256="a" * 64,
                size_bytes=100,
                status="ready",
            )
        )
    return {
        "agents": agent.id,
        "conversations": conversation.id,
        "messages": message.id,
        "routines": routine.id,
        "tasks": task.id,
        "runs": run.id,
        "actions": action.id,
        "policies": policy.id,
        "approvals": approval.id,
        "memories": memory.id,
        "artifacts": artifact.id,
    }


def test_all_entities_and_history_survive_another_process(store: StateStore) -> None:
    ids = populate(store)
    code = """
import json, sys
from bees_core.storage.database import Database
from bees_core.storage.store import StateStore
store = StateStore(Database(sys.argv[1]))
with store.transaction(write=False) as uow:
    result = {name: getattr(uow,name).get(id).model_dump(mode='json')
              for name,id in json.loads(sys.argv[2]).items()}
    result['event_count'] = len(uow.events.list())
print(json.dumps(result))
"""
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            code,
            str(store.database.path),
            json.dumps({name: str(id) for name, id in ids.items()}),
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    restored = json.loads(result.stdout)
    assert restored["event_count"] == 11
    assert restored["messages"]["content"] == "CONTEÚDO PRIVADO"
    assert restored["routines"]["timezone"] == "America/Sao_Paulo"
    assert restored["artifacts"]["sha256"] == "a" * 64
    for name, id in ids.items():
        assert restored[name]["id"] == str(id)
        assert datetime.fromisoformat(restored[name]["created_at"].replace("Z", "+00:00")).tzinfo


def test_domain_events_do_not_copy_content_or_parameters(store: StateStore) -> None:
    populate(store)
    with store.transaction(write=False) as uow:
        events = uow.events.list(limit=100)
        assert len(events) == 11
        assert all(event.correlation_id == "test-flow" for event in events)
        assert "CONTEÚDO PRIVADO" not in json.dumps(
            [event.model_dump(mode="json") for event in events]
        )
        assert all(set(event.payload) <= {"revision", "status"} for event in events)


def test_transaction_rolls_back_records_and_events(store: StateStore) -> None:
    agent = Agent(name="Não persistir")
    with pytest.raises(RuntimeError, match="controlada"):
        with store.transaction() as uow:
            uow.agents.create(agent)
            raise RuntimeError("Falha controlada antes do commit")
    with store.transaction(write=False) as uow:
        assert uow.agents.get(agent.id) is None
        assert uow.events.list() == []


def test_event_failure_rolls_back_record_even_when_caller_catches(store: StateStore) -> None:
    # Falha controlada no ledger demonstra atomicidade de cada operação dentro da UoW.
    with store.database.transaction() as connection:
        connection.execute(
            "CREATE TRIGGER test_fail_event BEFORE INSERT ON domain_events "
            "BEGIN SELECT RAISE(ABORT,'falha controlada'); END"
        )
    agent = Agent(name="Sem ledger")
    with store.transaction() as uow:
        with pytest.raises(IntegrityError):
            uow.agents.create(agent)
    with store.transaction(write=False) as uow:
        assert uow.agents.get(agent.id) is None


def test_optimistic_revision_preserves_the_winning_edit(store: StateStore) -> None:
    with store.transaction() as uow:
        original = uow.agents.create(Agent(name="Original"))
    with store.transaction() as uow:
        winner = uow.agents.update(original.model_copy(update={"name": "Atualizada"}), 1)
        assert winner.revision == 2
        assert winner.created_at == original.created_at
        assert winner.updated_at >= original.updated_at
    with store.transaction() as uow:
        with pytest.raises(RevisionConflict):
            uow.agents.update(original.model_copy(update={"name": "Antiga"}), 1)
    with store.transaction(write=False) as uow:
        assert uow.agents.get(original.id).name == "Atualizada"
        assert len(uow.events.list(entity_id=original.id)) == 2


def test_parents_are_immutable_and_messages_append_only(store: StateStore) -> None:
    ids = populate(store)
    with store.transaction() as uow:
        second = uow.agents.create(Agent(name="Segunda"))
        conversation = uow.conversations.get(ids["conversations"])
        with pytest.raises(IntegrityError):
            uow.conversations.update(conversation.model_copy(update={"agent_id": second.id}), 1)
        message = uow.messages.get(ids["messages"])
        with pytest.raises(IntegrityError, match="append-only"):
            uow.messages.update(message.model_copy(update={"content": "Substituída"}), 1)


def test_cross_agent_and_cross_task_links_rejected(store: StateStore) -> None:
    ids = populate(store)
    with store.transaction() as uow:
        second = uow.agents.create(Agent(name="Segunda"))
        second_task = uow.tasks.create(Task(agent_id=second.id, title="Outra", objective="Teste"))
        policy = uow.policies.create(Policy(agent_id=second.id, name="Outra regra", effect="deny"))
        records = [
            (
                uow.tasks,
                Task(
                    agent_id=second.id,
                    conversation_id=ids["conversations"],
                    title="Inválida",
                    objective="Teste",
                ),
            ),
            (
                uow.tasks,
                Task(
                    agent_id=second.id,
                    routine_id=ids["routines"],
                    title="Inválida",
                    objective="Teste",
                ),
            ),
            (uow.approvals, Approval(action_id=ids["actions"], policy_id=policy.id)),
            (
                uow.memories,
                Memory(agent_id=second.id, task_id=ids["tasks"], scope="task", content="Teste"),
            ),
            (uow.artifacts, Artifact(task_id=second_task.id, run_id=ids["runs"], name="Teste")),
        ]
        for repository, record in records:
            with pytest.raises(IntegrityError):
                repository.create(record)
        with pytest.raises(IntegrityError):
            uow.runs.create(Run(task_id=uuid4()))


def test_unknown_result_requires_explicit_reconciliation(store: StateStore) -> None:
    ids = populate(store)
    with store.transaction() as uow:
        action = uow.actions.get(ids["actions"])
        for status in ("ready", "dispatch_started", "outcome_unknown"):
            action = uow.actions.update(
                action.model_copy(update={"status": status}), action.revision
            )
    restarted = StateStore(Database(store.database.path))
    with restarted.transaction() as uow:
        unknown = uow.actions.get(ids["actions"])
        assert unknown.status == "outcome_unknown"
        for status in ("prepared", "ready", "dispatch_started", "confirmed", "cancelled"):
            with pytest.raises(InvalidTransition):
                uow.actions.update(unknown.model_copy(update={"status": status}), unknown.revision)
        reconciled = uow.actions.reconcile(
            unknown.id,
            "confirmed",
            expected_revision=unknown.revision,
            evidence_ref="test/evidence",
        )
        assert reconciled.status == "confirmed"
        assert reconciled.metadata["reconciliation_ref"] == "test/evidence"
        assert uow.events.list(entity_id=unknown.id)[-1].event_type == "reconciled"


@pytest.mark.parametrize(
    "changed",
    [
        {"parameters": {"path": "diferente"}},
        {"tool_name": "outro.tool"},
        {"idempotency_key": "nova"},
        {"attempt": 2},
    ],
)
def test_authorized_action_parameters_cannot_be_replaced(store: StateStore, changed: dict) -> None:
    ids = populate(store)
    with store.transaction() as uow:
        action = uow.actions.get(ids["actions"])
        ready = uow.actions.update(action.model_copy(update={"status": "ready"}), 1)
        with pytest.raises(InvalidTransition):
            uow.actions.update(ready.model_copy(update=changed), 2)


def test_cache_pruning_only_removes_expired_derived_entries(store: StateStore) -> None:
    ids = populate(store)
    store.put_cache("expiring-1", {"derived": 1}, ttl_seconds=1)
    store.put_cache("expiring-2", [2], ttl_seconds=1)
    store.put_cache("keep", "derived", ttl_seconds=3600)
    assert store.get_cache("keep") == "derived"
    future = datetime.now(UTC) + timedelta(seconds=10)
    assert store.prune_cache(limit=1, now=future) == 1
    assert store.prune_cache(limit=1, now=future) == 1
    assert store.prune_cache(limit=1, now=future) == 0
    assert store.get_cache("keep") == "derived"
    with store.transaction(write=False) as uow:
        assert uow.approvals.get(ids["approvals"]) is not None
        assert uow.actions.get(ids["actions"]) is not None
        assert len(uow.events.list()) == 11


def test_cache_expiration_and_default_ttl(
    store: StateStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    instant = datetime(2026, 10, 4, tzinfo=UTC)
    monkeypatch.setattr("bees_core.storage.store.utc_now", lambda: instant)
    store.put_cache("default", 1)
    store.put_cache("short", 2, ttl_seconds=5)
    monkeypatch.setattr("bees_core.storage.store.utc_now", lambda: instant + timedelta(seconds=5))
    assert store.get_cache("short") is None
    assert store.get_cache("default") == 1
    assert store.prune_cache() == 1


def test_readonly_and_closed_uow_cannot_mutate_or_reuse(store: StateStore) -> None:
    with store.transaction(write=False) as uow:
        with pytest.raises(ReadOnlyTransaction):
            uow.agents.create(Agent(name="Negada"))
    with pytest.raises(TransactionClosed):
        uow.agents.list()


def test_dates_scope_artifact_and_timezone_validation() -> None:
    with pytest.raises(ValidationError):
        Agent(name="Teste", created_at=datetime(2026, 10, 4))
    with pytest.raises(ValidationError):
        Memory(scope="task", content="Sem vínculo")
    with pytest.raises(ValidationError):
        Artifact(task_id=uuid4(), name="Sem blob metadata", status="ready")
    with pytest.raises(ValidationError):
        Routine(agent_id=uuid4(), name="Teste", instructions="Teste", timezone="not/a/timezone")


def test_wrong_record_and_invalid_listing_rejected(store: StateStore) -> None:
    with store.transaction() as uow:
        with pytest.raises(TypeError):
            uow.agents.create(Conversation(agent_id=uuid4()))
        with pytest.raises(ValueError):
            uow.agents.list(limit=1001)
        with pytest.raises(ValueError):
            uow.events.list(after_seq=-1)


def test_ready_artifact_requires_a_new_record_for_new_content(store: StateStore) -> None:
    ids = populate(store)
    with store.transaction() as uow:
        artifact = uow.artifacts.get(ids["artifacts"])
        with pytest.raises(IntegrityError):
            uow.artifacts.update(artifact.model_copy(update={"sha256": "b" * 64}), 1)
        with pytest.raises(IntegrityError):
            uow.artifacts.update(artifact.model_copy(update={"version": 2}), 1)
        with pytest.raises(InvalidTransition):
            uow.artifacts.update(artifact.model_copy(update={"status": "draft"}), 1)
        deleted = uow.artifacts.update(artifact.model_copy(update={"status": "deleted"}), 1)
        with pytest.raises(InvalidTransition):
            uow.artifacts.update(deleted.model_copy(update={"status": "draft"}), 2)


@pytest.mark.parametrize("scope", ["user", "agent", "task"])
def test_memory_scopes_roundtrip_as_text(store: StateStore, scope: str) -> None:
    ids = populate(store)
    memory = Memory(
        scope=scope,
        content="Preferência",
        agent_id=ids["agents"] if scope in ("agent", "task") else None,
        task_id=ids["tasks"] if scope == "task" else None,
    )
    with store.transaction() as uow:
        uow.memories.create(memory)
    with StateStore(Database(store.database.path)).transaction(write=False) as uow:
        assert uow.memories.get(memory.id) == memory


@pytest.mark.parametrize("terminal", ["confirmed", "failed_no_effect", "outcome_unknown"])
@pytest.mark.parametrize(
    "changed",
    [
        {"result": {"overwritten": True}},
        {"lease_generation": 2},
        {"policy_revision": 2},
        {"metadata": {"evidence": "overwritten"}},
    ],
)
def test_effect_evidence_cannot_be_overwritten(
    store: StateStore, terminal: str, changed: dict
) -> None:
    ids = populate(store)
    with store.transaction() as uow:
        action = uow.actions.get(ids["actions"])
        action = uow.actions.update(
            action.model_copy(
                update={
                    "status": "ready",
                    "lease_generation": 1,
                    "policy_revision": 1,
                }
            ),
            action.revision,
        )
        dispatched = uow.actions.update(action.model_copy(update={"status": "dispatch_started"}), 2)
        with pytest.raises(InvalidTransition):
            uow.actions.update(dispatched.model_copy(update=changed), 3)
        final = uow.actions.update(
            dispatched.model_copy(
                update={
                    "status": terminal,
                    "result": {"evidence": "original"},
                }
            ),
            3,
        )
        with pytest.raises(InvalidTransition):
            uow.actions.update(final.model_copy(update=changed), 4)
        assert uow.actions.get(final.id).result == {"evidence": "original"}


def test_reconciliation_retains_original_result(store: StateStore) -> None:
    ids = populate(store)
    with store.transaction() as uow:
        action = uow.actions.get(ids["actions"])
        for status in ("ready", "dispatch_started", "outcome_unknown"):
            changes = {"status": status}
            if status == "outcome_unknown":
                changes["result"] = {"partial": "original"}
            action = uow.actions.update(action.model_copy(update=changes), action.revision)
        final = uow.actions.reconcile(
            action.id,
            "failed_no_effect",
            expected_revision=action.revision,
            evidence_ref="test/reconciled",
        )
        assert final.result == {"partial": "original"}
        with pytest.raises(InvalidTransition):
            uow.actions.update(final.model_copy(update={"status": "ready"}), final.revision)


def test_listing_validates_status_and_uuid_filters(store: StateStore) -> None:
    with store.transaction(write=False) as uow:
        with pytest.raises(ValidationError):
            uow.actions.list(status="invalid")
        with pytest.raises(ValidationError):
            uow.tasks.list(agent_id="not-a-uuid")


def test_model_copy_cannot_bypass_storage_validation(store: StateStore) -> None:
    ids = populate(store)
    with store.transaction() as uow:
        action = uow.actions.get(ids["actions"])
        count = len(uow.events.list())
        with pytest.raises(ValidationError):
            uow.actions.update(action.model_copy(update={"status": "invalid"}), 1)
        artifact = uow.artifacts.get(ids["artifacts"])
        with pytest.raises(ValidationError):
            uow.artifacts.update(artifact.model_copy(update={"sha256": None}), 1)
        assert uow.actions.get(action.id) == action
        assert uow.artifacts.get(artifact.id) == artifact
        assert len(uow.events.list()) == count
