"""Projeções com SQLite real, concorrência e HTTP loopback próprio; sem modelos pagos."""

import asyncio
import json
from datetime import timedelta
from uuid import uuid4

import httpx
import pytest
from test_budgets import Setup, answer
from test_execution_approvals import approve, waiting
from test_task_control_consumption import response
from test_task_control_consumption import scenario as http_scenario
from test_tasks_execution import fixture_state, task_input

import bees_core.activity as activity
from bees_core.activity import ActivityService
from bees_core.budgets import BudgetService, LimitInput, settle
from bees_core.execution import TaskWorker
from bees_core.models import Agent, Run, UsageEntry, utc_now
from bees_core.providers.contracts import Usage
from bees_core.storage.database import Database
from bees_core.storage.store import NotFoundError, RevisionConflict


def rows(database):
    with database.transaction(write=False) as connection:
        return {
            table: connection.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall()
            for table in (
                "domain_events",
                "tasks",
                "runs",
                "model_calls",
                "actions",
                "usage_entries",
                "budget_limits",
                "safety_control",
            )
        }


scenario = http_scenario


@pytest.mark.parametrize("control", ["redirect", "cancel"])
def test_cancel_keeps_confirmed_or_unknown_consumption_and_read_is_pure(scenario, control):
    task = scenario.task()
    observed = []

    def respond(request):
        page = ActivityService(Database(scenario.database.path)).list(scenario.agent.id)
        assert page.operations.model_calls_in_flight == 1
        assert page.usage.open_reservations == 1
        assert page.usage.reserved_tokens > 0
        scenario.command(
            task, control, **({"instruction": "Novo recorte"} if control == "redirect" else {})
        )
        observed.append(page)
        return response("Resultado privado que não pode aparecer na timeline")

    scenario.respond = respond
    assert scenario.run_once()
    if control == "redirect":
        scenario.command(task, "cancel")
    before = rows(scenario.database)
    page = ActivityService(Database(scenario.database.path)).list(scenario.agent.id)
    assert rows(scenario.database) == before
    assert scenario.detail(task).task.status == "cancelled"
    assert page.operations.model_calls_in_flight == 0
    if control == "redirect":
        assert page.usage.counted_tokens == page.usage.reported_tokens == 15
    else:
        assert page.usage.counted_tokens == page.usage.unknown_tokens > 15
        assert page.operations.unknown_model_calls == 1
    assert page.usage.entries == 1
    facts = [item for item in page.items if item.entity_type == "usage_entry"]
    assert len(facts) == 2
    historical = next(item for item in facts if item.status == "reserved")
    final = next(item for item in facts if item.status != "reserved")
    assert historical.usage.usage_kind is None
    assert historical.usage.input_tokens is None and historical.usage.total_tokens is None
    assert historical.usage.counted_tokens == historical.usage.reserved_tokens > 15
    if control == "redirect":
        assert final.usage.total_tokens == final.usage.counted_tokens == 15
    else:
        assert final.usage.total_tokens is None
        assert final.usage.counted_tokens == historical.usage.reserved_tokens
    assert all(item.environment.kind == "textual" for item in page.items)
    serialized = page.model_dump_json()
    for private in (
        "Explique filas duráveis",
        "Resultado privado",
        "lease_owner",
        "execution_binding",
        "request_hash",
        "provider_config",
        "checkpoint",
        "authorization",
        "metadata",
    ):
        assert private not in serialized
    assert len(scenario.requests) == 1 and len(observed) == 1


def test_certainty_buckets_are_disjoint_and_window_uses_reservation_date(tmp_path):
    state = Setup(tmp_path)
    now = utc_now()
    specs = [
        ("reported", 8, 2, None),
        ("estimated", None, None, 12),
        ("reported", 3, None, None),
        ("reported", 0, 0, 0),
        ("unknown", None, None, None),
        (None, None, None, None),
        ("released", None, None, None),
    ]
    with state.store.transaction() as unit:
        for index, (kind, inp, out, total) in enumerate(specs):
            created = now - timedelta(minutes=1)
            entry = unit.usage_entries.create(
                UsageEntry(
                    agent_id=state.agent.id,
                    source="chat",
                    conversation_id=state.chat.id,
                    provider_kind="openai_compatible",
                    model="Private model",
                    reserved_tokens=100,
                    estimate_method="private_arbitrary_method" if index == 0 else "chars_div_3_v1",
                    created_at=created,
                    updated_at=created,
                    metadata={"secret": "PRIVATE"},
                )
            )
            if kind == "released":
                settle(unit, entry.id, released=True, now=now)
            elif kind is not None:
                # Partial and zero must remain uncertain even when marked reported.
                unit.usage_entries.update(
                    entry.model_copy(
                        update={
                            "status": "confirmed",
                            "usage_kind": kind,
                            "input_tokens": inp,
                            "output_tokens": out,
                            "total_tokens": total,
                            "settled_at": now,
                        }
                    ),
                    entry.revision,
                )
        old = unit.usage_entries.create(
            UsageEntry(
                agent_id=state.agent.id,
                source="chat",
                conversation_id=state.chat.id,
                provider_kind="openai_compatible",
                model="old",
                reserved_tokens=999,
                estimate_method="chars_div_3_v1",
                created_at=now - timedelta(days=2),
                updated_at=now - timedelta(days=2),
            )
        )
        settle(unit, old.id, usage=Usage(kind="reported", total_tokens=999), now=now)
        unit.usage_entries.create(
            UsageEntry(
                agent_id=state.agent.id,
                source="chat",
                conversation_id=state.chat.id,
                provider_kind="openai_compatible",
                model="future",
                reserved_tokens=777,
                estimate_method="chars_div_3_v1",
                created_at=now + timedelta(days=1),
                updated_at=now + timedelta(days=1),
            )
        )
    page = ActivityService(state.database).list(state.agent.id, now=now)
    usage = page.usage
    assert usage.model_dump() == {
        "counted_tokens": 422,
        "reported_tokens": 10,
        "estimated_tokens": 12,
        "reserved_tokens": 100,
        "unknown_tokens": 300,
        "released_entries": 1,
        "entries": 7,
        "open_reservations": 1,
        "unknown_entries": 3,
        "estimate_methods": ["chars_div_3_v1", "unknown"],
    }
    assert usage.counted_tokens == sum(
        (usage.reported_tokens, usage.estimated_tokens, usage.reserved_tokens, usage.unknown_tokens)
    )
    budget = BudgetService(state.database).summary(state.agent.id, now=now, include_snapshot=True)
    assert budget["counted_tokens"] == 1199 and budget["entries"] == 8
    assert budget["open_reservations"] == 2
    assert budget["snapshot"] == page.snapshot.model_dump(mode="json")
    assert page.operations.chat_reservations == 2  # Future clock rows remain operational facts.
    assert (
        "PRIVATE" not in page.model_dump_json()
        and "private_arbitrary" not in page.model_dump_json()
    )


def test_cursor_is_exclusive_requires_fixed_interval_and_refuses_new_snapshot(tmp_path):
    database, _, agent, _, tasks = fixture_state(tmp_path)
    tasks.create(agent.id, task_input())
    service = ActivityService(database)
    first = service.list(agent.id, limit=1)
    seen = list(first.items)
    page = first
    while page.next_before is not None:
        page = service.list(
            agent.id,
            before=page.next_before,
            snapshot_seq=first.snapshot.event_seq,
            since=first.interval.since,
            until=first.interval.until,
            limit=1,
        )
        seen.extend(page.items)
    assert len({item.seq for item in seen}) == len(seen)
    assert [item.seq for item in seen] == sorted((item.seq for item in seen), reverse=True)
    assert {item.seq for item in seen} == {
        item.seq
        for item in service.list(
            agent.id, since=first.interval.since, until=first.interval.until
        ).items
    }
    with pytest.raises(ValueError):
        service.list(agent.id, before=first.next_before, snapshot_seq=first.snapshot.event_seq)
    BudgetService(database).set_limit(
        agent.id, LimitInput(token_limit=6000), expected_revision=None
    )
    with pytest.raises(RevisionConflict):
        service.list(
            agent.id,
            before=first.next_before,
            snapshot_seq=first.snapshot.event_seq,
            since=first.interval.since,
            until=first.interval.until,
        )


@pytest.mark.parametrize(
    "arguments",
    [
        {"limit": True},
        {"before": 2**100, "snapshot_seq": 0},
        {"snapshot_seq": 2**100},
        {"run_id": uuid4()},
        {"window_seconds": 100},
        {"window_seconds": 86400, "until": utc_now()},
        {"since": utc_now().replace(tzinfo=None)},
        {"until": utc_now() + timedelta(days=1)},
        {"since": utc_now() - timedelta(days=32)},
    ],
)
def test_invalid_scope_cursor_and_time_are_refused(tmp_path, arguments):
    database, _, agent, _, _ = fixture_state(tmp_path)
    with pytest.raises(ValueError):
        ActivityService(database).list(agent.id, **arguments)


def test_binding_precedes_snapshot_and_scoped_chat_is_never_assigned_to_task(tmp_path):
    database, store, agent, _, tasks = fixture_state(tmp_path)
    task = tasks.create(agent.id, task_input())
    with store.transaction() as unit:
        other = unit.agents.create(Agent(name="Outra", provider_config=agent.provider_config))
        run = unit.runs.latest(task.id)
    service = ActivityService(database)
    with pytest.raises(NotFoundError):
        service.list(other.id, task_id=task.id, snapshot_seq=0)
    with pytest.raises(NotFoundError):
        service.list(agent.id, task_id=task.id, run_id=uuid4(), snapshot_seq=0)
    with pytest.raises(NotFoundError):
        service.list(uuid4(), snapshot_seq=0)
    page = service.list(agent.id, task_id=task.id, run_id=run.id)
    assert all(item.run_id == run.id for item in page.items)
    assert page.scope.task_id == task.id and page.operations.chat_reservations == 0


def test_first_read_pins_snapshot_despite_concurrent_commit(tmp_path, monkeypatch):
    state = Setup(tmp_path)
    original = activity.observe
    committed = []

    def pin_then_write(connection, **kwargs):
        snapshot = original(connection, **kwargs)
        if not committed:
            with state.store.transaction() as unit:
                committed.append(
                    unit.usage_entries.create(
                        UsageEntry(
                            agent_id=state.agent.id,
                            source="chat",
                            conversation_id=state.chat.id,
                            provider_kind="openai_compatible",
                            model="fixture",
                            reserved_tokens=321,
                            estimate_method="chars_div_3_v1",
                        )
                    )
                )
        return snapshot

    monkeypatch.setattr(activity, "observe", pin_then_write)
    initial = ActivityService(state.database).list(state.agent.id)
    assert initial.usage.entries == 0 and initial.operations.chat_reservations == 0
    assert not initial.items
    current = ActivityService(state.database).list(state.agent.id)
    assert current.snapshot.event_seq > initial.snapshot.event_seq
    assert current.usage.counted_tokens == 321 and current.operations.chat_reservations == 1


def test_old_unknown_survives_more_than_one_thousand_runs_and_is_not_limited_by_interval(tmp_path):
    database, store, agent, _, tasks = fixture_state(tmp_path)
    task = tasks.create(agent.id, task_input())
    assert asyncio.run(
        TaskWorker(
            database,
            transport=httpx.MockTransport(
                lambda request: httpx.Response(503, json={"secret": "provider detail"})
            ),
        ).run_once()
    )
    detail = tasks.detail(agent.id, task.id)
    unknown = detail.calls[-1]
    assert unknown.status == "outcome_unknown"
    with store.transaction() as unit:
        for index in range(1001):
            stamp = utc_now() + timedelta(microseconds=index)
            unit.runs.create(
                Run(
                    task_id=task.id,
                    provider_config=agent.provider_config,
                    created_at=stamp,
                    updated_at=stamp,
                )
            )
    since = utc_now()
    page = ActivityService(Database(database.path)).list(agent.id, task_id=task.id, since=since)
    assert page.usage.entries == 0 and page.operations.unknown_model_calls == 1
    assert page.operations.unknown_tool_actions == 0
    scoped = ActivityService(database).list(agent.id, task_id=task.id, run_id=unknown.run_id)
    assert scoped.operations.unknown_model_calls == 1 and scoped.usage.entries == 1


def test_approval_action_and_model_call_are_not_double_counted(tmp_path):
    database, _, agent, tasks, _, _, task, approvals, pending = waiting(tmp_path)
    service = ActivityService(database)
    page = service.list(agent.id, task_id=task.id)
    assert page.operations.waiting_approval == 1
    assert any(item.entity_type == "approval" and item.status == "pending" for item in page.items)
    approve(approvals, agent, pending)
    observed = []

    def handle(request):
        page = service.list(agent.id, task_id=task.id)
        observed.append(page.operations)
        return httpx.Response(503, json={"error": "ambiguous"})

    assert asyncio.run(TaskWorker(database, transport=httpx.MockTransport(handle)).run_once())
    assert observed[0].model_calls_in_flight == 1 and observed[0].tool_actions_in_flight == 0
    assert service.list(agent.id).operations.unknown_model_calls == 1
    assert service.list(agent.id).operations.unknown_tool_actions == 0
    old_run = tasks.detail(agent.id, task.id).runs[-1]
    with service.store.transaction() as unit:
        unit.runs.create(Run(task_id=task.id, provider_config=agent.provider_config))
        current = unit.tasks.get(task.id)
        unit.tasks.update(
            current.model_copy(update={"status": "waiting_approval"}), current.revision
        )
    assert service.list(agent.id, task_id=task.id).operations.waiting_approval == 1
    assert (
        service.list(agent.id, task_id=task.id, run_id=old_run.id).operations.waiting_approval == 0
    )


def test_previous_run_revision_does_not_receive_new_failure(tmp_path):
    database, store, agent, _, tasks = fixture_state(tmp_path)
    task = tasks.create(agent.id, task_input())
    with store.transaction() as unit:
        run = unit.runs.latest(task.id)
        paused = unit.runs.update(
            run.model_copy(update={"status": "paused", "error": "timeout"}), run.revision
        )
        unit.runs.update(paused.model_copy(update={"error": "connection_failed"}), paused.revision)
    page = ActivityService(database).list(agent.id)
    facts = [item for item in page.items if item.entity_id == run.id]
    latest = max(facts, key=lambda item: item.seq)
    assert latest.error_code == "connection_failed"
    assert all(item.error_code is None for item in facts if item.id != latest.id)


@pytest.mark.parametrize(
    "payload",
    [
        {"status": "SECRET", "revision": 9},
        {"status": ["SECRET"], "revision": 1},
        {"status": "queued", "revision": 999},
    ],
)
def test_cross_agent_usage_is_excluded_and_invalid_event_is_not_echoed(tmp_path, payload):
    database, store, agent, _, tasks = fixture_state(tmp_path)
    task = tasks.create(agent.id, task_input())
    assert asyncio.run(
        TaskWorker(database, transport=httpx.MockTransport(lambda request: answer())).run_once()
    )
    detail = tasks.detail(agent.id, task.id)
    with store.transaction() as unit:
        other = unit.agents.create(Agent(name="Outra", provider_config=agent.provider_config))
        original = unit.model_calls.get(detail.calls[-1].id)
        call = unit.model_calls.create(
            original.model_copy(
                update={
                    "id": uuid4(),
                    "revision": 1,
                    "ordinal": 2,
                    "status": "prepared",
                    "response": None,
                    "output_message_id": None,
                    "started_at": None,
                    "finished_at": None,
                }
            )
        )
        unit.usage_entries.create(
            UsageEntry(
                agent_id=other.id,
                source="task",
                model_call_id=call.id,
                provider_kind="openai_compatible",
                model="foreign",
                reserved_tokens=99,
                estimate_method="chars_div_3_v1",
            )
        )
    foreign = ActivityService(database).list(other.id)
    assert foreign.usage.entries == 0 and not foreign.items
    # Canonical events are append-only; inject malformed payload through its owning writer,
    # never mutate an existing event or remove a trigger.
    with database.transaction() as connection:
        connection.execute(
            "INSERT INTO domain_events (id,created_at,entity_type,entity_id,event_type,"
            "payload_json,actor,source) VALUES (?,?,?,?,?,?,?,?)",
            (
                str(uuid4()),
                utc_now().isoformat(),
                "task",
                str(task.id),
                "updated",
                json.dumps(payload),
                "test",
                "test",
            ),
        )
    with pytest.raises(RevisionConflict, match="Evento operacional inválido"):
        ActivityService(database).list(agent.id)
