"""Fila e fencing comprovados com SQLite real, sem rede/provedor de produção."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from importlib import resources
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError

from bees_core.models import Agent, Conversation, Message, ModelCall, Run, Task, TaskCommand
from bees_core.providers.contracts import ChatMessage, ChatRequest, ChatResponse, ProviderConfig
from bees_core.storage import database as database_module
from bees_core.storage.database import Database, MigrationError
from bees_core.storage.store import IntegrityError, InvalidTransition, RevisionConflict, StateStore

NOW = datetime(2026, 10, 5, 12, tzinfo=UTC)
CONFIG = ProviderConfig(
    kind="openai_compatible",
    endpoint="https://fixture.invalid/v1",
    model="fixture",
    capabilities={"text": True, "tool_calls": False},
).model_dump(mode="json")
REQUEST = ChatRequest(
    messages=[ChatMessage(role="user", content="Dado privado de teste")]
).model_dump(mode="json")
RESPONSE = ChatResponse(
    message=ChatMessage(role="assistant", content="Resposta"), finish_reason="stop"
).model_dump(mode="json")


@pytest.fixture
def store(tmp_path: Path):
    db = Database(tmp_path / "state.sqlite3")
    db.initialize()
    return StateStore(db)


def task_graph(store, **limits):
    with store.transaction() as unit:
        agent = unit.agents.create(Agent(name="Teste", provider_config=CONFIG))
        conversation = unit.conversations.create(Conversation(agent_id=agent.id))
        task = unit.tasks.create(
            Task(
                agent_id=agent.id,
                conversation_id=conversation.id,
                title="Teste",
                objective="Produzir resposta",
                **limits,
            )
        )
        unit.runs.create(Run(task_id=task.id, provider_config=CONFIG))
    return agent, task, conversation


def claim(store, when=NOW):
    with store.transaction() as unit:
        return unit.execution.claim_next(uuid4(), now=when, ttl_seconds=5)


def intention(unit, owned, ordinal=1):
    task = unit.tasks.get(owned.task.id)
    return ModelCall(
        run_id=owned.run.id,
        ordinal=ordinal,
        task_control_revision=task.control_revision,
        agent_revision=1,
        lease_generation=owned.generation,
        provider_config=CONFIG,
        request=REQUEST,
        request_hash=unit.model_calls.request_digest(CONFIG, REQUEST, {}),
    )


def test_concurrent_claim_global_slot_and_recent_order(store):
    _, older, _ = task_graph(store)
    _, newer, _ = task_graph(store)
    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(lambda _: claim(store), range(2)))
    assert sum(item is not None for item in claims) == 1
    with store.transaction(write=False) as unit:
        assert [t.id for t in unit.tasks.list()] == [older.id, newer.id]
        assert [t.id for t in unit.tasks.list(newest_first=True, limit=1)] == [newer.id]


def test_prepared_crash_requeues_and_fences_old_worker(store):
    _, task, _ = task_graph(store)
    first = claim(store)
    with store.transaction() as unit:
        prepared = unit.model_calls.create(intention(unit, first))
    second = claim(store, NOW + timedelta(seconds=6))
    assert second.task.id == task.id
    assert second.generation > first.generation
    with store.transaction() as unit:
        with pytest.raises(RevisionConflict):
            unit.execution.begin_call(first, prepared, now=NOW + timedelta(seconds=6))
        unit.execution.release(first)
        unit.execution.assert_claim(second, now=NOW + timedelta(seconds=6))
        cancelled = unit.execution.discard_prepared(
            second, prepared.id, now=NOW + timedelta(seconds=6)
        )
        assert cancelled.status == "cancelled"
        assert unit.tasks.get(task.id).calls_started == 0
        assert unit.model_calls.get(prepared.id).request_hash == prepared.request_hash


def test_unknown_recovery_quarantines_task_not_other_work_and_ack_preserves_journal(store):
    _, task, _ = task_graph(store)
    owned = claim(store)
    with store.transaction() as unit:
        dispatched = unit.execution.begin_call(owned, intention(unit, owned), now=NOW)
    _, other, _ = task_graph(store)
    other_claim = claim(store, NOW + timedelta(seconds=6))
    assert other_claim.task.id == other.id
    with store.transaction() as unit:
        old = unit.model_calls.get(dispatched.id)
        assert old.status == "outcome_unknown"
        assert old.error_code == "worker_lost"
        paused = unit.tasks.get(task.id)
        assert paused.status == paused.desired_state == "paused"
        assert paused.calls_started == 1 and paused.active_milliseconds == 6000
        with pytest.raises(IntegrityError):
            unit.execution.acknowledge_unknown(task.id, command_id=uuid4(), now=NOW)
        cmd = unit.task_commands.create(
            TaskCommand(
                task_id=task.id,
                client_request_id=uuid4(),
                kind="resume",
                expected_revision=paused.revision,
                payload={"acknowledge_unknown": True},
            )
        )
        unit.execution.acknowledge_unknown(task.id, command_id=cmd.id, now=NOW)
        acknowledged = unit.model_calls.get(dispatched.id)
        assert acknowledged.status == "outcome_unknown" and acknowledged.response is None
        assert acknowledged.unknown_acknowledged_at == NOW
        with pytest.raises(IntegrityError):
            unit.task_commands.update(cmd, cmd.revision)
        with pytest.raises(RevisionConflict):
            unit.execution.finish_call(
                owned,
                dispatched.id,
                expected_revision=dispatched.revision,
                status="confirmed",
                response=RESPONSE,
                now=NOW + timedelta(seconds=6),
            )


def test_completion_atomic_scope_and_terminal_guard(store):
    _, _, conversation = task_graph(store)
    owned = claim(store)
    _, _, foreign = task_graph(store)
    with store.transaction() as unit:
        started = unit.execution.begin_call(owned, intention(unit, owned), now=NOW)
        wrong = unit.messages.create(
            Message(conversation_id=foreign.id, role="assistant", content="Outro")
        )
        with pytest.raises(IntegrityError):
            unit.execution.finish_call(
                owned,
                started.id,
                expected_revision=started.revision,
                status="confirmed",
                response=RESPONSE,
                output_message_id=wrong.id,
                now=NOW,
            )
        assert unit.model_calls.get(started.id).status == "dispatch_started"
        output = unit.messages.create(
            Message(conversation_id=conversation.id, role="assistant", content="Resposta")
        )
        confirmed = unit.execution.finish_call(
            owned,
            started.id,
            expected_revision=started.revision,
            status="confirmed",
            response=RESPONSE,
            output_message_id=output.id,
            obsolete=True,
            now=NOW,
        )
        assert confirmed.metadata["obsolete"] is True
        with pytest.raises(InvalidTransition):
            unit.execution.finish_call(
                owned,
                started.id,
                expected_revision=confirmed.revision,
                status="outcome_unknown",
                now=NOW,
            )
        with pytest.raises(IntegrityError):
            unit.model_calls.update(confirmed, confirmed.revision)
    reopened = StateStore(Database(store.database.path))
    with reopened.transaction(write=False) as unit:
        assert unit.model_calls.get(started.id).output_message_id == output.id
        events = unit.events.list(entity_type="model_call", entity_id=started.id)
        assert events and all("Dado privado" not in event.model_dump_json() for event in events)


def test_budget_and_control_recheck_before_dispatch(store):
    _, task, _ = task_graph(store, max_calls=1)
    owned = claim(store)
    with store.transaction() as unit:
        prepared = intention(unit, owned)
        current = unit.tasks.get(task.id)
        changed = unit.tasks.update(
            current.model_copy(update={"control_revision": 1}), current.revision
        )
        with pytest.raises(RevisionConflict):
            unit.execution.begin_call(owned, prepared, now=NOW)
        assert unit.model_calls.get(prepared.id) is None and changed.calls_started == 0
        started = unit.execution.begin_call(owned, intention(unit, owned), now=NOW)
        unit.execution.finish_call(
            owned,
            started.id,
            expected_revision=started.revision,
            status="confirmed",
            response=RESPONSE,
            now=NOW,
        )
        with pytest.raises(InvalidTransition, match="Orçamento"):
            unit.execution.begin_call(owned, intention(unit, owned, ordinal=2), now=NOW)
        assert unit.tasks.get(task.id).calls_started == 1


def test_hash_secret_and_duplicate_intention_rejected(store):
    task_graph(store)
    owned = claim(store)
    with store.transaction() as unit:
        prepared = intention(unit, owned)
        with pytest.raises(IntegrityError, match="Hash"):
            unit.model_calls.create(prepared.model_copy(update={"request_hash": "0" * 64}))
        with pytest.raises(ValidationError):
            unit.model_calls.create(
                prepared.model_copy(update={"provider_config": CONFIG | {"api_key": "PRIVADO"}})
            )
        unit.model_calls.create(prepared)
        with pytest.raises(IntegrityError):
            unit.model_calls.create(prepared.model_copy(update={"id": uuid4()}))
        assert len(unit.model_calls.list(run_id=owned.run.id)) == 1


def test_require_current_schema_never_creates_or_migrates(tmp_path):
    path = tmp_path / "missing" / "state.sqlite3"
    with pytest.raises(MigrationError):
        Database(path).require_current_schema()
    assert not path.parent.exists()
    db = Database(tmp_path / "existing.sqlite3")
    db.initialize()
    assert db.require_current_schema() == 4
    with db.transaction() as connection:
        connection.execute("DELETE FROM schema_migrations WHERE version=4")
    with pytest.raises(MigrationError):
        db.require_current_schema()
    with db.transaction(write=False) as connection:
        assert connection.execute("SELECT max(version) FROM schema_migrations").get == 3
    assert not list(tmp_path.glob("*.backup-*.sqlite3"))


def test_real_upgrade_preserves_graph_and_rolls_out_queue(tmp_path, monkeypatch):
    migration_files = {
        file.name: file.read_bytes()
        for file in resources.files("bees_core.storage.migrations").iterdir()
        if file.name.endswith(".sql")
    }
    directory = tmp_path / "migrations"
    directory.mkdir()
    for name, content in migration_files.items():
        if name.startswith(("0001_", "0002_")):
            (directory / name).write_bytes(content)
    monkeypatch.setattr(database_module.resources, "files", lambda _: directory)
    db = Database(tmp_path / "state.sqlite3")
    db.initialize()
    with db.transaction() as connection:
        # Insere usando o esquema anterior real, sem falsificar código produtor.
        date = NOW.isoformat()
        connection.execute(
            "INSERT INTO agents(id,name,created_at,updated_at) VALUES('agent','Original',?,?)",
            (date, date),
        )
        connection.execute(
            "INSERT INTO tasks(id,agent_id,title,objective,created_at,updated_at) "
            "VALUES('task','agent','Original','Objetivo',?,?)",
            (date, date),
        )
    for name, content in migration_files.items():
        if name.startswith("0003_"):
            (directory / name).write_bytes(content)
    with pytest.raises(MigrationError, match="pendente"):
        db.require_current_schema()
    assert db.schema_version() == 2
    db.initialize()
    assert db.require_current_schema() == 3
    with db.transaction(write=False) as connection:
        assert connection.execute("SELECT title,max_calls,calls_started FROM tasks").get == (
            "Original",
            3,
            0,
        )
        assert connection.execute("SELECT count(*) FROM model_calls").get == 0
    backups = list(tmp_path.glob("*.backup-*.sqlite3"))
    assert len(backups) == 1
    backup = Database(backups[0])
    assert backup.schema_version() == 2
    db.initialize()
    assert len(list(tmp_path.glob("*.backup-*.sqlite3"))) == 1


def test_failed_begin_rolls_back_counter_intent_and_event_inside_caught_error(store, monkeypatch):
    task_graph(store)
    owned = claim(store)
    with store.transaction() as unit:
        prepared = intention(unit, owned)
        original_event = unit.model_calls._event

        def fail_result(record, kind, previous=None):
            if kind == "updated":
                raise RuntimeError("Falha controlada do ledger")
            return original_event(record, kind, previous)

        monkeypatch.setattr(unit.model_calls, "_event", fail_result)
        with pytest.raises(RuntimeError, match="controlada"):
            unit.execution.begin_call(owned, prepared, now=NOW)
        assert unit.model_calls.get(prepared.id) is None
        assert unit.tasks.get(owned.task.id).calls_started == 0
        assert not unit.events.list(entity_type="model_call", entity_id=prepared.id)


def test_cooperative_pause_accepts_response_and_cancelled_recovery_keeps_unknown(store):
    _, task, _ = task_graph(store)
    owned = claim(store)
    with store.transaction() as unit:
        started = unit.execution.begin_call(owned, intention(unit, owned), now=NOW)
        current = unit.tasks.get(task.id)
        unit.tasks.update(
            current.model_copy(update={"desired_state": "paused", "control_revision": 1}),
            current.revision,
        )
        confirmed = unit.execution.finish_call(
            owned,
            started.id,
            expected_revision=started.revision,
            status="confirmed",
            response=RESPONSE,
            now=NOW,
        )
        assert confirmed.status == "confirmed"
        with pytest.raises(RevisionConflict):
            unit.execution.begin_call(owned, intention(unit, owned, ordinal=2), now=NOW)
        unit.execution.release(owned)
    _, cancelled_task, _ = task_graph(store)
    owned = claim(store)
    with store.transaction() as unit:
        started = unit.execution.begin_call(owned, intention(unit, owned), now=NOW)
        current = unit.tasks.get(cancelled_task.id)
        unit.tasks.update(
            current.model_copy(update={"status": "cancelled", "desired_state": "cancelled"}),
            current.revision,
        )
        run = unit.runs.get(owned.run.id)
        unit.runs.update(run.model_copy(update={"status": "cancelled"}), run.revision)
    with store.transaction() as unit:
        assert unit.execution.recover_expired(now=NOW + timedelta(days=2)) == 1
        recovered = unit.model_calls.get(started.id)
        assert recovered.status == "outcome_unknown"
        assert unit.tasks.get(cancelled_task.id).status == "cancelled"
        assert unit.tasks.get(cancelled_task.id).active_milliseconds == 60000
        assert unit.runs.get(owned.run.id).checkpoint["attention_required"] is True


def test_migration_cannot_disable_constraints(tmp_path, monkeypatch):
    db = Database(tmp_path / "state.sqlite3")
    db.initialize()
    original = db._migrations()
    files = tmp_path / "migrations"
    files.mkdir()
    for migration in original:
        (files / migration.name).write_text(migration.sql, encoding="utf-8")
    (files / "0005_unsafe.sql").write_text("PRAGMA foreign_keys=OFF;", encoding="utf-8")
    monkeypatch.setattr(database_module.resources, "files", lambda _: files)
    with pytest.raises(MigrationError):
        db.initialize()
    assert db.schema_version() == 4
    with db.transaction() as connection:
        assert connection.execute("PRAGMA foreign_keys").get == 1
