"""Scoped keyset pages never turn cursors into authority or read effect payloads."""

from datetime import timedelta
from uuid import UUID, uuid4

import apsw
import pytest
from test_recovery_inventory import PRIVATE, inventory_fixture

from bees_core.models import Action, Run, Task, TaskCommand, utc_now
from bees_core.recovery import RecoveryInventoryService
from bees_core.storage.store import NotFoundError


def test_pages_include_all_runs_and_keep_ties_stable_without_private_payloads(tmp_path):
    db, store, agent, task, run = inventory_fixture(tmp_path)
    stamp = utc_now()
    ids = [UUID(int=n) for n in range(1, 105)]
    with store.transaction() as unit:
        older = unit.runs.create(Run(task_id=task.id))
        for index, identity in enumerate(reversed(ids)):
            unit.actions.create(
                Action(
                    id=identity,
                    run_id=run.id if index % 2 else older.id,
                    tool_name="fixture",
                    created_at=stamp,
                    parameters={"private": PRIVATE},
                    result={"private": PRIVATE},
                    metadata={"private": PRIVATE},
                )
            )
    service = RecoveryInventoryService(db)
    pages = [service.actions(agent.id, task.id)]
    while pages[-1].has_more:
        pages.append(service.actions(agent.id, task.id, after=pages[-1].next_cursor))
    assert [len(page.items) for page in pages] == [50, 50, 4]
    assert [item.id for page in pages for item in page.items] == ids
    assert pages[-1].next_cursor is None
    assert all(page.executor_evidence == "not_inspected" for page in pages)
    assert all(PRIVATE not in page.model_dump_json() for page in pages)


def test_foreign_missing_and_moved_scope_cursor_fail_closed(tmp_path):
    db, store, agent, task, run = inventory_fixture(tmp_path)
    with store.transaction() as unit:
        own = unit.actions.create(Action(run_id=run.id, tool_name="own"))
        other_task = unit.tasks.create(Task(agent_id=agent.id, title="Other", objective="Private"))
        other_run = unit.runs.create(Run(task_id=other_task.id))
        other = unit.actions.create(Action(run_id=other_run.id, tool_name="other"))
    service = RecoveryInventoryService(db)
    for after in [other.id, uuid4()]:
        with pytest.raises(NotFoundError):
            service.actions(agent.id, task.id, after=after)
    with pytest.raises(NotFoundError):
        service.actions(uuid4(), task.id, after=own.id)
    assert service.actions(agent.id, task.id, after=own.id).items == []


def test_empty_and_limits_are_explicit(tmp_path):
    db, _, agent, task, _ = inventory_fixture(tmp_path)
    service = RecoveryInventoryService(db)
    page = service.actions(agent.id, task.id)
    assert page.items == [] and not page.has_more and page.next_cursor is None
    for limit in [0, 101, True, 1.5, "50"]:
        with pytest.raises(ValueError):
            service.actions(agent.id, task.id, limit=limit)


def test_each_page_is_fresh_and_acknowledgement_does_not_reorder_history(tmp_path):
    db, store, agent, task, run = inventory_fixture(tmp_path)
    stamp = utc_now() - timedelta(seconds=2)
    with store.transaction() as unit:
        first = unit.actions.create(Action(run_id=run.id, tool_name="one", created_at=stamp))
        second = unit.actions.create(
            Action(
                run_id=run.id,
                tool_name="two",
                created_at=stamp + timedelta(seconds=1),
                status="outcome_unknown",
            )
        )
    service = RecoveryInventoryService(db)
    page = service.actions(agent.id, task.id, limit=1)
    assert page.items[0].id == first.id
    with store.transaction() as unit:
        command = unit.task_commands.create(
            TaskCommand(
                task_id=task.id,
                client_request_id=uuid4(),
                kind="resume",
                expected_revision=task.revision,
                payload={"acknowledge_unknown": True},
            )
        )
        unit.execution.acknowledge_unknown(task.id, command_id=command.id, now=utc_now())
    following = service.actions(agent.id, task.id, after=page.next_cursor, limit=1)
    assert following.items[0].id == second.id
    assert (
        following.items[0].status == "outcome_unknown" and following.items[0].unknown_acknowledged
    )
    assert following.items[0].revision == 2
    assert following.observed_at >= page.observed_at


def test_page_reader_is_query_only_and_does_not_access_private_columns(tmp_path, monkeypatch):
    db, store, agent, task, run = inventory_fixture(tmp_path)
    with store.transaction() as unit:
        unit.actions.create(Action(run_id=run.id, tool_name="one", parameters={"secret": PRIVATE}))
    original = db.connect

    def connect():
        connection = original()

        def authorize(operation, table, column, database, trigger):
            if operation == apsw.SQLITE_READ and column in {
                "parameters_json",
                "result_json",
                "metadata_json",
                "execution_binding_json",
            }:
                return apsw.SQLITE_DENY
            if operation in {apsw.SQLITE_INSERT, apsw.SQLITE_UPDATE, apsw.SQLITE_DELETE}:
                return apsw.SQLITE_DENY
            return apsw.SQLITE_OK

        connection.set_authorizer(authorize)
        return connection

    monkeypatch.setattr(db, "connect", connect)
    page = RecoveryInventoryService(db).actions(agent.id, task.id)
    assert len(page.items) == 1 and PRIVATE not in page.model_dump_json()
