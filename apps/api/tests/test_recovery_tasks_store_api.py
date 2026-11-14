"""Task store loss cannot turn diagnosis into a successful or empty response."""

from uuid import uuid4

import pytest
from test_task_actions_api import journal, paused_task
from test_tasks import state as state


@pytest.mark.parametrize("page", ["", "/actions", "/model-calls", "/runs"])
@pytest.mark.parametrize("substitute", ["missing", "view", "empty_view", "foreign_view"])
def test_recovery_refuses_noncanonical_task_store(state, page, substitute):
    client, agent, other, _, _ = state
    path, task = paused_task(client, agent)
    journal(client, task, "outcome_unknown")
    route = path + "/recovery" + page
    before = client.get(route)
    assert before.status_code == 200
    db = client.app.state.database
    with db.transaction() as connection:
        connection.execute("ALTER TABLE tasks RENAME TO preserved_tasks")
        if substitute in {"view", "empty_view"}:
            condition = " WHERE 0" if substitute == "empty_view" else ""
            connection.execute("CREATE VIEW tasks AS SELECT * FROM preserved_tasks" + condition)
        elif substitute == "foreign_view":
            connection.execute(
                "CREATE VIEW tasks AS SELECT id,'"
                + str(other.id)
                + "' AS agent_id,revision,status FROM preserved_tasks"
            )
    with db.transaction(write=False) as connection:
        snapshot = connection.serialize("main")
    urls = [
        route,
        route.replace(str(agent.id), str(other.id)),
        route.replace(task["id"], str(uuid4())),
    ]
    if page:
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
    with db.transaction() as connection:
        assert connection.serialize("main") == snapshot
        if substitute != "missing":
            connection.execute("DROP VIEW tasks")
        connection.execute("ALTER TABLE preserved_tasks RENAME TO tasks")
    restored = client.get(route)
    assert restored.status_code == 200
    assert {k: v for k, v in restored.json().items() if k != "observed_at"} == {
        k: v for k, v in before.json().items() if k != "observed_at"
    }
    assert client.get(route.replace(str(agent.id), str(other.id))).status_code == 404
    assert client.get(route.replace(task["id"], str(uuid4()))).status_code == 404
