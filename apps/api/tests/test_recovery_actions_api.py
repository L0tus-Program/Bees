"""Human task action pages keep exact scope and never replay or dispatch."""

from uuid import uuid4

import pytest
from test_task_actions_api import journal, paused_task
from test_tasks import state as state

from bees_core.executors import CommandQueue, ExecutorService


@pytest.mark.parametrize("code", ["private-path-token", "late_private-path-token", "x" * 100])
def test_executor_text_is_not_published_by_authenticated_action_page(state, code):
    client, agent, _, _, _ = state
    db = client.app.state.database
    path, task = paused_task(client, agent)
    action = journal(client, task)
    service = ExecutorService(db)
    invite = service.invite({"kind": "computer_runtime", "name": "Fixture"})
    executor = service.exchange({"token": invite["token"]})["executor_id"]
    command = str(action.id)
    stamp = action.created_at.isoformat()
    with db.transaction() as connection:
        connection.execute(
            "INSERT INTO executor_commands(id,created_at,updated_at,executor_id,agent_id,"
            "tool_name,arguments_json,deadline_at,status) VALUES(?,?,?,?,?,?,?,?,'sent')",
            (command, stamp, stamp, executor, str(agent.id), action.tool_name, "{}", stamp),
        )
    queue = CommandQueue(db)
    assert queue.report(executor, command, "failed", code=code)
    before = queue.get(command)
    response = client.get(path + "/recovery/actions")
    assert response.status_code == 200
    assert response.json()["items"][0]["executor_code"] is None
    assert response.json()["items"][0]["executor_command"] == "failed"
    assert code not in response.text
    assert queue.get(command) == before


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
        "executor_command",
        "executor_code",
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


@pytest.mark.parametrize(
    "mismatch", ["agent", "tool", "computer_agent", "computer_executor", "executor_kind"]
)
def test_inconsistent_command_returns_sanitized_conflict_without_partial_page(state, mismatch):
    client, agent, other, _, _ = state
    db = client.app.state.database
    path, task = paused_task(client, agent)
    journal(client, task)  # A valid record must not leak as a partial page.
    action = journal(client, task)
    service = ExecutorService(db)
    invite = service.invite({"kind": "computer_runtime", "name": "Fixture"})
    executor = service.exchange({"token": invite["token"]})["executor_id"]
    other_invite = service.invite({"kind": "computer_runtime", "name": "Other"})
    other_executor = service.exchange({"token": other_invite["token"]})["executor_id"]
    command, stamp, computer = str(action.id), action.created_at.isoformat(), None
    secret = "private-tool-path-token"
    with db.transaction() as connection:
        if mismatch.startswith("computer_") or mismatch == "executor_kind":
            computer = str(uuid4())
            connection.execute(
                "INSERT INTO computers(id,created_at,updated_at,agent_id,executor_id,name,status) "
                "VALUES(?,?,?,?,?,?,'removed')",
                (
                    computer,
                    stamp,
                    stamp,
                    str(other.id) if mismatch == "computer_agent" else str(agent.id),
                    other_executor if mismatch == "computer_executor" else executor,
                    secret,
                ),
            )
        connection.execute(
            "INSERT INTO executor_commands(id,created_at,updated_at,executor_id,agent_id,"
            "computer_id,tool_name,arguments_json,deadline_at,status,error_code) "
            "VALUES(?,?,?,?,?,?,?,?,?,'unknown',?)",
            (
                command,
                stamp,
                stamp,
                executor,
                str(other.id) if mismatch == "agent" else str(agent.id),
                computer,
                secret if mismatch == "tool" else action.tool_name,
                "{}",
                stamp,
                secret,
            ),
        )
    if mismatch == "executor_kind":
        with db.transaction() as connection:
            connection.execute("UPDATE executors SET kind='personal' WHERE id=?", (executor,))
    queue = CommandQueue(db)
    before = queue.get(command)
    response = client.get(path + "/recovery/actions")
    assert response.status_code == 409
    assert response.json() == {
        "error": {
            "code": "state_conflict",
            "message": "Registro ausente ou alterado; atualize a tela.",
        }
    }
    assert secret not in response.text and command not in response.text
    assert queue.get(command) == before
    # Other valid tasks remain inspectable; refusal does not become global authority.
    other_path, _ = paused_task(client, agent)
    assert client.get(other_path + "/recovery/actions").status_code == 200


@pytest.mark.parametrize("substitute", [False, True], ids=["missing", "view"])
@pytest.mark.parametrize("empty", [False, True], ids=["history", "empty"])
def test_missing_command_store_returns_conflict_even_for_empty_page(state, substitute, empty):
    client, agent, other, _, _ = state
    db = client.app.state.database
    path, task = paused_task(client, agent)
    if not empty:
        journal(client, task)
    before = client.get(path).json()
    with db.transaction() as connection:
        connection.execute("ALTER TABLE executor_commands RENAME TO preserved_commands")
        if substitute:
            connection.execute("CREATE VIEW executor_commands AS SELECT * FROM preserved_commands")
        schema = connection.execute(
            "SELECT type,name,sql FROM sqlite_schema ORDER BY name"
        ).fetchall()
    response = client.get(path + "/recovery/actions")
    assert response.status_code == 409
    assert response.json() == {
        "error": {
            "code": "state_conflict",
            "message": "Registro ausente ou alterado; atualize a tela.",
        }
    }
    assert "preserved_commands" not in response.text and "executor_commands" not in response.text
    assert client.get(path).json() == before
    assert (
        client.get(path.replace(str(agent.id), str(other.id)) + "/recovery/actions").status_code
        == 404
    )
    assert client.get(path + "/recovery/actions?after=" + str(uuid4())).status_code == 404
    with db.transaction() as connection:
        assert (
            connection.execute("SELECT type,name,sql FROM sqlite_schema ORDER BY name").fetchall()
            == schema
        )
        if substitute:
            connection.execute("DROP VIEW executor_commands")
        connection.execute("ALTER TABLE preserved_commands RENAME TO executor_commands")
    assert client.get(path + "/recovery/actions").status_code == 200
