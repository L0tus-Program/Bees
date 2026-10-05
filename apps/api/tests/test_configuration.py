from uuid import UUID

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from pydantic import SecretStr

from bees_api import onboarding
from bees_api.app import create_app
from bees_api.config import Settings
from bees_core.models import Agent, Conversation, Memory, Message
from bees_core.providers.base import create_adapter
from bees_core.providers.contracts import ProviderConfig
from bees_core.storage.store import RevisionConflict

ORIGIN = "http://127.0.0.1:8000"
KEY = "CHAVE-DESCARTAVEL-TESTE"


@pytest.fixture
def configured(tmp_path, monkeypatch):
    seen = []

    def respond(request):
        seen.append(request)
        return httpx.Response(200, json={"data": [{"id": "modelo-a"}, {"id": "modelo-b"}]})

    monkeypatch.setattr(
        onboarding,
        "create_adapter",
        lambda kind, resolver: create_adapter(
            kind, resolver, transport=httpx.MockTransport(respond)
        ),
    )
    # Linux também tem um cofre real cifrado, com chave privada descartável.
    app = create_app(
        Settings(
            data_dir=tmp_path,
            web_dist=tmp_path / "missing",
            vault_key=SecretStr(Fernet.generate_key().decode()),
        )
    )
    with TestClient(app, base_url=ORIGIN) as client:
        token = app.state.identity.issue_bootstrap()
        response = client.post(
            "/api/v1/auth/setup",
            json={
                "bootstrap_token": token,
                "name": "Teste",
                "password": "senha descartavel 123456",
            },
            headers={"Origin": ORIGIN},
        )
        assert response.status_code == 200
        reference = app.state.vault.put(SecretStr(KEY))
        config = ProviderConfig(
            kind="openai_compatible",
            endpoint="https://modelo.example/v1",
            model="modelo-a",
            secret_ref=reference,
            capabilities={"text": True, "tool_calls": False},
        )
        with app.state.store.transaction() as unit:
            agent = unit.agents.create(
                Agent(
                    name="Original",
                    purpose="Propósito",
                    provider_config=config.model_dump(mode="json"),
                )
            )
            conversation = unit.conversations.create(Conversation(agent_id=agent.id))
            unit.messages.create(
                Message(conversation_id=conversation.id, role="user", content="Histórico")
            )
            memory = unit.memories.create(
                Memory(scope="agent", agent_id=agent.id, content="Memória")
            )
        headers = {
            "Origin": ORIGIN,
            "X-Bees-CSRF": client.get("/api/v1/auth/status").json()["csrf_token"],
        }
        yield client, agent, conversation, memory, config.model_dump(mode="json"), headers, seen


def test_switch_model_preserves_graph_and_explicit_credential(configured):
    client, agent, conversation, memory, original, headers, seen = configured
    config = original | {"model": "modelo-b"}
    probe = client.post(
        "/api/v1/models/test", json={"agent_id": str(agent.id), "config": config}, headers=headers
    )
    assert probe.status_code == 200
    response = client.post(
        f"/api/v1/agents/{agent.id}/configuration",
        json={
            "config": config,
            "expected_revision": agent.revision,
            "validation_token": probe.json()["validation_token"],
        },
        headers=headers,
    )
    assert response.status_code == 200, response.text
    assert response.json()["conversation_id"] == str(conversation.id)
    assert response.json()["provider_config"]["model"] == "modelo-b"
    assert response.json()["revision"] == 2
    assert KEY not in response.text
    with client.app.state.store.transaction(write=False) as unit:
        assert unit.memories.get(memory.id) == memory
        assert len(unit.messages.list(conversation_id=conversation.id)) == 1
        assert unit.agents.get(agent.id).name == agent.name
    assert seen[0].headers["authorization"] == f"Bearer {KEY}"


def test_saved_credential_is_not_forwarded_to_changed_endpoint(configured):
    client, agent, _, _, config, headers, seen = configured
    response = client.post(
        "/api/v1/models/test",
        json={
            "agent_id": str(agent.id),
            "config": config | {"endpoint": "https://other.example/v1"},
        },
        headers=headers,
    )
    assert response.status_code == 422
    assert seen == []
    response = client.post("/api/v1/models/test", json={"config": config}, headers=headers)
    assert response.status_code == 422
    assert seen == []


def test_receipt_cannot_reconfigure_another_agent(configured):
    client, agent, _, _, config, headers, _ = configured
    probe = client.post(
        "/api/v1/models/test", json={"agent_id": str(agent.id), "config": config}, headers=headers
    )
    with client.app.state.store.transaction() as unit:
        other = unit.agents.create(Agent(name="Outra", provider_config=config))
    response = client.post(
        f"/api/v1/agents/{other.id}/configuration",
        json={
            "config": config,
            "expected_revision": 1,
            "validation_token": probe.json()["validation_token"],
        },
        headers=headers,
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "validation_required"


def test_replace_key_rolls_back_blob_if_revision_changes(configured, monkeypatch):
    client, agent, _, _, config, headers, _ = configured
    config = config | {"secret_ref": None}
    body = {"agent_id": str(agent.id), "config": config, "api_key": "NEW-TEST-CREDENTIAL"}
    probe = client.post("/api/v1/models/test", json=body, headers=headers)
    assert probe.status_code == 200
    before = set(client.app.state.vault.directory.glob("*.secret"))

    def fail(*args, **kwargs):
        raise RevisionConflict("Conflito de teste.")

    monkeypatch.setattr(client.app.state.providers, "update_profile", fail)
    response = client.post(
        f"/api/v1/agents/{agent.id}/configuration",
        json=body | {"expected_revision": 1, "validation_token": probe.json()["validation_token"]},
        headers=headers,
    )
    assert response.status_code == 409
    assert set(client.app.state.vault.directory.glob("*.secret")) == before
    assert client.app.state.resolver.resolve(configured[4]["secret_ref"]).get_secret_value() == KEY


def test_configuration_requires_csrf_and_leaves_state_untouched(configured):
    client, agent, _, _, config, _, seen = configured
    response = client.post(
        f"/api/v1/agents/{agent.id}/configuration",
        json={"config": config, "expected_revision": 1, "validation_token": "a" * 43},
        headers={"Origin": ORIGIN},
    )
    assert response.status_code == 403
    assert seen == []
    with client.app.state.store.transaction(write=False) as unit:
        assert unit.agents.get(UUID(str(agent.id))) == agent


def test_invalid_key_input_never_echoes_credentials(configured):
    client, agent, _, _, config, headers, _ = configured
    response = client.post(
        f"/api/v1/agents/{agent.id}/configuration",
        json={
            "config": config,
            "api_key": {"private": KEY},
            "expected_revision": 1,
            "validation_token": "a" * 43,
        },
        headers=headers,
    )
    assert response.status_code == 422
    assert KEY not in response.text
    assert "private" not in response.text


def test_new_endpoint_uses_only_the_new_credential_and_preserves_old_reference(configured):
    client, agent, conversation, _, original, headers, seen = configured
    config = original | {"endpoint": "https://new-model.example/v1", "secret_ref": None}
    body = {"agent_id": str(agent.id), "config": config, "api_key": "NOVA-CHAVE-DESCARTAVEL"}
    probe = client.post("/api/v1/models/test", json=body, headers=headers)
    assert probe.status_code == 200
    assert seen[-1].headers["authorization"] == "Bearer NOVA-CHAVE-DESCARTAVEL"
    assert seen[-1].url.host == "new-model.example"
    changed = client.post(
        f"/api/v1/agents/{agent.id}/configuration",
        json=body | {"expected_revision": 1, "validation_token": probe.json()["validation_token"]},
        headers=headers,
    )
    assert changed.status_code == 200
    saved = changed.json()
    assert saved["conversation_id"] == str(conversation.id)
    new_reference = saved["provider_config"]["secret_ref"]
    assert new_reference != original["secret_ref"]
    assert (
        client.app.state.resolver.resolve(new_reference).get_secret_value()
        == "NOVA-CHAVE-DESCARTAVEL"
    )
    assert client.app.state.resolver.resolve(original["secret_ref"]).get_secret_value() == KEY
    assert "NOVA-CHAVE-DESCARTAVEL" not in changed.text
