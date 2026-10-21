"""Authenticated read-only recovery projection without exposing execution data."""

from uuid import uuid4

from test_task_actions_api import journal, paused_task
from test_tasks import state as state

from bees_core.models import Agent


def test_recovery_inventory_is_scoped_private_and_readonly(state):
    client, first, other, _, _ = state
    path, task = paused_task(client, first)
    journal(client, task, "outcome_unknown")
    journal(client, task, "confirmed")
    before = client.get(path).json()
    response = client.get(path + "/recovery")
    assert response.status_code == 200
    result = response.json()
    assert result["task_id"] == task["id"] and result["agent_id"] == str(first.id)
    assert result["actions"]["total"] == 2 and result["actions"]["outcome_unknown"] == 1
    assert result["actions"]["unknown_unacknowledged"] == 1
    assert result["executor_evidence"] == "not_inspected"
    assert "conteudo-privado" not in response.text
    assert client.get(path).json() == before
    assert response.headers["cache-control"] == "no-store"
    for url in [
        path.replace(str(first.id), str(other.id)) + "/recovery",
        path.replace(task["id"], str(uuid4())) + "/recovery",
    ]:
        refused = client.get(url)
        assert refused.status_code == 404 and task["id"] not in refused.text


def test_inventory_uses_get_only_and_requires_human_session(state):
    client, first, _, _, _ = state
    path, _ = paused_task(client, first)
    assert client.post(path + "/recovery", json={}).status_code in (403, 405)
    client.cookies.clear()
    assert client.get(path + "/recovery").status_code == 401


def test_empty_agent_does_not_inherit_another_task_inventory(state):
    client, first, _, _, _ = state
    path, _ = paused_task(client, first)
    with client.app.state.store.transaction() as unit:
        agent = unit.agents.create(Agent(name="Sem histórico"))
    assert client.get(path.replace(str(first.id), str(agent.id)) + "/recovery").status_code == 404
