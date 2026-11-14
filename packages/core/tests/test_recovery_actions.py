"""Scoped keyset pages never turn cursors into authority or read effect payloads."""

from datetime import timedelta
from uuid import UUID, uuid4

import apsw
import pytest
from test_recovery_inventory import PRIVATE, inventory_fixture

from bees_core.models import Action, Run, Task, TaskCommand, utc_now
from bees_core.recovery import RecoveryInventoryService
from bees_core.storage.store import NotFoundError


def command_fixture(tmp_path):
    from test_executors import pair, queued

    from bees_core.executors import CommandQueue, ExecutorService

    db, store, agent, _, _ = inventory_fixture(tmp_path)
    paired = pair(ExecutorService(db))
    with store.transaction(write=False) as unit:
        generation = unit.safety.state().generation
    command_id = queued(
        store, paired["executor_id"], agent, "computer.shell.run", generation=generation
    )
    queue = CommandQueue(db)
    assert len(queue.claim(paired["executor_id"], "conn-1")) == 1
    with db.transaction(write=False) as connection:
        task_id = connection.execute(
            "SELECT r.task_id FROM actions a JOIN runs r ON r.id=a.run_id WHERE a.id=?",
            (command_id,),
        ).get
    return db, agent.id, task_id, queue, paired["executor_id"], command_id


@pytest.mark.parametrize(
    "code",
    [PRIVATE, "late_" + PRIVATE, "late_confirmed " + PRIVATE, "x" * 100, "", None],
)
def test_executor_free_text_never_becomes_public_even_when_short(tmp_path, code):
    db, agent, task, queue, executor, command = command_fixture(tmp_path)
    assert queue.report(executor, command, "failed", result={"private": PRIVATE}, code=code)
    before = queue.get(command)
    page = RecoveryInventoryService(db).actions(agent, task)
    assert page.items[0].executor_command == "failed"
    assert page.items[0].executor_code is None
    assert PRIVATE not in page.model_dump_json()
    assert queue.get(command) == before


@pytest.mark.parametrize("code", ["worker_restart", "executor_timeout", "element_not_found"])
def test_executor_public_codes_are_preserved(tmp_path, code):
    db, agent, task, queue, executor, command = command_fixture(tmp_path)
    queue.report(executor, command, "unknown", code=code)
    assert RecoveryInventoryService(db).actions(agent, task).items[0].executor_code == code


@pytest.mark.parametrize("status", ["confirmed", "failed", "unknown"])
def test_exact_late_receipts_remain_unknown_without_reading_private_results(
    tmp_path, monkeypatch, status
):
    db, agent, task, queue, executor, command = command_fixture(tmp_path)
    queue.report(executor, command, "unknown", code="executor_timeout")
    assert not queue.report(executor, command, status, result={"private": PRIVATE})
    original = db.connect

    def connect():
        connection = original()

        def authorize(operation, table, column, database, trigger):
            if operation == apsw.SQLITE_READ and column in {"result_json", "arguments_json"}:
                return apsw.SQLITE_DENY
            if operation in {apsw.SQLITE_INSERT, apsw.SQLITE_UPDATE, apsw.SQLITE_DELETE}:
                return apsw.SQLITE_DENY
            return apsw.SQLITE_OK

        connection.set_authorizer(authorize)
        return connection

    monkeypatch.setattr(db, "connect", connect)
    item = RecoveryInventoryService(db).actions(agent, task).items[0]
    assert item.executor_command == "unknown" and item.executor_code == "late_" + status
    assert PRIVATE not in item.model_dump_json()


@pytest.mark.parametrize("status", ["confirmed", "failed"])
@pytest.mark.parametrize("code", ["late_confirmed", "late_failed", "late_unknown"])
def test_late_codes_on_terminal_commands_are_not_presented_as_late_receipts(tmp_path, status, code):
    db, agent, task, queue, executor, command = command_fixture(tmp_path)
    queue.report(executor, command, status, code=code)
    assert RecoveryInventoryService(db).actions(agent, task).items[0].executor_code is None


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


@pytest.mark.parametrize("mismatch", ["agent", "tool", "computer_agent", "computer_executor"])
def test_inconsistent_queue_binding_refuses_page_without_changing_history(tmp_path, mismatch):
    from bees_core.executors import ExecutorService
    from bees_core.models import Agent
    from bees_core.storage.store import IntegrityError, StateStore

    db, agent, task, queue, executor, command = command_fixture(tmp_path)
    agent = str(agent)
    with StateStore(db).transaction() as unit:
        other = unit.agents.create(
            Agent(name="Other", provider_config=unit.agents.get(agent).provider_config)
        )
    other_executor = ExecutorService(db).exchange(
        {
            "token": ExecutorService(db).invite({"kind": "computer_runtime", "name": "Other"})[
                "token"
            ]
        }
    )["executor_id"]
    with db.transaction() as connection:
        if mismatch == "agent":
            connection.execute(
                "UPDATE executor_commands SET agent_id=? WHERE id=?", (str(other.id), command)
            )
        elif mismatch == "tool":
            connection.execute(
                "UPDATE executor_commands SET tool_name=? WHERE id=?", (PRIVATE, command)
            )
        else:
            computer = str(uuid4())
            stamp = utc_now().isoformat()
            connection.execute(
                "INSERT INTO computers(id,created_at,updated_at,agent_id,executor_id,name,status) "
                "VALUES(?,?,?,?,?,?,'removed')",
                (
                    computer,
                    stamp,
                    stamp,
                    str(other.id) if mismatch == "computer_agent" else agent,
                    other_executor if mismatch == "computer_executor" else executor,
                    PRIVATE,
                ),
            )
            connection.execute(
                "UPDATE executor_commands SET computer_id=? WHERE id=?", (computer, command)
            )
    before = queue.get(command)
    with pytest.raises(IntegrityError, match="Invalid recovery command binding"):
        RecoveryInventoryService(db).actions(agent, task)
    assert queue.get(command) == before


def test_valid_historical_computer_binding_is_read_only_and_survives_revocation(
    tmp_path, monkeypatch
):
    db, agent, task, queue, executor, command = command_fixture(tmp_path)
    agent = str(agent)
    stamp, computer = utc_now().isoformat(), str(uuid4())
    with db.transaction() as connection:
        connection.execute(
            "INSERT INTO computers(id,created_at,updated_at,agent_id,executor_id,name,status) "
            "VALUES(?,?,?,?,?,?,'removed')",
            (computer, stamp, stamp, agent, executor, PRIVATE),
        )
        connection.execute(
            "UPDATE executor_commands SET computer_id=? WHERE id=?", (computer, command)
        )
        connection.execute("UPDATE executors SET status='revoked' WHERE id=?", (executor,))
    queue.report(executor, command, "unknown", code="executor_timeout")
    original = db.connect

    def connect():
        connection = original()

        def authorize(operation, table, column, database, trigger):
            if (
                operation == apsw.SQLITE_READ
                and column
                in {
                    "result_json",
                    "arguments_json",
                    "metadata_json",
                    "execution_binding_json",
                    "credential_hash",
                }
                or (
                    operation == apsw.SQLITE_READ
                    and table in {"computers", "executors"}
                    and column == "name"
                )
            ):
                return apsw.SQLITE_DENY
            if operation in {apsw.SQLITE_INSERT, apsw.SQLITE_UPDATE, apsw.SQLITE_DELETE}:
                return apsw.SQLITE_DENY
            return apsw.SQLITE_OK

        connection.set_authorizer(authorize)
        return connection

    monkeypatch.setattr(db, "connect", connect)
    page = RecoveryInventoryService(db).actions(agent, task)
    assert page.items[0].executor_command == "unknown"
    assert page.items[0].executor_code == "executor_timeout"
    assert PRIVATE not in page.model_dump_json()


def test_inconsistent_later_record_refuses_its_page_and_preserves_cursor(tmp_path):
    from bees_core.storage.store import IntegrityError, StateStore

    db, agent, task, queue, executor, command = command_fixture(tmp_path)
    service = RecoveryInventoryService(db)
    original = service.actions(agent, task).items[0]
    # Put an ordinary action before the inconsistent command, in the same run.
    with StateStore(db).transaction() as unit:
        earlier = unit.actions.create(
            Action(
                run_id=original.run_id,
                tool_name="fixture",
                created_at=original.created_at - timedelta(seconds=1),
            )
        )
    with db.transaction() as connection:
        connection.execute(
            "UPDATE executor_commands SET tool_name=? WHERE id=?", (PRIVATE, command)
        )
    before = queue.get(command)
    page = service.actions(agent, task, limit=1)
    assert page.items[0].id == earlier.id and page.items[0].executor_command is None
    assert page.has_more and page.next_cursor == earlier.id
    for _ in range(2):
        with pytest.raises(IntegrityError):
            service.actions(agent, task, limit=1, after=page.next_cursor)
    assert queue.get(command) == before
