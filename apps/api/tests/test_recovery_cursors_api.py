"""Cursor corruption is a sanitized conflict, including after the last record."""

from uuid import UUID, uuid4

import pytest
from test_recovery_model_calls_api import model_call
from test_task_actions_api import journal, paused_task
from test_tasks import state as state

PRIVATE = "private-cursor-path-token"


@pytest.mark.parametrize(
    "suffix,table", [("actions", "actions"), ("model-calls", "model_calls"), ("runs", "runs")]
)
@pytest.mark.parametrize("value", [None, PRIVATE, "2026-01-01T00:00:00"])
def test_cursor_conflict_is_sanitized_and_restorable(state, suffix, table, value):
    client, agent, other, _, _ = state
    path, task = paused_task(client, agent)
    action = journal(client, task, "confirmed")
    with client.app.state.store.transaction() as unit:
        run = unit.runs.get(UUID(task["active_run_id"]))
        call = model_call(unit, agent, run)
    identity = {"actions": action.id, "model_calls": call.id, "runs": run.id}[table]
    route = path + "/recovery/" + suffix
    params = {"after": str(identity)}
    before = client.get(route, params=params)
    assert before.status_code == 200
    assert before.json()["items"] == []
    db = client.app.state.database
    with db.transaction() as connection:
        connection.execute(f"ALTER TABLE {table} RENAME TO preserved_records")
        connection.execute(f"CREATE TABLE {table} AS SELECT * FROM preserved_records")
        connection.execute(f"UPDATE {table} SET created_at=? WHERE id=?", (value, str(identity)))
    with db.transaction(write=False) as connection:
        snapshot = connection.serialize("main")
    for _ in range(2):
        response = client.get(route, params=params)
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
        assert client.get(foreign, params=params).status_code == 404
    assert client.get(route, params={"after": str(uuid4())}).status_code == 404
    with db.transaction() as connection:
        assert connection.serialize("main") == snapshot
        connection.execute(f"DROP TABLE {table}")
        connection.execute(f"ALTER TABLE preserved_records RENAME TO {table}")
    restored = client.get(route, params=params)
    assert restored.status_code == 200
    assert {k: v for k, v in restored.json().items() if k != "observed_at"} == {
        k: v for k, v in before.json().items() if k != "observed_at"
    }
    client.cookies.clear()
    assert client.get(route, params=params).status_code == 401
