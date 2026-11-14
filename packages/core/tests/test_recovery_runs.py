"""Run pages expose complete canonical history without opening private run state."""

from typing import get_args
from uuid import UUID, uuid4

import apsw
import pytest
from test_recovery_inventory import PRIVATE, inventory_fixture

from bees_core.models import Run, Task, TaskStatus, utc_now
from bees_core.recovery import RecoveryInventoryService
from bees_core.storage.store import NotFoundError


def test_more_than_one_thousand_runs_with_tied_dates_have_no_gaps(tmp_path):
    db, store, agent, task, run = inventory_fixture(tmp_path)
    stamp = utc_now()
    ids = [UUID(int=n) for n in range(1, 1005)]
    with store.transaction() as unit:
        for identity in reversed(ids):
            unit.runs.create(Run(id=identity, task_id=task.id, created_at=stamp))
    service = RecoveryInventoryService(db)
    cursor, found, sizes = run.id, [], []
    # Existing first run predates the tied history; cursor is its exact identity.
    while True:
        page = service.runs(agent.id, task.id, after=cursor)
        sizes.append(len(page.items))
        found.extend(item.id for item in page.items)
        assert page.scope == "all_canonical_task_runs"
        assert page.executor_evidence == "not_inspected"
        if not page.has_more:
            assert page.next_cursor is None
            break
        cursor = page.next_cursor
    assert found == ids and sizes == [50] * 20 + [4]
    assert service.inspect(agent.id, task.id).runs_total == 1005


def test_scope_cursors_and_empty_history_are_distinct(tmp_path):
    db, store, agent, task, run = inventory_fixture(tmp_path)
    with store.transaction() as unit:
        other = unit.tasks.create(Task(agent_id=agent.id, title="Other", objective=PRIVATE))
        foreign = unit.runs.create(Run(task_id=other.id))
        empty = unit.tasks.create(Task(agent_id=agent.id, title="Empty", objective=PRIVATE))
    service = RecoveryInventoryService(db)
    for identity in [foreign.id, uuid4()]:
        with pytest.raises(NotFoundError):
            service.runs(agent.id, task.id, after=identity)
    for owner, identity in [(uuid4(), task.id), (agent.id, uuid4())]:
        with pytest.raises(NotFoundError):
            service.runs(owner, identity, after=run.id)
    for identity, cursor in [(empty.id, None), (task.id, run.id)]:
        page = service.runs(agent.id, identity, after=cursor)
        assert page.items == [] and not page.has_more and page.next_cursor is None
    for limit in [0, 101, True, 1.5, "50"]:
        with pytest.raises(ValueError):
            service.runs(agent.id, task.id, limit=limit)
    assert len(service.runs(agent.id, task.id, limit=1).items) == 1
    assert len(service.runs(agent.id, task.id, limit=100).items) == 1


def test_query_only_projection_never_reads_private_columns_or_mutates(tmp_path, monkeypatch):
    db, store, agent, task, run = inventory_fixture(tmp_path)
    with db.transaction() as connection:
        connection.execute(
            "UPDATE runs SET checkpoint_json=?,metadata_json=?,error=?,provider=?,model=?,"
            "environment_id=? WHERE id=?",
            (
                '{"private":"' + PRIVATE + '"}',
                '{"private":"' + PRIVATE + '"}',
                PRIVATE,
                PRIVATE,
                PRIVATE,
                PRIVATE,
                str(run.id),
            ),
        )
    original = db.connect
    with original() as connection:
        before = connection.serialize("main")
    checked = []

    def connect():
        connection = original()

        def authorize(operation, table, column, database, trigger):
            if operation == apsw.SQLITE_READ and column in {
                "checkpoint_json",
                "metadata_json",
                "provider_config_json",
                "objective",
                "error",
                "provider",
                "model",
                "environment_id",
            }:
                return apsw.SQLITE_DENY
            if operation in {apsw.SQLITE_INSERT, apsw.SQLITE_UPDATE, apsw.SQLITE_DELETE}:
                return apsw.SQLITE_DENY
            return apsw.SQLITE_OK

        def trace(cursor, statement, bindings):
            if "SELECT x.id,x.revision" in statement:
                checked.append(connection.execute("PRAGMA query_only").get)
            return True

        connection.set_authorizer(authorize)
        connection.set_exec_trace(trace)
        return connection

    monkeypatch.setattr(db, "connect", connect)
    page = RecoveryInventoryService(db).runs(agent.id, task.id)
    assert len(page.items) == 1 and PRIVATE not in page.model_dump_json() and checked == [1]
    assert set(page.items[0].model_dump()) == {
        "id",
        "revision",
        "status",
        "created_at",
        "updated_at",
        "started_at",
        "finished_at",
    }
    with original() as connection:
        assert connection.serialize("main") == before


def test_page_revision_cursor_and_records_share_one_snapshot(tmp_path, monkeypatch):
    db, store, agent, task, run = inventory_fixture(tmp_path)
    with store.transaction() as unit:
        following = unit.runs.create(Run(task_id=task.id, status="running"))
    original, changed = db.connect, False

    def connect():
        connection = original()

        def trace(cursor, statement, bindings):
            nonlocal changed
            if "SELECT x.created_at,x.id" in statement and not changed:
                changed = True
                assert connection.execute("PRAGMA query_only").get == 1
                with pytest.raises(apsw.ReadOnlyError):
                    connection.execute("UPDATE tasks SET revision=revision+1")
                with db.transaction() as writer:
                    writer.execute(
                        "UPDATE tasks SET revision=revision+1 WHERE id=?", (str(task.id),)
                    )
                    writer.execute(
                        "UPDATE runs SET status='cancelled' WHERE id=?", (str(following.id),)
                    )
            return True

        connection.set_exec_trace(trace)
        return connection

    monkeypatch.setattr(db, "connect", connect)
    service = RecoveryInventoryService(db)
    page = service.runs(agent.id, task.id, after=run.id)
    assert changed and page.task_revision == task.revision and page.items[0].status == "running"
    fresh = service.runs(agent.id, task.id, after=run.id)
    assert fresh.task_revision == task.revision + 1 and fresh.items[0].status == "cancelled"


@pytest.mark.parametrize("status", get_args(TaskStatus))
def test_all_run_states_and_nullable_dates_are_observed_without_effects(tmp_path, status):
    db, store, agent, task, _ = inventory_fixture(tmp_path)
    now = utc_now()
    with store.transaction() as unit:
        run = unit.runs.create(Run(task_id=task.id, status=status, started_at=now, finished_at=now))
    page = RecoveryInventoryService(db).runs(agent.id, task.id)
    assert page.items[0].started_at is None and page.items[0].finished_at is None
    item = page.items[1]
    assert item.status == status and item.started_at == now and item.finished_at == now
    assert item.revision == run.revision and item.id == run.id
    with store.transaction(write=False) as unit:
        assert unit.runs.get(run.id) == run
