"""Fatos atômicos, leitura humana durável e avisos sem conteúdo privado."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from importlib import resources
from uuid import uuid4

import apsw
import httpx
import pytest
from test_execution_approvals import approve, waiting
from test_tasks_execution import answer, fixture_state, task_input

from bees_core import attention as attention_module
from bees_core.attention import AttentionService, PreferenceInput, ReadInput, project_event
from bees_core.execution import TaskWorker
from bees_core.models import Agent, Run, Task
from bees_core.storage import database as database_module
from bees_core.storage.database import Database
from bees_core.storage.store import NotFoundError, RevisionConflict, StateStore


def done_state(tmp_path):
    db, store, agent, _, tasks = fixture_state(tmp_path)
    task = tasks.create(agent.id, task_input())
    assert asyncio.run(
        TaskWorker(db, transport=httpx.MockTransport(lambda request: answer())).run_once()
    )
    return db, store, agent, task, tasks, AttentionService(db)


def snapshot(db):
    with db.transaction(write=False) as connection:
        return {
            table: list(connection.execute(f"SELECT * FROM {table}"))
            for table in (
                "attention_items",
                "attention_preferences",
                "attention_commands",
                "domain_events",
            )
        }


def test_worker_completion_persists_sanitized_notice_and_get_is_read_only(tmp_path):
    db, _, agent, task, tasks, service = done_state(tmp_path)
    page = service.list(agent.id)
    assert page.unread_count == 1 and page.next_before is None
    item = page.items[0]
    assert item.kind == "task_completed" and item.task_id == task.id
    assert item.agent_id == agent.id and item.requires_attention
    assert not item.read and not item.muted
    before = snapshot(db)
    reopened = AttentionService(Database(db.path))
    assert reopened.list(agent.id) == page
    assert all(
        pref["enabled"] and pref["revision"] == 0
        for pref in reopened.preferences(agent.id)["items"]
    )
    assert snapshot(db) == before
    payload = item.model_dump_json()
    assert item.task_title == task.title
    assert task.objective not in payload and "Resultado" not in payload
    assert tasks.detail(agent.id, task.id).messages[-1].content == "Resultado útil"


def test_projector_failure_rolls_back_entity_and_event_even_if_caught_inside_uow(
    tmp_path, monkeypatch
):
    db, store, agent, _, tasks = fixture_state(tmp_path)
    task = tasks.create(agent.id, task_input())
    with db.transaction(write=False) as connection:
        event_count = connection.execute("SELECT count(*) FROM domain_events").get

    def crash(*args):
        raise RuntimeError("Falha de armazenamento após evento")

    monkeypatch.setattr(attention_module, "project_event", crash)
    with store.transaction() as unit:
        current = unit.tasks.get(task.id)
        with pytest.raises(RuntimeError):
            unit.tasks.update(current.model_copy(update={"status": "failed"}), current.revision)
        assert unit.tasks.get(task.id) == current
    with db.transaction(write=False) as connection:
        assert connection.execute("SELECT count(*) FROM domain_events").get == event_count
        assert connection.execute("SELECT count(*) FROM attention_items").get == 0


def test_projection_is_deduplicated_by_canonical_identity(tmp_path):
    db, store, agent, task, _, service = done_state(tmp_path)
    with store.transaction() as unit:
        current = unit.tasks.get(task.id)
        unit.tasks.update(
            current.model_copy(update={"title": "Outro título privado"}), current.revision
        )
    with db.transaction() as connection:
        event = connection.execute(
            "SELECT id FROM domain_events WHERE entity_type='task' AND entity_id=? "
            "AND json_extract(payload_json,'$.status')='completed' ORDER BY seq LIMIT 1",
            (str(task.id),),
        ).get
        project_event(connection, current, None, event)
    assert len(service.list(agent.id).items) == 1
    assert service.list(agent.id).unread_count == 1


def test_outer_rollback_leaves_no_phantom_notice(tmp_path):
    db, store, agent, _, tasks = fixture_state(tmp_path)
    task = tasks.create(agent.id, task_input())
    with pytest.raises(RuntimeError):
        with store.transaction() as unit:
            current = unit.tasks.get(task.id)
            unit.tasks.update(current.model_copy(update={"status": "failed"}), current.revision)
            raise RuntimeError("Rollback após projeção")
    assert AttentionService(db).list(agent.id).items == []


def test_read_replay_returns_current_state_and_never_authorizes_task(tmp_path):
    db, _, agent, task, tasks, service = done_state(tmp_path)
    item = service.list(agent.id).items[0]
    request = ReadInput(client_request_id=uuid4(), expected_revision=1, read=True)
    assert service.set_read(agent.id, item.id, request).revision == 2
    assert service.list(agent.id).unread_count == 0
    current = service.set_read(
        agent.id, item.id, ReadInput(client_request_id=uuid4(), expected_revision=2, read=False)
    )
    assert current.revision == 3
    assert service.set_read(agent.id, item.id, request) == current
    with pytest.raises(RevisionConflict):
        service.set_read(agent.id, item.id, request.model_copy(update={"read": False}))
    with pytest.raises(RevisionConflict):
        service.set_read(
            agent.id, item.id, request.model_copy(update={"client_request_id": uuid4()})
        )
    assert tasks.detail(agent.id, task.id).task.status == "completed"
    with StateStore(db).transaction(write=False) as unit:
        audit = unit.events.list(entity_type="attention", entity_id=item.id)
    assert len(audit) == 2
    assert all(event.actor == "user" and "read" in event.payload for event in audit)


def test_concurrent_read_cas_accepts_only_one_writer(tmp_path):
    db, _, agent, _, _, service = done_state(tmp_path)
    item = service.list(agent.id).items[0]

    def write(_):
        try:
            return (
                AttentionService(Database(db.path))
                .set_read(
                    agent.id,
                    item.id,
                    ReadInput(client_request_id=uuid4(), expected_revision=1, read=True),
                )
                .revision
            )
        except RevisionConflict:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(write, range(2)), key=str) == [2, "conflict"]
    assert service.list(agent.id).items[0].revision == 2


def test_silencing_preserves_notice_and_audit_and_preference_replay(tmp_path):
    db, _, agent, _, _, service = done_state(tmp_path)
    request = PreferenceInput(client_request_id=uuid4(), expected_revision=0, enabled=False)
    preference = service.set_preference(agent.id, "task_completed", request)
    assert preference.revision == 1 and not preference.enabled
    assert service.list(agent.id).unread_count == 0
    assert len(service.list(agent.id).items) == 1 and service.list(agent.id).items[0].muted
    service.set_preference(
        agent.id,
        "task_completed",
        PreferenceInput(client_request_id=uuid4(), expected_revision=1, enabled=True),
    )
    assert service.set_preference(agent.id, "task_completed", request).enabled
    assert service.set_preference(agent.id, "task_completed", request).revision == 2
    with pytest.raises(RevisionConflict):
        service.set_preference(agent.id, "task_failed", request)
    with pytest.raises(RevisionConflict):
        service.set_preference(
            agent.id, "task_completed", request.model_copy(update={"client_request_id": uuid4()})
        )
    with StateStore(db).transaction(write=False) as unit:
        assert len(unit.events.list(entity_type="attention_preference")) == 2


def test_read_and_uuid_replay_cannot_cross_agent_binding(tmp_path):
    db, store, agent, _, _, service = done_state(tmp_path)
    another = Agent(name="Outra abelha")
    with store.transaction() as unit:
        unit.agents.create(another)
    item = service.list(agent.id).items[0]
    request = ReadInput(client_request_id=uuid4(), expected_revision=1, read=True)
    service.set_read(agent.id, item.id, request)
    with pytest.raises(NotFoundError):
        service.set_read(another.id, item.id, request)
    assert service.list(another.id).items == []
    preference = PreferenceInput(client_request_id=uuid4(), expected_revision=0, enabled=False)
    service.set_preference(agent.id, "task_failed", preference)
    with pytest.raises(RevisionConflict):
        service.set_preference(another.id, "task_failed", preference)


def test_pagination_keeps_old_cursor_stable_during_new_events(tmp_path):
    db, store, agent, _, tasks = fixture_state(tmp_path)

    def fail():
        task = tasks.create(agent.id, task_input())
        with store.transaction() as unit:
            unit.tasks.update(task.model_copy(update={"status": "failed"}), task.revision)

    for _ in range(4):
        fail()
    service = AttentionService(db)
    first = service.list(agent.id, limit=2)
    fail()
    second = service.list(agent.id, limit=2, before=first.next_before)
    assert second.next_before is None
    assert (
        len(first.items + second.items)
        == len({item.id for item in first.items + second.items})
        == 4
    )
    assert service.list(agent.id).unread_count == 5
    with pytest.raises(ValueError):
        service.list(agent.id, before=True)


def test_two_failed_runs_of_same_task_produce_two_notices_without_replay(tmp_path):
    from bees_core.tasks import TaskCommandInput

    db, store, agent, _, tasks = fixture_state(tmp_path)
    task = tasks.create(agent.id, task_input())
    requests = []
    worker = TaskWorker(
        db,
        transport=httpx.MockTransport(
            lambda request: requests.append(request) or answer(finish="length")
        ),
    )
    assert asyncio.run(worker.run_once())
    first = tasks.detail(agent.id, task.id)
    assert first.task.status == "paused" and first.runs[-1].error == "response_truncated"
    notices = AttentionService(db).list(agent.id)
    assert len(notices.items) == 1 and notices.items[0].kind == "task_failed"
    assert not asyncio.run(worker.run_once())
    tasks.command(
        agent.id,
        task.id,
        TaskCommandInput(
            client_request_id=uuid4(), expected_revision=first.task.revision, kind="resume"
        ),
    )
    assert asyncio.run(worker.run_once())
    second = tasks.detail(agent.id, task.id)
    assert second.runs[-1].id != first.runs[-1].id
    with store.transaction() as unit:
        run = unit.runs.get(second.runs[-1].id)
        unit.runs.update(
            run.model_copy(update={"metadata": {"note": "Atualização privada"}}), run.revision
        )
    notices = AttentionService(db).list(agent.id)
    assert len(notices.items) == notices.unread_count == 2
    assert all(item.kind == "task_failed" and item.task_id == task.id for item in notices.items)
    assert len(requests) == 2


@pytest.mark.parametrize("change", ["approved", "expired", "policy_changed", "cancelled"])
def test_pending_decision_stops_requiring_attention_without_mutating_history(tmp_path, change):
    db, store, agent, tasks, policies, policy, task, approvals, pending = waiting(tmp_path)
    service = AttentionService(db)
    page = service.list(agent.id)
    assert page.unread_count == 1 and page.items[0].requires_attention
    if change == "approved":
        approve(approvals, agent, pending)
    elif change == "expired":
        with store.transaction() as unit:
            action = unit.actions.get(pending.action_id)
            unit.actions.update(
                action.model_copy(
                    update={
                        "metadata": action.metadata | {"expires_at": "2000-01-01T00:00:00+00:00"}
                    }
                ),
                action.revision,
            )
    elif change == "policy_changed":
        from bees_core.policies import PolicyUpdate

        policies.update(
            agent.id,
            policy.id,
            PolicyUpdate(expected_revision=policy.revision, reason="Revisão humana"),
        )
    else:
        from bees_core.tasks import TaskCommandInput

        current = tasks.detail(agent.id, task.id).task
        tasks.command(
            agent.id,
            task.id,
            TaskCommandInput(
                client_request_id=uuid4(), expected_revision=current.revision, kind="cancel"
            ),
        )
    before = snapshot(db)
    current = service.list(agent.id)
    assert current.unread_count == 0 and not current.items[0].requires_attention
    assert current.items[0].id == page.items[0].id and current.items[0].revision == 1
    assert snapshot(db) == before


def test_silenced_pending_and_unknown_remain_accessible_without_retry(tmp_path):
    db, _, agent, tasks, _, _, task, approvals, pending = waiting(tmp_path)
    service = AttentionService(db)
    service.set_preference(
        agent.id,
        "approval_required",
        PreferenceInput(client_request_id=uuid4(), expected_revision=0, enabled=False),
    )
    assert service.list(agent.id).items[0].requires_attention
    assert service.list(agent.id).items[0].muted and service.list(agent.id).unread_count == 0
    approve(approvals, agent, pending)
    requests = []

    def lost(request):
        requests.append(request)
        raise httpx.ReadError("Perda após envio", request=request)

    worker = TaskWorker(db, transport=httpx.MockTransport(lost))
    assert asyncio.run(worker.run_once())
    assert not asyncio.run(worker.run_once())
    unknown = [item for item in service.list(agent.id).items if item.kind == "outcome_unknown"]
    assert len(unknown) == 1 and unknown[0].requires_attention
    assert service.list(agent.id).unread_count == 1
    service.set_read(
        agent.id,
        unknown[0].id,
        ReadInput(client_request_id=uuid4(), expected_revision=1, read=True),
    )
    assert service.list(agent.id).items[0].requires_attention
    assert service.list(agent.id).unread_count == 0
    assert len(requests) == 1 and tasks.detail(agent.id, task.id).task.status == "paused"


def test_human_denial_resolves_notice_without_new_failure_or_worker_effect(tmp_path):
    db, store, agent, tasks, _, _, task, approvals, pending = waiting(tmp_path)
    attention = AttentionService(db)
    original = attention.list(agent.id).items[0]
    denied = approve(approvals, agent, pending, decision="deny")
    assert denied.status == "denied"
    requests = []
    worker = TaskWorker(
        db, transport=httpx.MockTransport(lambda request: requests.append(request) or answer())
    )
    assert not asyncio.run(worker.run_once())
    current = attention.list(agent.id)
    assert current.unread_count == 0 and len(current.items) == 1
    assert current.items[0].id == original.id
    assert current.items[0].kind == "approval_required"
    assert not current.items[0].requires_attention
    assert not any(item.kind == "task_failed" for item in current.items)
    detail = tasks.detail(agent.id, task.id)
    assert detail.task.status == "paused" and detail.runs[-1].error == "approval_denied"
    assert detail.task.calls_started == 0 and not requests
    with store.transaction(write=False) as unit:
        events = unit.events.list(entity_type="approval", entity_id=pending.id)
        assert events[-1].actor == "user" and events[-1].payload["status"] == "denied"


def test_current_approval_remains_visible_after_more_than_1000_historical_runs(tmp_path):
    from datetime import UTC, datetime

    db, store, agent, _, _, _, task, _, _ = waiting(tmp_path)
    old = datetime(2000, 1, 1, tzinfo=UTC)
    with store.transaction() as unit:
        for _ in range(1001):
            unit.runs.create(
                Run(task_id=task.id, created_at=old, updated_at=old, status="cancelled")
            )
    page = AttentionService(db).list(agent.id)
    assert page.unread_count == 1 and page.items[0].requires_attention


def test_database_rejects_tampered_bindings_identity_and_deletion(tmp_path):
    db, store, agent, task, _, service = done_state(tmp_path)
    another = Agent(name="Outra")
    with store.transaction() as unit:
        unit.agents.create(another)
    item = service.list(agent.id).items[0]
    with db.transaction() as connection:
        for sql, params in (
            (
                "UPDATE attention_items SET task_id=?,revision=revision+1 WHERE id=?",
                (str(uuid4()), str(item.id)),
            ),
            ("DELETE FROM attention_items WHERE id=?", (str(item.id),)),
        ):
            with pytest.raises(apsw.ConstraintError):
                connection.execute(sql, params)
        event = connection.execute(
            "SELECT source_event_id FROM attention_items WHERE id=?", (str(item.id),)
        ).get
        with pytest.raises(apsw.ConstraintError):
            connection.execute(
                "INSERT INTO attention_items(id,agent_id,task_id,kind,entity_type,entity_id,"
                "source_event_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    str(uuid4()),
                    str(another.id),
                    str(task.id),
                    "task_failed",
                    "task",
                    str(task.id),
                    event,
                    item.created_at.isoformat(),
                    item.created_at.isoformat(),
                ),
            )
    assert service.list(agent.id).items[0] == item


def test_upgrade_11_to_12_preserves_canonical_rows_and_starts_without_backfill(
    tmp_path, monkeypatch
):
    folder = tmp_path / "migrations"
    folder.mkdir()
    sources = resources.files("bees_core.storage.migrations")
    for file in sources.iterdir():
        if file.name.endswith(".sql") and int(file.name[:4]) < 12:
            (folder / file.name).write_bytes(file.read_bytes())
    monkeypatch.setattr(database_module.resources, "files", lambda _: folder)
    db = Database(tmp_path / "state.sqlite3")
    db.initialize()
    assert db.schema_version() == 11
    agent = Agent(name="Histórico")
    task = Task(agent_id=agent.id, title="Título legado", objective="Privado", status="completed")
    with db.transaction() as connection:
        connection.execute(
            "INSERT INTO agents(id,name,created_at,updated_at) VALUES(?,?,?,?)",
            (str(agent.id), agent.name, agent.created_at.isoformat(), agent.updated_at.isoformat()),
        )
        connection.execute(
            "INSERT INTO tasks(id,agent_id,title,objective,status,created_at,"
            "updated_at) VALUES(?,?,?,?,?,?,?)",
            (
                str(task.id),
                str(agent.id),
                task.title,
                task.objective,
                task.status,
                task.created_at.isoformat(),
                task.updated_at.isoformat(),
            ),
        )
        before = list(connection.execute("SELECT * FROM tasks"))
    (folder / "0012_attention.sql").write_bytes((sources / "0012_attention.sql").read_bytes())
    db.initialize()
    assert db.schema_version() == 12 and len(list(tmp_path.glob("*.backup-*.sqlite3"))) == 1
    with db.transaction(write=False) as connection:
        assert list(connection.execute("SELECT * FROM tasks")) == before
        assert connection.execute("PRAGMA integrity_check").get == "ok"
        assert list(connection.execute("PRAGMA foreign_key_check")) == []
    assert AttentionService(db).list(agent.id).items == []
