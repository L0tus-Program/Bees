"""Stored projection errors must not expose validation input through the API."""

from uuid import UUID, uuid4

import pytest
from test_recovery_model_calls_api import model_call
from test_task_actions_api import journal, paused_task
from test_tasks import state as state

from bees_core.executors import ExecutorService

PRIVATE = "private-projection-path-token"
CASES = (
    [
        (route, table, column, value)
        for route, table in (
            ("actions", "actions"),
            ("model-calls", "model_calls"),
            ("runs", "runs"),
        )
        for column, value in (("status", PRIVATE), ("revision", 0), ("updated_at", PRIVATE))
    ]
    + [
        (route, table, "id", value)
        for route, table in (
            ("actions", "actions"),
            ("model-calls", "model_calls"),
            ("runs", "runs"),
        )
        for value in (
            "ABCDEF01-2345-4678-9ABC-DEF012345678",
            "abcdef01234546789abcdef012345678",
            "{abcdef01-2345-4678-9abc-def012345678}",
            "urn:uuid:abcdef01-2345-4678-9abc-def012345678",
        )
    ]
    + [
        (route, table, "unknown_acknowledged_at", value)
        for route, table in (
            ("", "actions"),
            ("", "model_calls"),
            ("actions", "actions"),
            ("model-calls", "model_calls"),
        )
        for value in (PRIVATE, "2026-01-01T00:00:00")
    ]
    + [(route, "tasks", "revision", 0) for route in ("", "actions", "model-calls", "runs")]
    + [
        ("", "tasks", "status", PRIVATE),
        ("actions", "executor_commands", "status", PRIVATE),
        ("actions", "executor_commands", "status", None),
    ]
)


@pytest.mark.parametrize(("suffix", "table", "column", "value"), CASES)
def test_projection_conflict_is_sanitized_preserved_and_restorable(
    state, suffix, table, column, value
):
    client, agent, other, _, _ = state
    path, task = paused_task(client, agent)
    action = journal(client, task, "confirmed")
    with client.app.state.store.transaction() as unit:
        run = unit.runs.get(UUID(task["active_run_id"]))
        model_call(unit, agent, run)
    route = path + "/recovery" + ("/" + suffix if suffix else "")
    before = client.get(route)
    assert before.status_code == 200
    db = client.app.state.database
    if table == "executor_commands":
        service = ExecutorService(db)
        invite = service.invite({"kind": "computer_runtime", "name": "Fixture"})
        executor = service.exchange({"token": invite["token"]})["executor_id"]
        stamp = action.created_at.isoformat()
        with db.transaction() as connection:
            connection.execute(
                "INSERT INTO executor_commands(id,created_at,updated_at,executor_id,agent_id,"
                "tool_name,arguments_json,deadline_at,status) VALUES(?,?,?,?,?,?,?,?,'sent')",
                (
                    str(action.id),
                    stamp,
                    stamp,
                    executor,
                    str(agent.id),
                    action.tool_name,
                    "{}",
                    stamp,
                ),
            )
        before = client.get(route)
        assert before.status_code == 200
    with db.transaction() as connection:
        connection.execute(f"ALTER TABLE {table} RENAME TO preserved_records")
        connection.execute(f"CREATE TABLE {table} AS SELECT * FROM preserved_records")
        connection.execute(f'UPDATE {table} SET "{column}"=?', (value,))
        if column == "unknown_acknowledged_at":
            connection.execute(f"UPDATE {table} SET status='outcome_unknown'")
    with db.transaction(write=False) as connection:
        snapshot = connection.serialize("main")
    for _ in range(2):
        response = client.get(route)
        assert response.status_code == 409
        assert response.json() == {
            "error": {
                "code": "state_conflict",
                "message": "Registro ausente ou alterado; atualize a tela.",
            }
        }
        assert PRIVATE not in response.text
    for foreign in [
        route.replace(str(agent.id), str(other.id)),
        route.replace(task["id"], str(uuid4())),
    ]:
        assert client.get(foreign).status_code == 404
    with db.transaction() as connection:
        assert connection.serialize("main") == snapshot
        connection.execute(f"DROP TABLE {table}")
        connection.execute(f"ALTER TABLE preserved_records RENAME TO {table}")
    restored = client.get(route)
    assert restored.status_code == 200
    assert {k: v for k, v in restored.json().items() if k != "observed_at"} == {
        k: v for k, v in before.json().items() if k != "observed_at"
    }
    client.cookies.clear()
    assert client.get(route).status_code == 401
