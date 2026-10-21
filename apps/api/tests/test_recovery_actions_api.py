"""Human task action pages keep exact scope and never replay or dispatch."""

from uuid import uuid4

from test_task_actions_api import journal, paused_task
from test_tasks import state as state


def test_scoped_action_pages_and_cursor_rejection(state):
    client, first, other, _, _ = state
    path, task = paused_task(client, first)
    entries = [journal(client, task, "outcome_unknown") for _ in range(3)]
    route = path + "/recovery/actions"
    response = client.get(route + "?limit=2")
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    page = response.json()
    assert len(page["items"]) == 2 and page["has_more"]
    assert page["next_cursor"] == str(entries[1].id)
    assert "conteudo-privado" not in response.text
    assert set(page["items"][0]) == {
        "id",
        "run_id",
        "revision",
        "tool_name",
        "status",
        "created_at",
        "updated_at",
        "unknown_acknowledged",
    }
    last = client.get(route + "?after=" + page["next_cursor"]).json()
    assert [item["id"] for item in last["items"]] == [str(entries[2].id)]
    assert not last["has_more"] and last["next_cursor"] is None
    assert (
        client.get(
            route.replace(str(first.id), str(other.id)) + "?after=" + page["next_cursor"]
        ).status_code
        == 404
    )
    different_path, _ = paused_task(client, first)
    assert (
        client.get(different_path + "/recovery/actions?after=" + page["next_cursor"]).status_code
        == 404
    )
    assert client.get(route + "?after=" + str(uuid4())).status_code == 404


def test_bounds_and_authentication_without_mutation(state):
    client, first, _, _, _ = state
    path, _ = paused_task(client, first)
    route = path + "/recovery/actions"
    before = client.get(path).json()
    for query in ["limit=0", "limit=101", "limit=1.5", "after=../private"]:
        assert client.get(route + "?" + query).status_code == 422
    assert client.get(route).json()["items"] == []
    assert client.get(path).json() == before
    assert client.post(route, json={}).status_code in (403, 405)
    client.cookies.clear()
    assert client.get(route).status_code == 401
