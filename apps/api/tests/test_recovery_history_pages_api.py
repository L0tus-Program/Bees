"""Missing history and substitute views are sanitized conflicts on every page."""

from uuid import UUID, uuid4

import pytest
from test_recovery_model_calls_api import model_call
from test_task_actions_api import journal, paused_task
from test_tasks import state as state


@pytest.mark.parametrize(
    ("page", "table"),
    [
        ("actions", "actions"),
        ("actions", "runs"),
        ("model-calls", "model_calls"),
        ("model-calls", "runs"),
        ("runs", "runs"),
    ],
)
@pytest.mark.parametrize("substitute", ["missing", "view", "empty_view"])
def test_pages_refuse_missing_or_substituted_history_with_sanitized_conflict(
    state, page, table, substitute
):
    client, agent, other, _, _ = state
    path, task = paused_task(client, agent)
    journal(client, task, "outcome_unknown")
    with client.app.state.store.transaction() as unit:
        run = unit.runs.get(UUID(task["active_run_id"]))
        model_call(unit, agent, run)
    route = path + "/recovery/" + page
    before = client.get(route).json()
    cursor = before["items"][0]["id"]
    db = client.app.state.database
    with db.transaction() as connection:
        connection.execute(f"ALTER TABLE {table} RENAME TO preserved_records")
        if substitute != "missing":
            condition = " WHERE 0" if substitute == "empty_view" else ""
            connection.execute(f"CREATE VIEW {table} AS SELECT * FROM preserved_records{condition}")
    with db.transaction(write=False) as connection:
        snapshot = connection.serialize("main")
    for _ in range(2):
        for query in ["", "?after=" + cursor, "?after=" + str(uuid4())]:
            response = client.get(route + query)
            assert response.status_code == 409
            assert response.json() == {
                "error": {
                    "code": "state_conflict",
                    "message": "Registro ausente ou alterado; atualize a tela.",
                }
            }
            assert "preserved_records" not in response.text and table not in response.text
    for url in [
        route.replace(str(agent.id), str(other.id)),
        route.replace(task["id"], str(uuid4())),
    ]:
        assert client.get(url).status_code == 404
    with db.transaction() as connection:
        assert connection.serialize("main") == snapshot
        if substitute != "missing":
            connection.execute(f"DROP VIEW {table}")
        connection.execute(f"ALTER TABLE preserved_records RENAME TO {table}")
    restored = client.get(route)
    assert restored.status_code == 200
    assert {k: v for k, v in restored.json().items() if k != "observed_at"} == {
        k: v for k, v in before.items() if k != "observed_at"
    }
    assert client.get(route + "?after=" + cursor).json()["items"] == []
    assert client.get(route + "?after=" + str(uuid4())).status_code == 404
