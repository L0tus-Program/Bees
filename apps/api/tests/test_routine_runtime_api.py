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


def agent_runtime(state, *, agent_version=1):
    from bees_core.tools import ToolService

    client, agent, _ = state
    with client.app.state.store.transaction() as unit:
        current = unit.agents.get(agent.id)
        config = current.provider_config | {"capabilities": {"text": True, "tool_calls": True}}
        unit.agents.update(current.model_copy(update={"provider_config": config}), current.revision)
    tools = ToolService(client.app.state.database)
    manifest = next(item for item in tools.catalog() if item["plugin_key"] == "bees.workspace")
    plugin = tools.install({"client_request_id": uuid4(), "manifest": manifest})
    tools.update_plugin(plugin.id, {"expected_revision": 1, "enabled": True})
    tools.set_grant(
        agent.id, plugin.id, "deliverable.publish", {"expected_revision": 0, "enabled": True}
    )
    client.app.state.store.put_cache(
        HEARTBEAT_KEY,
        {
            "last_seen": datetime.now(UTC).isoformat(),
            "tool_cycle_version": 1,
            "routine_scheduler_version": 1,
            "routine_manual_version": 1,
            "agent_cycle_version": agent_version,
        },
        ttl_seconds=20,
    )
    return client, agent, plugin


def test_agent_readiness_does_not_require_or_create_a_legacy_resource(state):
    client, agent, _ = agent_runtime(state)
    url = f"/api/v1/agents/{agent.id}/routines/runtime"
    with client.app.state.database.connect() as connection:
        before = connection.execute("SELECT count(*) FROM tasks").get
    assert client.get(url).json()["reason"] == "resource_unavailable"
    value = client.get(url + "?execution_mode=agent").json()
    assert value["available"] and value["manual_available"] and value["reason"] is None
    assert not (client.app.state.database.path.parent / "text-resources").exists()
    with client.app.state.database.connect() as connection:
        assert connection.execute("SELECT count(*) FROM tasks").get == before
        assert connection.execute("SELECT count(*) FROM model_calls").get == 0
    assert (
        TestClient(client.app, base_url=ORIGIN).get(url + "?execution_mode=agent").status_code
        == 401
    )
    assert client.get(url + "?execution_mode=unknown").status_code == 422


def test_agent_readiness_requires_current_grant_and_active_configured_agent(state):
    from bees_core.tools import ToolService

    client, agent, plugin = agent_runtime(state)
    url = f"/api/v1/agents/{agent.id}/routines/runtime?execution_mode=agent"
    tools = ToolService(client.app.state.database)
    tools.set_grant(
        agent.id, plugin.id, "deliverable.publish", {"expected_revision": 1, "enabled": False}
    )
    assert client.get(url).json()["reason"] == "tool_unavailable"
    tools.set_grant(
        agent.id, plugin.id, "deliverable.publish", {"expected_revision": 2, "enabled": True}
    )
    with client.app.state.store.transaction() as unit:
        current = unit.agents.get(agent.id)
        unit.agents.update(current.model_copy(update={"status": "paused"}), current.revision)
    assert client.get(url).json()["available"] is False
    with client.app.state.store.transaction() as unit:
        current = unit.agents.get(agent.id)
        config = current.provider_config | {"capabilities": {"text": True, "tool_calls": False}}
        unit.agents.update(
            current.model_copy(update={"status": "active", "provider_config": config}),
            current.revision,
        )
    assert client.get(url).json()["available"] is False


def test_agent_readiness_rejects_old_or_malformed_worker_capability(state):
    client, agent, _ = agent_runtime(state, agent_version=True)
    url = f"/api/v1/agents/{agent.id}/routines/runtime?execution_mode=agent"
    assert client.get(url).json()["reason"] == "worker_unavailable"
    for version in (None, "1", 0):
        client.app.state.store.put_cache(
            HEARTBEAT_KEY,
            {
                "last_seen": datetime.now(UTC).isoformat(),
                "tool_cycle_version": 1,
                "routine_scheduler_version": 1,
                "routine_manual_version": 1,
                "agent_cycle_version": version,
            },
            ttl_seconds=20,
        )
        assert client.get(url).json()["reason"] == "worker_unavailable"
