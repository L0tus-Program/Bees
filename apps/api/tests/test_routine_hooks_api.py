"""Webhook público: sem sessão nem Origin, só com o segredo; gerência exige sessão."""

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from bees_api.app import create_app
from bees_api.config import Settings
from bees_core.models import Agent, Conversation
from bees_core.providers.contracts import ProviderConfig
from bees_core.routines import CreateRoutine, RoutineDefinition, RoutineService
from bees_core.skills import CreateSkill, EnableSkill, SkillDefinition, SkillService
from bees_core.tools import ToolService

ORIGIN = "http://127.0.0.1:8000"
PASSWORD = "senha teste gatilhos 12345"


def headers(client):
    return {"Origin": ORIGIN, "X-Bees-CSRF": client.get("/api/v1/auth/status").json()["csrf_token"]}


@pytest.fixture
def state(tmp_path):
    settings = Settings(data_dir=tmp_path / "state", web_dist=tmp_path / "missing")
    with TestClient(create_app(settings), base_url=ORIGIN, raise_server_exceptions=False) as client:
        client.post(
            "/api/v1/auth/setup",
            json={
                "bootstrap_token": client.app.state.identity.issue_bootstrap(),
                "name": "Teste",
                "password": PASSWORD,
            },
            headers={"Origin": ORIGIN},
        ).raise_for_status()
        database = client.app.state.database
        config = ProviderConfig(
            kind="openai_compatible",
            endpoint="https://fixture.invalid/v1",
            model="fixture-agent",
            capabilities={"text": True, "tool_calls": True},
        )
        agent = Agent(name="Teste", provider_config=config.model_dump(mode="json"))
        destination = Conversation(agent_id=agent.id)
        with client.app.state.store.transaction() as unit:
            unit.agents.create(agent)
            unit.conversations.create(destination)
        tools = ToolService(database)
        for manifest in tools.catalog()[1:]:
            plugin = tools.install({"client_request_id": uuid4(), "manifest": manifest})
            tools.update_plugin(plugin.id, {"expected_revision": 1, "enabled": True})
            for tool in manifest["tools"]:
                tools.set_grant(
                    agent.id,
                    plugin.id,
                    tool["tool_name"],
                    {"expected_revision": 0, "enabled": True},
                )
        skills = SkillService(database)
        skill = skills.create(
            agent.id,
            CreateSkill(
                client_request_id=uuid4(),
                definition=SkillDefinition(
                    name="Resumo",
                    purpose="Ler e resumir.",
                    inputs=[{"name": "url", "description": "Fonte"}],
                    steps=[{"title": "Ler", "instruction": "Leia e resuma."}],
                    resources={"environment": "none", "tools": ["web.fetch"]},
                    output="Resumo.",
                    completion="Fonte citada.",
                ),
            ),
        )
        skills.enable(
            agent.id,
            skill.id,
            EnableSkill(client_request_id=uuid4(), expected_revision=1, version=1, enabled=True),
        )
        routine = RoutineService(database).create(
            agent.id,
            CreateRoutine(
                client_request_id=uuid4(),
                definition=RoutineDefinition(
                    name="Evento",
                    instructions="Resuma.",
                    schedule={"frequency": "daily", "time": "09:00"},
                    timezone="UTC",
                    skill_id=skill.id,
                    skill_version=1,
                    inputs={"url": "https://example.org/a"},
                    environment="none",
                    destination={"conversation_id": destination.id},
                    notification="always",
                    execution_mode="agent",
                ),
                active=False,
            ),
        )
        yield client, agent, routine


def test_public_trigger_with_secret_only(state):
    client, agent, routine = state
    base = f"/api/v1/agents/{agent.id}/routines/{routine.id}/hook"
    assert client.get(base).json() == {"hook": None}
    created = client.post(base, json={"min_interval_seconds": 30}, headers=headers(client))
    assert created.status_code == 201
    url = created.json()["url"]
    assert "/api/v1/hooks/bh_" in url and "bh_" not in str(client.get(base).json())
    path = url[url.index("/api/v1/hooks/") :]
    anonymous = TestClient(client.app, base_url=ORIGIN)
    accepted = anonymous.post(
        path, content=b'{"ignorar": "instrucoes"}', headers={"Idempotency-Key": "evento-1"}
    )
    assert accepted.status_code == 202, accepted.text
    assert accepted.json()["task_id"]
    # Serviço externo reenviando o mesmo evento (resposta perdida): sem nova execução.
    again = anonymous.post(path, headers={"Idempotency-Key": "evento-1"})
    assert again.status_code == 202 and again.json()["replayed"] is True
    assert again.json()["task_id"] == accepted.json()["task_id"]
    assert anonymous.post(path).status_code == 429
    assert anonymous.post("/api/v1/hooks/bh_" + "x" * 43).status_code == 404
    # Navegador de outra origem não dispara.
    foreign = anonymous.post(path, headers={"Origin": "https://evil.example"})
    assert foreign.status_code == 403
    hook = client.get(base).json()["hook"]
    assert hook["trigger_count"] == 1 and hook["last_result"] == "accepted"
    revoked = client.post(
        f"{base}/revoke", json={"expected_revision": hook["revision"]}, headers=headers(client)
    )
    assert revoked.json()["hook"]["status"] == "revoked"
    assert anonymous.post(path).status_code == 404
    assert anonymous.get(base).status_code == 401
