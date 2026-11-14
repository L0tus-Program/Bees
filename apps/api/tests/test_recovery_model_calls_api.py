"""Human model-call pages require a session and never dispatch or reveal content."""

from uuid import UUID, uuid4

from test_task_actions_api import paused_task
from test_tasks import state as state

from bees_core.models import ModelCall
from bees_core.storage.store import ModelCalls

PRIVATE = "private-payload-path-owner-token"


def model_call(unit, agent, run, ordinal=1):
    request = {"messages": [{"role": "user", "content": PRIVATE}], "tools": [], "stream": False}
    config = agent.provider_config
    call = ModelCall(
        run_id=run.id,
        ordinal=ordinal,
        provider_config=config,
        request=request,
        snapshot={"private": PRIVATE},
        request_hash="0" * 64,
        task_control_revision=0,
        agent_revision=1,
        lease_generation=1,
    )
    return unit.model_calls.create(
        call.model_copy(
            update={
                "request_hash": ModelCalls.request_digest(
                    call.provider_config, call.request, call.snapshot
                )
            }
        )
    )


def test_scoped_model_pages_and_foreign_cursor_rejection(state):
    client, first, other, _, _ = state
    path, task = paused_task(client, first)
    with client.app.state.store.transaction() as unit:
        run = unit.runs.get(UUID(task["active_run_id"]))
        entries = [model_call(unit, first, run, n) for n in range(1, 4)]
    route = path + "/recovery/model-calls"
    response = client.get(route + "?limit=2")
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    page = response.json()
    assert len(page["items"]) == 2 and page["has_more"]
    assert page["next_cursor"] == str(entries[1].id)
    assert "private-payload-path-owner-token" not in response.text
    assert set(page["items"][0]) == {
        "id",
        "run_id",
        "revision",
        "status",
        "created_at",
        "updated_at",
        "unknown_acknowledged",
    }
    last = client.get(route + "?after=" + page["next_cursor"]).json()
    assert [item["id"] for item in last["items"]] == [str(entries[2].id)]
    assert not last["has_more"] and last["next_cursor"] is None
    assert client.get(route.replace(str(first.id), str(other.id))).status_code == 404
    different_path, _ = paused_task(client, first)
    assert (
        client.get(
            different_path + "/recovery/model-calls?after=" + page["next_cursor"]
        ).status_code
        == 404
    )
    assert client.get(route + "?after=" + str(uuid4())).status_code == 404


def test_bounds_authentication_and_no_mutation(state):
    client, first, _, _, _ = state
    path, _ = paused_task(client, first)
    route = path + "/recovery/model-calls"
    before = client.get(path).json()
    for query in ["limit=0", "limit=101", "limit=1.5", "after=../private"]:
        assert client.get(route + "?" + query).status_code == 422
    assert client.get(route).json()["items"] == []
    assert client.get(path).json() == before
    assert client.post(route, json={}).status_code in (403, 405)
    client.cookies.clear()
    assert client.get(route).status_code == 401
