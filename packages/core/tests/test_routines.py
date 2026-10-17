"""Agenda canônica, limites temporais e intenções humanas de rotinas."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from uuid import uuid4

import apsw
import pytest
from pydantic import ValidationError

from bees_core.models import Agent, Conversation, Routine
from bees_core.routines import (
    CreateRoutine,
    ReviseRoutine,
    RoutineDefinition,
    RoutineService,
    SetRoutineStatus,
    next_run,
)
from bees_core.skills import CreateSkill, EnableSkill, ReviseSkill, SkillDefinition, SkillService
from bees_core.storage.database import Database
from bees_core.storage.store import NotFoundError, RevisionConflict, StateStore


def instant(value):
    return datetime.fromisoformat(value).astimezone(UTC)


@pytest.fixture
def state(tmp_path):
    db = Database(tmp_path / "bees.sqlite3")
    db.initialize()
    with StateStore(db).transaction() as unit:
        agent = unit.agents.create(Agent(name="Rotinas"))
        other = unit.agents.create(Agent(name="Outra abelha"))
        conversation = unit.conversations.create(Conversation(agent_id=agent.id))
    skill_service = SkillService(db)
    skill = skill_service.create(
        agent.id,
        CreateSkill(
            client_request_id=uuid4(),
            definition=SkillDefinition(
                name="Resumo",
                purpose="Produzir resumo",
                inputs=[{"name": "tema", "description": "Tema"}],
                steps=[{"title": "Resumir", "instruction": "Consultar as fontes e resumir"}],
                output="Resumo com fontes",
                completion="Fontes conferidas",
            ),
        ),
    )
    skill_service.enable(
        agent.id,
        skill.id,
        EnableSkill(client_request_id=uuid4(), expected_revision=1, version=1, enabled=True),
    )
    definition = RoutineDefinition(
        name="Resumo diário",
        instructions="Preparar o resumo para revisão humana",
        schedule={"frequency": "daily", "time": "09:00"},
        timezone="UTC",
        skill_id=skill.id,
        skill_version=1,
        inputs={"tema": "Ciência"},
        environment="none",
        destination={"conversation_id": conversation.id},
        notification="always",
    )
    service = RoutineService(db, clock=lambda: instant("2026-01-01T08:00:00+00:00"))
    return db, agent, other, skill, definition, service


def create(state, *, active=True, definition=None):
    _, agent, _, _, original, service = state
    return service.create(
        agent.id,
        CreateRoutine(client_request_id=uuid4(), definition=definition or original, active=active),
    )


def evaluate(state, routine, value):
    return state[-1].evaluate(
        state[1].id, routine.id, expected_revision=routine.revision, now=instant(value)
    )


def test_dst_skips_nonexistent_and_uses_first_repeated_time(state):
    definition = state[4].model_copy(update={"timezone": "America/New_York"})
    missing = definition.model_copy(
        update={"schedule": definition.schedule.model_copy(update={"time": "02:30"})}
    )
    assert next_run(missing, instant("2026-03-08T05:00:00+00:00")) == instant(
        "2026-03-09T06:30:00+00:00"
    )
    repeated = definition.model_copy(
        update={"schedule": definition.schedule.model_copy(update={"time": "01:30"})}
    )
    first = next_run(repeated, instant("2026-11-01T04:00:00+00:00"))
    assert first == instant("2026-11-01T05:30:00+00:00")
    assert next_run(repeated, first) == instant("2026-11-02T06:30:00+00:00")
    assert next_run(repeated, instant("2026-11-01T05:45:00+00:00")) == instant(
        "2026-11-02T06:30:00+00:00"
    )


def test_weekly_zone_is_canonical_and_after_is_strict(state):
    definition = RoutineDefinition.model_validate(
        state[4].model_dump()
        | {
            "timezone": "America/Sao_Paulo",
            "schedule": {"frequency": "weekly", "time": "09:00", "weekdays": [0, 4]},
        }
    )
    assert next_run(definition, instant("2026-01-02T12:00:00+00:00")) == instant(
        "2026-01-05T12:00:00+00:00"
    )
    with pytest.raises(ValueError, match="timezone"):
        next_run(definition, datetime(2026, 1, 1))


def test_occurrences_are_durable_idempotent_and_reads_have_no_effect(state):
    db, agent, _, _, _, service = state
    routine = create(state)
    assert routine.origin == "human" and routine.next_run_at == instant("2026-01-01T09:00:00+00:00")
    for _ in range(3):
        assert service.get(agent.id, routine.id) == routine
        assert service.list(agent.id) == [routine]
        assert service.occurrences(agent.id, routine.id) == []
    first = evaluate(state, routine, "2026-01-01T09:00:15+00:00")
    assert len(first) == 1 and first[0].status == "pending"
    assert first[0].scheduled_at == first[0].window_end == routine.next_run_at
    assert evaluate(state, routine, "2026-01-01T09:00:15+00:00") == []
    assert service.get(agent.id, routine.id).revision == routine.revision
    reopened = RoutineService(Database(db.path))
    assert reopened.occurrences(agent.id, routine.id) == first
    with db.transaction(write=False) as connection:
        assert connection.execute("SELECT count(*) FROM tasks").get == 0
        assert connection.execute("SELECT count(*) FROM tool_grants").get == 0
    # Mesmo conteúdo e conteúdo divergente não podem duplicar a identidade temporal.
    for change in ("reason", "status"):
        with pytest.raises(apsw.ConstraintError):
            with db.transaction() as connection:
                connection.execute(
                    "INSERT INTO routine_occurrences SELECT ?,routine_id,routine_revision,"
                    "scheduled_at,window_end,status,reason,task_id,revision,created_at,updated_at "
                    "FROM routine_occurrences WHERE id=?",
                    (str(uuid4()), str(first[0].id)),
                )
        with pytest.raises(apsw.ConstraintError):
            with db.transaction() as connection:
                connection.execute(
                    f"UPDATE routine_occurrences SET {change}=? WHERE id=?",
                    ("failed" if change == "status" else "divergent", str(first[0].id)),
                )


def test_indefinite_downtime_is_bounded_and_skipped_window_preserves_evidence(state):
    _, agent, _, _, _, service = state
    routine = create(state)
    late = evaluate(state, routine, "2046-01-01T10:00:00+00:00")
    assert len(late) == 1
    assert late[0].status == "skipped" and late[0].reason == "misfire"
    assert late[0].scheduled_at == instant("2026-01-01T09:00:00+00:00")
    assert late[0].window_end == instant("2046-01-01T09:00:00+00:00")
    assert service.get(agent.id, routine.id).next_run_at == instant("2046-01-02T09:00:00+00:00")
    assert evaluate(state, routine, "2027-01-01T09:00:00+00:00") == []
    assert service.occurrences(agent.id, routine.id) == late


def test_long_gap_only_materializes_latest_within_grace(state):
    routine = create(state)
    rows = evaluate(state, routine, "2046-01-01T09:00:15+00:00")
    assert [row.status for row in rows] == ["skipped", "pending"]
    assert rows[0].window_end == instant("2045-12-31T09:00:00+00:00")
    assert rows[1].scheduled_at == instant("2046-01-01T09:00:00+00:00")
    assert len(state[-1].occurrences(state[1].id, routine.id)) == 2


def test_pending_running_and_unknown_prevent_overlap_across_restarts(state):
    db, _, _, _, _, _ = state
    routine = create(state)
    pending = evaluate(state, routine, "2026-01-01T09:00:00+00:00")[0]
    assert evaluate(state, routine, "2026-01-02T09:00:00+00:00")[0].reason == "overlap"
    with db.transaction() as connection:
        connection.execute(
            "UPDATE routine_occurrences SET status='running',revision=revision+1 WHERE id=?",
            (str(pending.id),),
        )
    assert evaluate(state, routine, "2026-01-03T09:00:00+00:00")[0].reason == "overlap"
    with db.transaction() as connection:
        connection.execute(
            "UPDATE routine_occurrences SET status='outcome_unknown',revision=revision+1 "
            "WHERE id=?",
            (str(pending.id),),
        )
    assert evaluate(state, routine, "2026-01-04T09:00:00+00:00")[0].reason == "overlap"


def test_mutation_replay_cas_isolation_and_edit_pause_boundary(state):
    db, agent, other, _, definition, service = state
    command = CreateRoutine(client_request_id=uuid4(), definition=definition, active=True)
    routine = service.create(agent.id, command)
    assert service.create(agent.id, command) == routine
    with pytest.raises(RevisionConflict):
        service.create(other.id, command)
    with pytest.raises(NotFoundError):
        service.get(other.id, routine.id)
    pending = evaluate(state, routine, "2026-01-01T09:00:00+00:00")[0]
    edit = ReviseRoutine(client_request_id=uuid4(), expected_revision=1, definition=definition)
    changed = service.revise(agent.id, routine.id, edit)
    assert changed.revision == 2 and changed.status == "paused" and changed.next_run_at is None
    assert service.revise(agent.id, routine.id, edit) == changed
    assert service.create(agent.id, command) == routine
    cancelled = service.occurrences(agent.id, routine.id)[0]
    assert cancelled.id == pending.id and cancelled.status == "cancelled"
    assert cancelled.reason == "routine_changed"
    with pytest.raises(RevisionConflict):
        service.set_status(
            agent.id,
            routine.id,
            SetRoutineStatus(client_request_id=uuid4(), expected_revision=1, status="active"),
        )
    with pytest.raises(RevisionConflict):
        evaluate(state, routine, "2026-01-02T09:00:00+00:00")
    assert evaluate(state, changed, "2026-01-02T09:00:00+00:00") == []
    # A edição em relógio recuado não agenda de novo o instante já materializado.
    resumed = service.set_status(
        agent.id,
        routine.id,
        SetRoutineStatus(client_request_id=uuid4(), expected_revision=2, status="active"),
    )
    assert resumed.next_run_at == instant("2026-01-02T09:00:00+00:00")
    with db.transaction(write=False) as connection:
        assert connection.execute("SELECT count(*) FROM routine_definitions").get == 3


def test_skill_disable_prevents_new_work_without_fallback(state):
    db, agent, _, skill, _, _ = state
    routine = create(state)
    skills = SkillService(db)
    revised = skills.revise(
        agent.id,
        skill.id,
        ReviseSkill(
            client_request_id=uuid4(),
            expected_revision=2,
            definition=skill.current.definition.model_copy(update={"name": "Nova revisão"}),
        ),
    )
    assert revised.enabled_version is None
    skipped = evaluate(state, routine, "2026-01-01T09:00:00+00:00")[0]
    assert skipped.status == "skipped" and skipped.reason == "skill_unavailable"
    with pytest.raises(RevisionConflict):
        state[-1].set_status(
            agent.id,
            routine.id,
            SetRoutineStatus(client_request_id=uuid4(), expected_revision=1, status="active"),
        )


def test_concurrent_materialization_and_cas_serialize(state):
    _, agent, _, _, definition, service = state
    routine = create(state)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(lambda _: evaluate(state, routine, "2026-01-01T09:00:00+00:00"), range(2))
        )
    assert sorted(map(len, results)) == [0, 1]

    def revise(_):
        try:
            return service.revise(
                agent.id,
                routine.id,
                ReviseRoutine(
                    client_request_id=uuid4(), expected_revision=1, definition=definition
                ),
            )
        except RevisionConflict:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(revise, range(2)))
    assert sum(item is not None for item in results) == 1


def test_legacy_is_readable_but_never_implicitly_operational(state):
    db, agent, _, _, definition, service = state
    with StateStore(db).transaction() as unit:
        legacy = unit.routines.create(
            Routine(
                agent_id=agent.id,
                name="Metadado antigo",
                instructions="Antigo",
                schedule={"cron": "* * * * *"},
                misfire_policy="catch_up",
                overlap_policy="queue",
            )
        )
    view = service.get(agent.id, legacy.id)
    assert view.origin == "legacy" and view.definition is None and view.status == "active"
    assert evaluate(state, view, "2026-01-01T09:00:00+00:00") == []
    with pytest.raises(RevisionConflict, match="legada"):
        service.set_status(
            agent.id,
            legacy.id,
            SetRoutineStatus(client_request_id=uuid4(), expected_revision=1, status="active"),
        )
    changed = service.revise(
        agent.id,
        legacy.id,
        ReviseRoutine(client_request_id=uuid4(), expected_revision=1, definition=definition),
    )
    assert changed.origin == "human" and changed.status == "paused"


@pytest.mark.parametrize(
    "change",
    [
        {"timezone": "Invalid/Zone"},
        {"misfire_policy": "catch_up"},
        {"overlap_policy": "queue"},
        {"schedule": {"frequency": "cron", "time": "09:00"}},
        {"schedule": {"frequency": "daily", "time": "24:00"}},
        {"schedule": {"frequency": "weekly", "time": "09:00", "weekdays": []}},
        {"schedule": {"frequency": "weekly", "time": "09:00", "weekdays": [1, 1]}},
        {"schedule": {"frequency": "daily", "time": "09:00", "weekdays": [1]}},
        {"grace_seconds": 3601},
        {"grace_seconds": True},
        {"inputs": {"tema": "sk-or-v1-" + "x" * 30}},
        {"destination": {"kind": "email", "address": "test@example.com"}},
        {"origin": "model"},
        {"environment": "fallback"},
    ],
)
def test_unsupported_and_unsafe_contracts_are_rejected(state, change):
    with pytest.raises(ValidationError):
        RoutineDefinition.model_validate(state[4].model_dump() | change)


def test_inputs_environment_and_destination_must_match_pinned_skill(state):
    _, agent, other, _, definition, service = state
    with service.store.transaction() as unit:
        foreign = unit.conversations.create(Conversation(agent_id=other.id))
    for change, error in (
        ({"inputs": {}}, ValueError),
        ({"inputs": {"tema": "A", "undeclared": "B"}}, ValueError),
        ({"environment": "personal"}, ValueError),
        ({"destination": {"conversation_id": foreign.id}}, NotFoundError),
    ):
        value = RoutineDefinition.model_validate(definition.model_dump() | change)
        with pytest.raises(error):
            service.create(agent.id, CreateRoutine(client_request_id=uuid4(), definition=value))


def test_migration_preserves_legacy_and_backs_up_before_new_tables(tmp_path):
    db = Database(tmp_path / "legacy.sqlite3")
    migrations = db._migrations()
    original = db._migrations
    db._migrations = lambda: migrations[:14]
    db.initialize()
    with StateStore(db).transaction() as unit:
        agent = unit.agents.create(Agent(name="Anterior"))
        routine = unit.routines.create(
            Routine(
                agent_id=agent.id,
                name="Conservar",
                instructions="Metadados",
                schedule={"legacy": "value"},
                timezone="America/New_York",
                misfire_policy="catch_up",
                overlap_policy="queue",
            )
        )
    db._migrations = original
    db.initialize()
    with StateStore(db).transaction(write=False) as unit:
        assert unit.routines.get(routine.id) == routine
    with db.transaction(write=False) as connection:
        assert connection.execute("SELECT count(*) FROM routine_cursors").get == 0
        assert connection.execute("SELECT count(*) FROM routine_definitions").get == 0
        assert list(connection.execute("PRAGMA foreign_key_check")) == []
    backup = list(tmp_path.glob("legacy.sqlite3.backup-*.sqlite3"))
    assert len(backup) == 1
    saved = apsw.Connection(str(backup[0]), flags=apsw.SQLITE_OPEN_READONLY)
    try:
        assert saved.execute("SELECT schedule_json FROM routines").get == '{"legacy":"value"}'
        assert saved.execute("SELECT max(version) FROM schema_migrations").get == 14
    finally:
        saved.close()


def test_materialization_failure_rolls_back_cursor_and_all_evidence(state):
    db, agent, _, _, _, service = state
    routine = create(state)
    with db.transaction() as connection:
        connection.execute(
            "CREATE TRIGGER simulated_routine_disk_failure BEFORE INSERT ON routine_occurrences "
            "BEGIN SELECT RAISE(ABORT,'simulated disk failure'); END"
        )
    with pytest.raises(apsw.ConstraintError):
        evaluate(state, routine, "2026-01-01T09:00:00+00:00")
    assert service.get(agent.id, routine.id).next_run_at == routine.next_run_at
    assert service.occurrences(agent.id, routine.id) == []
    with db.transaction() as connection:
        connection.execute("DROP TRIGGER simulated_routine_disk_failure")
    assert len(evaluate(state, routine, "2026-01-01T09:00:00+00:00")) == 1


def test_definition_history_is_immutable_and_cancellation_is_terminal(state):
    db, agent, _, _, definition, service = state
    routine = create(state)
    changed = service.revise(
        agent.id,
        routine.id,
        ReviseRoutine(
            client_request_id=uuid4(),
            expected_revision=1,
            definition=definition.model_copy(update={"name": "Revisada"}),
        ),
    )
    history = service.revisions(agent.id, routine.id)
    assert [item.revision for item in history] == [2, 1]
    assert history[1].definition == definition
    assert history[0].definition == changed.definition
    assert service.revisions(agent.id, routine.id, limit=1, offset=1) == history[1:]
    with pytest.raises(apsw.ConstraintError):
        with db.transaction() as connection:
            connection.execute("UPDATE routine_definitions SET origin='human'")
    cancelled = service.set_status(
        agent.id,
        routine.id,
        SetRoutineStatus(client_request_id=uuid4(), expected_revision=2, status="cancelled"),
    )
    assert cancelled.next_run_at is None and cancelled.status == "cancelled"
    with pytest.raises(RevisionConflict, match="terminal"):
        service.set_status(
            agent.id,
            routine.id,
            SetRoutineStatus(client_request_id=uuid4(), expected_revision=3, status="active"),
        )
