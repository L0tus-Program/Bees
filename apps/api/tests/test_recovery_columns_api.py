"""Incomplete canonical projections return a sanitized conflict to authenticated humans."""

from uuid import uuid4

import pytest
from test_task_actions_api import journal, paused_task
from test_tasks import state as state

CASES = [
    ("", "tasks", "agent_id"),
    ("/actions", "tasks", "revision"),
    ("/model-calls", "tasks", "id"),
    ("/runs", "tasks", "status"),
    ("", "runs", "task_id"),
    ("", "actions", "status"),
    ("", "model_calls", "unknown_acknowledged_at"),
    ("/actions", "actions", "tool_name"),
    ("/actions", "runs", "id"),
    ("/actions", "executor_commands", "error_code"),
    ("/actions", "executors", "kind"),
    ("/actions", "computers", "executor_id"),
    ("/model-calls", "model_calls", "run_id"),
    ("/model-calls", "runs", "task_id"),
    ("/runs", "runs", "finished_at"),
]


@pytest.mark.parametrize(("page", "table", "column"), CASES)
def test_incomplete_recovery_projection_is_sanitized_and_preserved(state, page, table, column):
    client, agent, other, _, _ = state
    path, task = paused_task(client, agent)
    journal(client, task, "outcome_unknown")
    route = path + "/recovery" + page
    before = client.get(route)
    assert before.status_code == 200
    db = client.app.state.database
    with db.transaction() as connection:
        connection.execute(f"ALTER TABLE {table} RENAME TO preserved_records")
        columns = ",".join(
            '"' + row[1] + '"'
            for row in connection.execute("PRAGMA table_info(preserved_records)")
            if row[1] != column
        )
        connection.execute(
            f"CREATE TABLE {table} AS SELECT {columns} FROM preserved_records WHERE 0"
        )
    with db.transaction(write=False) as connection:
        snapshot = connection.serialize("main")
    urls = [route]
    queue_table = table in {"executor_commands", "executors", "computers"}
    if page:
        if not queue_table:
            urls.append(route + "?after=" + str(uuid4()))
        if before.json()["items"]:
            urls.append(route + "?after=" + before.json()["items"][0]["id"])
    for _ in range(2):
        for url in urls:
            response = client.get(url)
            assert response.status_code == 409
            assert response.json() == {
                "error": {
                    "code": "state_conflict",
                    "message": "Registro ausente ou alterado; atualize a tela.",
                }
            }
    for url in [
        route.replace(str(agent.id), str(other.id)),
        route.replace(task["id"], str(uuid4())),
    ]:
        assert client.get(url).status_code == (409 if table == "tasks" else 404)
    if queue_table:
        assert client.get(route + "?after=" + str(uuid4())).status_code == 404
    with db.transaction() as connection:
        assert connection.serialize("main") == snapshot
        connection.execute(f"DROP TABLE {table}")
        connection.execute(f"ALTER TABLE preserved_records RENAME TO {table}")
    restored = client.get(route)
    assert restored.status_code == 200
    assert {k: v for k, v in restored.json().items() if k != "observed_at"} == {
        k: v for k, v in before.json().items() if k != "observed_at"
    }
