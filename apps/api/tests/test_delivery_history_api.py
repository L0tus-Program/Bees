"""Consulta autenticada além da janela recente e referência exata sem execução."""

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from bees_api.app import create_app
from bees_api.config import Settings
from bees_core.models import Agent, Conversation, Message, Run, Task

ORIGIN = "http://127.0.0.1:8000"


@pytest.fixture
def history_state(tmp_path):
    app = create_app(Settings(data_dir=tmp_path / "state", web_dist=tmp_path / "missing"))
    with TestClient(app, base_url=ORIGIN) as client:
        token = app.state.identity.issue_bootstrap()
        assert (
            client.post(
                "/api/v1/auth/setup",
                json={"bootstrap_token": token, "name": "Teste", "password": "senha teste 123456"},
                headers={"Origin": ORIGIN},
            ).status_code
            == 200
        )
        agent, other = Agent(name="Primeira"), Agent(name="Outra")
        conversation = Conversation(agent_id=agent.id)
        task = Task(
            agent_id=agent.id,
            conversation_id=conversation.id,
            title="Entrega antiga",
            objective="Consolidar",
            status="completed",
        )
        with app.state.store.transaction() as unit:
            unit.agents.create(agent)
            unit.agents.create(other)
            unit.conversations.create(conversation)
            unit.tasks.create(task)
            message = unit.messages.create(
                Message(
                    conversation_id=conversation.id,
                    role="assistant",
                    content="Resultado preservado",
                )
            )
            run = unit.runs.create(
                Run(
                    task_id=task.id,
                    status="completed",
                    checkpoint={"result_message_id": str(message.id)},
                )
            )
        artifact = app.state.artifacts.create(
            agent.id, task.id, name="resultado.txt", media_type="text/plain", content=b"conteudo"
        )
        yield client, agent, other, conversation, task, message, run, artifact


def test_session_required_and_safe_metadata(history_state):
    client, agent, _, _, task, message, run, artifact = history_state
    base = f"/api/v1/agents/{agent.id}/deliveries"
    anonymous = TestClient(client.app, base_url=ORIGIN)
    for path in [
        base,
        f"{base}/results/{message.id}?run_id={run.id}",
        f"{base}/artifacts/{artifact.id}",
    ]:
        assert anonymous.get(path).status_code == 401
    with client.app.state.database.transaction(write=False) as connection:
        before = list(connection.execute("SELECT * FROM domain_events"))
    page = client.get(base).json()
    assert page["limit"] == 50 and len(page["items"]) == 2
    assert page["next_cursor"] is None
    assert all(item["task_id"] == str(task.id) for item in page["items"])
    result = client.get(f"{base}/results/{message.id}?run_id={run.id}").json()
    assert result["content"] == "Resultado preservado"
    assert result["item"]["run_id"] == str(run.id)
    metadata = client.get(f"{base}/artifacts/{artifact.id}").json()
    assert metadata["id"] == str(artifact.id) and metadata["version"] == 1
    assert not {"storage_key", "metadata", "provider_config", "checkpoint"}.intersection(metadata)
    with client.app.state.database.transaction(write=False) as connection:
        assert list(connection.execute("SELECT * FROM domain_events")) == before


def test_scope_not_found_and_cursor_binding(history_state):
    client, agent, other, conversation, task, message, run, artifact = history_state
    base = f"/api/v1/agents/{agent.id}/deliveries"
    other_base = f"/api/v1/agents/{other.id}/deliveries"
    for path in [
        f"{other_base}/results/{message.id}?run_id={run.id}",
        f"{base}/results/{message.id}?run_id={uuid4()}",
        f"{other_base}/artifacts/{artifact.id}",
        f"{other_base}?task_id={task.id}",
        f"{other_base}?conversation_id={conversation.id}",
    ]:
        assert client.get(path).status_code == 404
    page = client.get(base, params={"limit": 1}).json()
    assert len(page["items"]) == 1 and page["next_cursor"]
    next_page = client.get(base, params={"limit": 1, "cursor": page["next_cursor"]})
    assert next_page.status_code == 200
    assert next_page.json()["items"][0]["id"] != page["items"][0]["id"]
    assert next_page.json()["next_cursor"] is None
    for params in [
        {"q": "another", "cursor": page["next_cursor"]},
        {"cursor": "bad"},
        {"limit": 101},
        {"limit": 0},
        {"q": "x" * 201},
    ]:
        assert client.get(base, params=params).status_code == 422
    assert client.get(base, params={"q": str(artifact.id)}).json()["items"][0]["id"] == str(
        artifact.id
    )
    assert client.get(base, params={"q": "nothing"}).json()["items"] == []


def test_removed_version_stays_identifiable_and_download_never_replays(history_state):
    client, agent, _, _, _, _, _, artifact = history_state
    base = f"/api/v1/agents/{agent.id}/deliveries"
    with client.app.state.store.transaction() as unit:
        unit.artifacts.update(artifact.model_copy(update={"status": "deleted"}), artifact.revision)
    current = client.get(f"{base}/artifacts/{artifact.id}").json()
    assert current["status"] == "deleted" and current["version"] == artifact.version
    page = client.get(base, params={"q": str(artifact.id)}).json()
    assert page["items"][0]["status"] == "deleted"
    response = client.get(f"/api/v1/agents/{agent.id}/artifacts/{artifact.id}/download")
    assert response.status_code >= 400
