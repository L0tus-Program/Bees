"""BEES-022.5: vínculos, liquidação e upgrades em SQLite real, sem geração."""

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from types import SimpleNamespace

import apsw
import pytest

from bees_core.activity import ActivityService
from bees_core.budgets import settle
from bees_core.models import Agent, Conversation, ModelCall, Run, Task, UsageEntry, utc_now
from bees_core.providers.contracts import ChatMessage, ChatRequest, ProviderConfig, Usage
from bees_core.storage.database import Database, MigrationError
from bees_core.storage.store import IntegrityError, InvalidTransition, RevisionConflict, StateStore

CONFIG = ProviderConfig(
    kind="openai_compatible",
    endpoint="https://usage-fixture.invalid/v1",
    model="fixture",
    capabilities={"text": True, "tool_calls": False},
).model_dump(mode="json")
REQUEST = ChatRequest(messages=[ChatMessage(role="user", content="Teste privado")]).model_dump(
    mode="json"
)


def graph(database):
    store = StateStore(database)
    with store.transaction() as unit:
        agent = unit.agents.create(Agent(name="Uma"))
        other = unit.agents.create(Agent(name="Outra"))
        chats = [
            unit.conversations.create(Conversation(agent_id=owner.id))
            for owner in (agent, agent, other)
        ]
        tasks, runs, calls = [], [], []
        for chat in (chats[0], chats[2], None):
            task = unit.tasks.create(
                Task(
                    agent_id=chat.agent_id if chat else agent.id,
                    conversation_id=chat.id if chat else None,
                    title="Teste",
                    objective="Sem chamada externa",
                )
            )
            run = unit.runs.create(Run(task_id=task.id))
            call = unit.model_calls.create(
                ModelCall(
                    run_id=run.id,
                    ordinal=1,
                    task_control_revision=0,
                    agent_revision=1,
                    lease_generation=1,
                    provider_config=CONFIG,
                    request=REQUEST,
                    request_hash=unit.model_calls.request_digest(CONFIG, REQUEST, {}),
                )
            )
            tasks.append(task)
            runs.append(run)
            calls.append(call)
    return SimpleNamespace(
        database=database,
        store=store,
        agent=agent,
        other=other,
        chats=chats,
        tasks=tasks,
        runs=runs,
        calls=calls,
    )


@pytest.fixture
def state(tmp_path):
    database = Database(tmp_path / "usage.sqlite3")
    database.initialize()
    return graph(database)


def entry(state, **overrides):
    return UsageEntry.model_validate(
        {
            "agent_id": state.agent.id,
            "conversation_id": state.chats[0].id,
            "source": "task",
            "model_call_id": state.calls[0].id,
            "provider_kind": "openai_compatible",
            "model": "fixture",
            "reserved_tokens": 500,
            "estimate_method": "chars_div_3_v1",
        }
        | overrides
    )


def raw_insert(connection, record):
    values = record.model_dump(mode="json")
    values["metadata_json"] = json.dumps(values.pop("metadata"))
    connection.execute(
        f"INSERT INTO usage_entries({','.join(values)}) VALUES({','.join('?' for _ in values)})",
        tuple(values.values()),
    )


def snapshot(database):
    with database.transaction(write=False) as connection:
        return {
            table: connection.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall()
            for table in (
                "usage_entries",
                "model_calls",
                "runs",
                "tasks",
                "domain_events",
                "schema_migrations",
            )
        }


@pytest.mark.parametrize("writer", ["store", "sql"])
@pytest.mark.parametrize("mismatch", ["agent", "conversation", "missing_conversation", "chat"])
def test_cross_scope_insert_fails_atomically(state, writer, mismatch):
    changes = {
        "agent": {"agent_id": state.other.id, "conversation_id": state.chats[2].id},
        "conversation": {"conversation_id": state.chats[1].id},
        "missing_conversation": {"conversation_id": None},
        "chat": {"source": "chat", "model_call_id": None, "conversation_id": state.chats[2].id},
    }[mismatch]
    invalid = entry(state, **changes)
    before = snapshot(state.database)
    if writer == "store":
        with state.store.transaction() as unit:
            with pytest.raises(IntegrityError):
                unit.usage_entries.create(invalid)
            assert unit.usage_entries.get(invalid.id) is None
    else:
        with state.database.transaction() as connection:
            with pytest.raises(apsw.ConstraintError, match="usage_.*_scope"):
                raw_insert(connection, invalid)
    assert snapshot(state.database) == before


@pytest.mark.parametrize("source", ["chat", "task", "task_without_conversation"])
def test_legitimate_settlement_is_final_idempotent_and_counted_once(state, source):
    overrides = {}
    if source == "chat":
        overrides = {"source": "chat", "model_call_id": None}
    elif source == "task_without_conversation":
        overrides = {"conversation_id": None, "model_call_id": state.calls[2].id}
    with state.store.transaction() as unit:
        reserved = unit.usage_entries.create(entry(state, **overrides))
        confirmed = settle(unit, reserved.id, usage=Usage(kind="reported", total_tokens=37))
        assert settle(unit, reserved.id, released=True) == confirmed
        with pytest.raises(InvalidTransition):
            unit.usage_entries.update(confirmed, confirmed.revision)
        totals = unit.usage_entries.window(
            state.agent.id, reserved.created_at - timedelta(seconds=1)
        )
        assert totals["counted_tokens"] == totals["reported_tokens"] == 37
        assert totals["entries"] == 1 and totals["open_reservations"] == 0
    with state.database.transaction() as connection:
        with pytest.raises(apsw.ConstraintError, match="settlement is final"):
            connection.execute(
                "UPDATE usage_entries SET total_tokens=1 WHERE id=?", (str(reserved.id),)
            )
        assert (
            connection.execute(
                "SELECT count(*) FROM domain_events WHERE entity_id=?", (str(reserved.id),)
            ).get
            == 2
        )


@pytest.mark.parametrize(
    "field",
    [
        "agent_id",
        "conversation_id",
        "model_call_id",
        "source",
        "provider_kind",
        "model",
        "reserved_tokens",
        "estimate_method",
        "created_at",
    ],
)
def test_settlement_cannot_change_identity_or_reservation(state, field):
    with state.store.transaction() as unit:
        reserved = unit.usage_entries.create(entry(state))
    replacements = {
        "agent_id": state.other.id,
        "conversation_id": state.chats[1].id,
        "model_call_id": state.calls[1].id,
        "source": "chat",
        "provider_kind": "ollama",
        "model": "different",
        "reserved_tokens": 1,
        "estimate_method": "other",
        "created_at": reserved.created_at - timedelta(days=1),
    }
    before = snapshot(state.database)
    with state.store.transaction() as unit:
        changes = {
            "status": "unknown",
            "settled_at": utc_now(),
            field: replacements[field],
        }
        if field == "source":
            changes["model_call_id"] = None
        with pytest.raises(IntegrityError):
            unit.usage_entries.update(reserved.model_copy(update=changes), reserved.revision)
    with state.database.transaction() as connection:
        value = replacements[field]
        if field == "created_at":
            value = value.isoformat()
        elif field in ("agent_id", "conversation_id", "model_call_id"):
            value = str(value)
        with pytest.raises(apsw.ConstraintError):
            connection.execute(
                f"UPDATE usage_entries SET {field}=?,status='unknown',settled_at=? WHERE id=?",
                (value, utc_now().isoformat(), str(reserved.id)),
            )
    assert snapshot(state.database) == before


@pytest.mark.parametrize("parent", ["call", "run", "task", "conversation"])
def test_parent_sql_updates_cannot_reassign_existing_consumption(state, parent):
    with state.store.transaction() as unit:
        unit.usage_entries.create(entry(state))
        # Mesmo escopo/abelha também não autoriza mover a chamada entre execuções.
        sibling = unit.runs.create(Run(task_id=state.tasks[0].id))
    before = snapshot(state.database)
    sql, args = {
        "call": ("UPDATE model_calls SET run_id=? WHERE id=?", (sibling.id, state.calls[0].id)),
        "run": ("UPDATE runs SET task_id=? WHERE id=?", (state.tasks[2].id, state.runs[0].id)),
        "task": (
            "UPDATE tasks SET conversation_id=? WHERE id=?",
            (state.chats[1].id, state.tasks[0].id),
        ),
        "conversation": (
            "UPDATE conversations SET agent_id=? WHERE id=?",
            (state.other.id, state.chats[0].id),
        ),
    }[parent]
    with state.database.transaction() as connection:
        with pytest.raises(apsw.ConstraintError):
            connection.execute(sql, tuple(map(str, args)))
    assert snapshot(state.database) == before


def test_concurrent_settlements_keep_one_observation_and_ledger(state):
    with state.store.transaction() as unit:
        reserved = unit.usage_entries.create(entry(state))
    barrier = threading.Barrier(2)

    def finish(tokens):
        with state.store.transaction(write=False) as unit:
            observed = unit.usage_entries.get(reserved.id)
        barrier.wait(timeout=10)
        try:
            with state.store.transaction() as unit:
                return unit.usage_entries.update(
                    observed.model_copy(
                        update={
                            "status": "confirmed",
                            "usage_kind": "reported",
                            "total_tokens": tokens,
                            "settled_at": utc_now(),
                        }
                    ),
                    observed.revision,
                ).total_tokens
        except RevisionConflict:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(finish, (31, 47)))
    assert results.count("conflict") == 1
    tokens = next(value for value in results if value != "conflict")
    with state.store.transaction(write=False) as unit:
        assert unit.usage_entries.for_model_call(state.calls[0].id).total_tokens == tokens
        assert (
            unit.usage_entries.window(state.agent.id, reserved.created_at)["counted_tokens"]
            == tokens
        )
        assert len(unit.events.list(entity_id=reserved.id)) == 2


def test_concurrent_valid_and_foreign_sql_writers_cannot_share_model_call(state):
    barrier = threading.Barrier(2)

    def insert(foreign):
        record = entry(
            state,
            **(
                {
                    "agent_id": state.other.id,
                    "conversation_id": state.chats[2].id,
                }
                if foreign
                else {}
            ),
        )
        barrier.wait(timeout=10)
        try:
            with state.database.transaction() as connection:
                raw_insert(connection, record)
            return "valid"
        except apsw.ConstraintError:
            return "rejected"

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(insert, (False, True))) == ["rejected", "valid"]
    with state.store.transaction(write=False) as unit:
        assert unit.usage_entries.for_model_call(state.calls[0].id).agent_id == state.agent.id
        assert unit.usage_entries.window(state.other.id, state.other.created_at)["entries"] == 0


def legacy_state(tmp_path, monkeypatch):
    database = Database(tmp_path / "legacy.sqlite3")
    migrations = database._migrations()
    with monkeypatch.context() as patch:
        patch.setattr(database, "_migrations", lambda: migrations[:15])
        database.initialize()
    return graph(database)


def test_upgrade_preserves_valid_usage_ledger_foreign_keys_and_backup(tmp_path, monkeypatch):
    state = legacy_state(tmp_path, monkeypatch)
    with state.store.transaction() as unit:
        reserved = unit.usage_entries.create(entry(state))
        unit.usage_entries.create(entry(state, source="chat", model_call_id=None))
        settle(unit, reserved.id, usage=Usage(kind="reported", total_tokens=37))
    before = snapshot(state.database)
    state.database.initialize()
    after = snapshot(state.database)
    for table in before.keys() - {"schema_migrations"}:
        assert after[table] == before[table]
    assert after["schema_migrations"][:15] == before["schema_migrations"]
    assert state.database.require_current_schema() == state.database._migrations()[-1].version
    assert after["schema_migrations"][15][1] == "0016_usage_integrity.sql"
    with state.database.transaction(write=False) as connection:
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    with state.store.transaction(write=False) as unit:
        assert (
            unit.usage_entries.window(state.agent.id, state.agent.created_at)["counted_tokens"]
            == 537
        )
    [backup] = list(tmp_path.glob("legacy.sqlite3.backup-*.sqlite3"))
    assert snapshot(Database(backup)) == before


@pytest.mark.parametrize("mismatch", ["agent", "conversation", "missing_conversation", "chat"])
@pytest.mark.parametrize("status", ["reserved", "confirmed", "unknown"])
def test_invalid_legacy_upgrade_preserves_evidence_and_conservative_budget(
    tmp_path, monkeypatch, mismatch, status
):
    state = legacy_state(tmp_path, monkeypatch)
    changes = {
        "agent": {"agent_id": state.other.id, "conversation_id": state.chats[2].id},
        "conversation": {"conversation_id": state.chats[1].id},
        "missing_conversation": {"conversation_id": None},
        "chat": {"source": "chat", "model_call_id": None, "conversation_id": state.chats[2].id},
    }[mismatch]
    if status != "reserved":
        changes |= {"status": status, "settled_at": utc_now()}
    if status == "confirmed":
        changes |= {"usage_kind": "reported", "total_tokens": 700}
    invalid = entry(state, **changes)
    with state.database.transaction() as connection:
        raw_insert(connection, invalid)
    before = snapshot(state.database)
    with pytest.raises(MigrationError) as failure:
        state.database.initialize()
    assert "usage_integrity_legacy_invalid" in str(failure.value.__cause__)
    assert snapshot(state.database) == before
    assert state.database.schema_version() == 15
    with state.database.transaction(write=False) as connection:
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    with state.store.transaction() as unit:
        window = unit.usage_entries.window(invalid.agent_id, invalid.created_at)
        assert window["counted_tokens"] == (700 if status == "confirmed" else 500)
        assert window["open_reservations"] == (1 if status == "reserved" else 0)
        with pytest.raises(IntegrityError):
            settle(unit, invalid.id, released=True)
        with pytest.raises(IntegrityError):
            unit.usage_entries.since(invalid.agent_id, invalid.created_at)
    if mismatch == "agent":
        page = ActivityService(state.database).list(state.other.id)
        assert page.usage.entries == 0
        assert all(item.entity_type != "usage_entry" for item in page.items)
        assert all(item.task_id == state.tasks[1].id for item in page.items)
    assert snapshot(state.database) == before
    [backup] = list(tmp_path.glob("legacy.sqlite3.backup-*.sqlite3"))
    assert snapshot(Database(backup)) == before


def test_sql_cannot_transfer_chat_only_conversation_to_another_agent(state):
    with state.store.transaction() as unit:
        reserved = unit.usage_entries.create(
            entry(
                state,
                source="chat",
                model_call_id=None,
                conversation_id=state.chats[1].id,
            )
        )
    with state.database.transaction() as connection:
        with pytest.raises(apsw.ConstraintError, match="usage conversation agent is immutable"):
            connection.execute(
                "UPDATE conversations SET agent_id=? WHERE id=?",
                (str(state.other.id), str(state.chats[1].id)),
            )
    with state.store.transaction(write=False) as unit:
        assert unit.usage_entries.get(reserved.id) == reserved
        assert unit.conversations.get(state.chats[1].id).agent_id == state.agent.id


def test_concurrent_reservation_and_task_reassignment_preserve_exact_scope(state):
    barrier = threading.Barrier(2)

    def mutate(reserve):
        barrier.wait(timeout=10)
        try:
            with state.database.transaction() as connection:
                if reserve:
                    raw_insert(connection, entry(state))
                else:
                    connection.execute(
                        "UPDATE tasks SET conversation_id=? WHERE id=?",
                        (str(state.chats[1].id), str(state.tasks[0].id)),
                    )
            return "applied"
        except apsw.ConstraintError:
            return "rejected"

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(mutate, (True, False))) == ["applied", "rejected"]
    with state.database.transaction(write=False) as connection:
        assert (
            connection.execute(
                "SELECT count(*) FROM usage_entries u JOIN model_calls c ON c.id=u.model_call_id "
                "JOIN runs r ON r.id=c.run_id JOIN tasks t ON t.id=r.task_id "
                "WHERE u.agent_id IS NOT t.agent_id OR u.conversation_id IS NOT t.conversation_id"
            ).get
            == 0
        )
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
