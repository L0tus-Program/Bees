"""Model-call recovery pages expose canonical state without reading private inputs."""

import json
from uuid import UUID, uuid4

import apsw
import pytest
from test_recovery_inventory import PRIVATE, inventory_fixture, model_call

from bees_core.models import Run, Task, TaskCommand, utc_now
from bees_core.recovery import RecoveryInventoryService
from bees_core.storage.store import NotFoundError


def test_all_runs_and_tied_dates_page_in_stable_order_beyond_detail_limit(tmp_path):
    db, store, agent, task, run = inventory_fixture(tmp_path)
    stamp = utc_now().isoformat()
    with store.transaction() as unit:
        older = unit.runs.create(Run(task_id=task.id))
        calls = [model_call(unit, agent, run if n % 2 else older, n) for n in range(1, 105)]
    ids = [UUID(int=n) for n in range(1, 105)]
    with db.transaction() as connection:
        for call, identity in zip(calls, reversed(ids), strict=True):
            connection.execute(
                "UPDATE model_calls SET id=?,created_at=? WHERE id=?",
                (str(identity), stamp, str(call.id)),
            )
    service = RecoveryInventoryService(db)
    pages = [service.model_calls(agent.id, task.id)]
    while pages[-1].has_more:
        pages.append(service.model_calls(agent.id, task.id, after=pages[-1].next_cursor))
    assert [len(page.items) for page in pages] == [50, 50, 4]
    assert [item.id for page in pages for item in page.items] == ids
    assert pages[-1].next_cursor is None
    assert all(page.scope == "all_canonical_task_model_calls" for page in pages)
    assert all(page.executor_evidence == "not_inspected" for page in pages)
    assert all(PRIVATE not in page.model_dump_json() for page in pages)


def test_foreign_task_agent_and_missing_cursor_are_not_empty_pages(tmp_path):
    db, store, agent, task, run = inventory_fixture(tmp_path)
    with store.transaction() as unit:
        own = model_call(unit, agent, run)
        other_task = unit.tasks.create(Task(agent_id=agent.id, title="Other", objective=PRIVATE))
        other_run = unit.runs.create(Run(task_id=other_task.id))
        other = model_call(unit, agent, other_run)
    service = RecoveryInventoryService(db)
    for after in [other.id, uuid4()]:
        with pytest.raises(NotFoundError):
            service.model_calls(agent.id, task.id, after=after)
    for owner, identity in [(uuid4(), task.id), (agent.id, uuid4())]:
        with pytest.raises(NotFoundError):
            service.model_calls(owner, identity, after=own.id)
    assert service.model_calls(agent.id, task.id, after=own.id).items == []


def test_empty_page_and_page_bounds(tmp_path):
    db, _, agent, task, _ = inventory_fixture(tmp_path)
    service = RecoveryInventoryService(db)
    page = service.model_calls(agent.id, task.id)
    assert page.items == [] and not page.has_more and page.next_cursor is None
    for limit in [0, 101, True, 1.5, "50"]:
        with pytest.raises(ValueError):
            service.model_calls(agent.id, task.id, limit=limit)


def test_acknowledgement_keeps_unknown_and_order_and_each_page_is_fresh(tmp_path):
    db, store, agent, task, run = inventory_fixture(tmp_path)
    with store.transaction() as unit:
        first = model_call(unit, agent, run)
        second = model_call(unit, agent, run, 2)
    with db.transaction() as connection:
        connection.execute(
            "UPDATE model_calls SET status='outcome_unknown' WHERE id=?", (str(second.id),)
        )
    service = RecoveryInventoryService(db)
    before = service.model_calls(agent.id, task.id, limit=1)
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
    following = service.model_calls(agent.id, task.id, after=before.next_cursor, limit=1)
    assert before.items[0].id == first.id and following.items[0].id == second.id
    assert (
        following.items[0].status == "outcome_unknown" and following.items[0].unknown_acknowledged
    )
    assert following.items[0].revision == 2 and following.observed_at >= before.observed_at


def test_query_only_projection_never_reads_private_columns_or_mutates(tmp_path, monkeypatch):
    db, store, agent, task, run = inventory_fixture(tmp_path)
    with store.transaction() as unit:
        model_call(unit, agent, run)
    original = db.connect
    with original() as connection:
        before = (
            list(connection.iterdump())
            if hasattr(connection, "iterdump")
            else connection.serialize("main")
        )
    connections = []

    def connect():
        connection = original()

        def authorize(operation, table, column, database, trigger):
            if operation == apsw.SQLITE_READ and column in {
                "request_json",
                "response_json",
                "snapshot_json",
                "provider_config_json",
                "model_config_json",
                "objective",
                "request_hash",
                "lease_generation",
                "error_code",
                "output_message_id",
            }:
                return apsw.SQLITE_DENY
            if operation in {apsw.SQLITE_INSERT, apsw.SQLITE_UPDATE, apsw.SQLITE_DELETE}:
                return apsw.SQLITE_DENY
            return apsw.SQLITE_OK

        connection.set_authorizer(authorize)
        connections.append(connection)
        return connection

    monkeypatch.setattr(db, "connect", connect)
    page = RecoveryInventoryService(db).model_calls(agent.id, task.id)
    assert len(page.items) == 1 and PRIVATE not in page.model_dump_json()
    # Query-only transaction, independently of authorizer refusal of writes.
    assert connections
    with original() as connection:
        assert connection.serialize("main") == before


def test_page_revision_and_records_share_one_snapshot(tmp_path, monkeypatch):
    db, store, agent, task, run = inventory_fixture(tmp_path)
    with store.transaction() as unit:
        call = model_call(unit, agent, run)
    original = db.connect
    changed = False

    def connect():
        connection = original()

        def trace(cursor, statement, bindings):
            nonlocal changed
            if "SELECT x.id,x.run_id" in statement and not changed:
                changed = True
                assert connection.execute("PRAGMA query_only").get == 1
                with pytest.raises(apsw.ReadOnlyError):
                    connection.execute("UPDATE tasks SET revision=revision+1")
                with db.transaction() as writer:
                    writer.execute(
                        "UPDATE tasks SET revision=revision+1 WHERE id=?", (str(task.id),)
                    )
                    writer.execute(
                        "UPDATE model_calls SET status='outcome_unknown' WHERE id=?",
                        (str(call.id),),
                    )
            return True

        connection.set_exec_trace(trace)
        return connection

    monkeypatch.setattr(db, "connect", connect)
    page = RecoveryInventoryService(db).model_calls(agent.id, task.id)
    assert changed and page.task_revision == task.revision and page.items[0].status == "prepared"
    following = RecoveryInventoryService(db).model_calls(agent.id, task.id)
    assert (
        following.task_revision == task.revision + 1
        and following.items[0].status == "outcome_unknown"
    )


@pytest.mark.parametrize(
    "status",
    [
        "prepared",
        "dispatch_started",
        "confirmed",
        "failed_no_effect",
        "outcome_unknown",
        "cancelled",
    ],
)
def test_model_states_are_observed_without_changing_records(tmp_path, status):
    db, store, agent, task, run = inventory_fixture(tmp_path)
    with store.transaction() as unit:
        call = model_call(unit, agent, run)
    with db.transaction() as connection:
        connection.execute(
            "UPDATE model_calls SET status=?,response_json=? WHERE id=?",
            (
                status,
                json.dumps(
                    {"message": {"role": "assistant", "content": PRIVATE}, "finish_reason": "stop"}
                )
                if status == "confirmed"
                else None,
                str(call.id),
            ),
        )
    with store.transaction(write=False) as unit:
        before = unit.model_calls.get(call.id)
    item = RecoveryInventoryService(db).model_calls(agent.id, task.id).items[0]
    assert item.status == status and not item.unknown_acknowledged
    assert item.run_id == run.id and item.revision == before.revision
    with store.transaction(write=False) as unit:
        assert unit.model_calls.get(call.id) == before
