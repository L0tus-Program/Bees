"""Invalid stored states return a sanitized conflict rather than partial totals."""

from uuid import UUID, uuid4

import pytest
from test_recovery_model_calls_api import PRIVATE, model_call
from test_task_actions_api import journal, paused_task
from test_tasks import state as state


@pytest.mark.parametrize(
    ("table", "status"),
    [
        ("actions", PRIVATE),
        ("actions", None),
        ("model_calls", PRIVATE),
        ("model_calls", None),
        ("model_calls", "awaiting_approval"),
        ("model_calls", "ready"),
    ],
)
def test_invalid_inventory_status_is_sanitized_and_preserved(state, table, status):
    client, agent, other, _, _ = state
    path, task = paused_task(client, agent)
    journal(client, task, "confirmed")
    with client.app.state.store.transaction() as unit:
        run = unit.runs.get(UUID(task["active_run_id"]))
        model_call(unit, agent, run)
    route = path + "/recovery"
    before = client.get(route)
    assert before.status_code == 200
    db = client.app.state.database
    with db.transaction() as connection:
        connection.execute(f"ALTER TABLE {table} RENAME TO preserved_records")
        connection.execute(f"CREATE TABLE {table} AS SELECT * FROM preserved_records")
        connection.execute(f"UPDATE {table} SET status=?", (status,))
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
