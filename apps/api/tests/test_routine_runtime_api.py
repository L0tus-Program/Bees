"""Sondagem humana de capacidade não materializa ocorrências nem concede recursos."""

from datetime import UTC, datetime
from uuid import uuid4

from fastapi.testclient import TestClient
from test_global_stop_api import ORIGIN
from test_tool_tasks_api import runtime as runtime
from test_tool_tasks_api import state as state

from bees_api.worker_status import HEARTBEAT_KEY


def test_runtime_is_read_only_owned_and_requires_new_worker_version(runtime):
    client, agent, _, _ = runtime
    url = f"/api/v1/agents/{agent.id}/routines/runtime"
    assert TestClient(client.app, base_url=ORIGIN).get(url).status_code == 401
    assert client.get(url).json()["reason"] == "worker_unavailable"
    with client.app.state.store.transaction(write=False) as unit:
        before = [
            unit.agents._context.connection.execute(f"SELECT count(*) FROM {table}").get
            for table in ("tasks", "routines", "routine_occurrences", "tool_grants", "messages")
        ]
    for version in (True, "1", 0, 1):
        client.app.state.store.put_cache(
            HEARTBEAT_KEY,
            {
                "last_seen": datetime.now(UTC).isoformat(),
                "tool_cycle_version": 1,
                "routine_scheduler_version": version,
            },
            ttl_seconds=20,
        )
        response = client.get(url)
        assert response.status_code == 200 and response.json()["available"] == (
            type(version) is int and version == 1
        )
    with client.app.state.store.transaction(write=False) as unit:
        after = [
            unit.agents._context.connection.execute(f"SELECT count(*) FROM {table}").get
            for table in ("tasks", "routines", "routine_occurrences", "tool_grants", "messages")
        ]
    assert before == after
    assert client.get(url.replace(str(agent.id), str(uuid4()))).status_code == 404
    assert client.post(url, json={}).status_code in (403, 405)
