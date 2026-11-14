"""Human run pages are scoped, authenticated and read only."""

from uuid import UUID, uuid4

from test_task_actions_api import paused_task
from test_tasks import state as state

from bees_core.models import Run


def test_scoped_run_pages_and_foreign_cursor_rejection(state):
    client, first, other, _, _ = state
    path, task = paused_task(client, first)
    with client.app.state.store.transaction() as unit:
        existing = unit.runs.get(UUID(task["active_run_id"]))
        entries = [
            unit.runs.create(Run(task_id=existing.task_id, error="PRIVATE")) for _ in range(2)
        ]
    route = path + "/recovery/runs"
    response = client.get(route + "?limit=2")
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    page = response.json()
    assert len(page["items"]) == 2 and page["has_more"]
    assert page["next_cursor"] == str(entries[0].id) and "PRIVATE" not in response.text
    last = client.get(route + "?after=" + page["next_cursor"]).json()
    assert [item["id"] for item in last["items"]] == [str(entries[1].id)]
    assert not last["has_more"] and last["next_cursor"] is None
    assert client.get(route.replace(str(first.id), str(other.id))).status_code == 404
    different, _ = paused_task(client, first)
    assert client.get(different + "/recovery/runs?after=" + page["next_cursor"]).status_code == 404
    assert client.get(route + "?after=" + str(uuid4())).status_code == 404


def test_bounds_authentication_and_no_mutation(state):
    client, first, _, _, _ = state
    path, _ = paused_task(client, first)
    route = path + "/recovery/runs"
    with client.app.state.database.connect() as connection:
        before = connection.serialize("main")
    for query in ["limit=0", "limit=101", "limit=1.5", "after=../private"]:
        assert client.get(route + "?" + query).status_code == 422
    assert len(client.get(route).json()["items"]) == 1
    with client.app.state.database.connect() as connection:
        assert connection.serialize("main") == before
    assert client.post(route, json={}).status_code in (403, 405)
    client.cookies.clear()
    assert client.get(route).status_code == 401
