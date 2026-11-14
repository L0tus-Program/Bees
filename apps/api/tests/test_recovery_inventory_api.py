"""Authenticated read-only recovery projection without exposing execution data."""

from uuid import uuid4

import pytest
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


@pytest.mark.parametrize("table", ["runs", "actions", "model_calls"])
@pytest.mark.parametrize("substitute", ["missing", "view", "empty_view"])
def test_inventory_refuses_missing_or_substituted_history_with_sanitized_conflict(
    state, table, substitute
):
    client, agent, other, _, _ = state
    path, task = paused_task(client, agent)
    journal(client, task, "outcome_unknown")
    db = client.app.state.database
    before = client.get(path + "/recovery").json()
    with db.transaction() as connection:
        connection.execute(f"ALTER TABLE {table} RENAME TO preserved_records")
        if substitute != "missing":
            condition = " WHERE 0" if substitute == "empty_view" else ""
            connection.execute(f"CREATE VIEW {table} AS SELECT * FROM preserved_records{condition}")
        schema = connection.execute(
            "SELECT type,name,sql FROM sqlite_schema ORDER BY name"
        ).fetchall()
    for _ in range(2):
        response = client.get(path + "/recovery")
        assert response.status_code == 409
        assert response.json() == {
            "error": {
                "code": "state_conflict",
                "message": "Registro ausente ou alterado; atualize a tela.",
            }
        }
        assert table not in response.text and "preserved_records" not in response.text
    assert client.get(path.replace(str(agent.id), str(other.id)) + "/recovery").status_code == 404
    with db.transaction() as connection:
        assert (
            connection.execute("SELECT type,name,sql FROM sqlite_schema ORDER BY name").fetchall()
            == schema
        )
        if substitute != "missing":
            connection.execute(f"DROP VIEW {table}")
        connection.execute(f"ALTER TABLE preserved_records RENAME TO {table}")
    restored = client.get(path + "/recovery")
    assert restored.status_code == 200
    assert {k: v for k, v in restored.json().items() if k != "observed_at"} == {
        k: v for k, v in before.items() if k != "observed_at"
    }
