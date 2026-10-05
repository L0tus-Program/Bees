from uuid import uuid4

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from pydantic import SecretStr

from bees_api import onboarding
from bees_api.app import create_app
from bees_api.auth import COOKIE_NAME
from bees_api.config import Settings
from bees_core.models import Agent
from bees_core.providers.catalog import connection_for_provider
from bees_core.providers.discovery import DiscoveryAdapter

ORIGIN = "http://127.0.0.1:8000"
KEY = "only-discovery-test-key"
PASSWORD = "senha descartável de teste"


def mutation_headers(client):
    return {"origin": ORIGIN, "x-bees-csrf": client.get("/api/v1/auth/status").json()["csrf_token"]}


@pytest.fixture
def client(tmp_path, monkeypatch):
    state = {"requests": [], "hook": None, "payload": {"data": [{"id": "gpt-4o"}]}}

    def respond(request):
        state["requests"].append(request)
        if state["hook"]:
            state["hook"]()
        return httpx.Response(200, json=state["payload"])

    monkeypatch.setattr(
        onboarding,
        "DiscoveryAdapter",
        lambda resolver: DiscoveryAdapter(resolver, transport=httpx.MockTransport(respond)),
    )
    settings = Settings(
        data_dir=tmp_path / "data",
        web_dist=tmp_path / "missing",
        vault_key=SecretStr(Fernet.generate_key().decode()),
    )
    with TestClient(create_app(settings), base_url=ORIGIN) as value:
        token = value.app.state.identity.issue_bootstrap()
        response = value.post(
            "/api/v1/auth/setup",
            json={
                "bootstrap_token": token,
                "name": "Teste",
                "password": PASSWORD,
            },
            headers={"origin": ORIGIN},
        )
        assert response.status_code == 200
        value.test_state = state
        yield value


def discover(client, body):
    return client.post("/api/v1/models/discover", json=body, headers=mutation_headers(client))


def saved_agent(client, *, endpoint="https://api.openai.com/v1", secret=KEY):
    reference = client.app.state.vault.put(SecretStr(secret))
    config = connection_for_provider("custom", endpoint=endpoint, secret_ref=reference).model_dump()
    config.update(model="gpt-4o", capabilities={"text": True, "tool_calls": False})
    with client.app.state.store.transaction() as unit:
        agent = unit.agents.create(Agent(name="Teste", provider_config=config))
    return agent, reference


def test_provider_catalog_is_authenticated_and_explicit(client):
    response = client.get("/api/v1/providers")
    assert response.status_code == 200
    providers = {provider["id"]: provider for provider in response.json()["providers"]}
    assert set(providers) == {"openai", "openrouter", "gemini", "ollama", "custom", "custom_ollama"}
    assert providers["openai"]["endpoint"] == "https://api.openai.com/v1"
    assert providers["openrouter"]["endpoint"] == "https://openrouter.ai/api/v1"
    assert (
        providers["gemini"]["endpoint"] == "https://generativelanguage.googleapis.com/v1beta/openai"
    )
    assert providers["ollama"]["endpoint"] == "http://127.0.0.1:11434"
    assert providers["custom"]["endpoint"] == providers["custom_ollama"]["endpoint"] == ""
    assert providers["openai"]["requires_api_key"] is True
    assert providers["ollama"]["requires_api_key"] is False
    assert [model["id"] for model in providers["openai"]["models"]] == [
        "gpt-5-mini",
        "gpt-5-nano",
        "gpt-4.1",
        "gpt-4.1-mini",
        "gpt-4o-mini",
    ]
    assert [model["id"] for model in providers["gemini"]["models"]] == [
        "gemini-3.8-flash",
        "gemini-3.5-flash-lite",
    ]
    assert {model["id"] for model in providers["ollama"]["models"]} == {
        "llama3.2:latest",
        "qwen3:latest",
    }
    assert providers["custom"]["models"] == providers["custom_ollama"]["models"] == []
    assert client.test_state["requests"] == []
    client.cookies.clear()
    assert client.get("/api/v1/providers").status_code == 401


@pytest.mark.parametrize(
    "provider,endpoint,payload,model",
    [
        ("openai", "https://api.openai.com/v1/models", {"data": [{"id": "gpt-4o"}]}, "gpt-4o"),
        (
            "gemini",
            "https://generativelanguage.googleapis.com/v1beta/openai/models",
            {"data": [{"id": "gemini-2.5-pro"}]},
            "gemini-2.5-pro",
        ),
        (
            "openrouter",
            "https://openrouter.ai/api/v1/models",
            {"data": [{"id": "vendor/model", "architecture": {"output_modalities": ["text"]}}]},
            "vendor/model",
        ),
    ],
)
def test_named_discovery_ephemeral_key_never_persists_or_issues_receipt(
    client, provider, endpoint, payload, model
):
    client.test_state["payload"] = payload
    response = discover(client, {"provider_id": provider, "api_key": KEY})
    assert response.status_code == 200, response.text
    assert response.json() == {"provider_id": provider, "models": [{"id": model, "name": model}]}
    assert KEY not in response.text and "validation_token" not in response.text
    requests = client.test_state["requests"]
    assert len(requests) == 1 and requests[0].method == "GET" and requests[0].content == b""
    assert str(requests[0].url) == endpoint
    assert requests[0].headers["authorization"] == f"Bearer {KEY}"
    with client.app.state.store.transaction(write=False) as unit:
        assert unit.agents.list() == [] and unit.messages.list() == []
    assert list(client.app.state.vault.directory.glob("*.secret")) == []
    assert client.app.state.receipts._items == {}


def test_discovery_does_not_require_vault_when_key_is_transient(client, monkeypatch):
    from bees_core.providers.vault import VaultStatus

    monkeypatch.setattr(
        client.app.state.vault,
        "status",
        lambda: VaultStatus(
            available=False, backend="fernet", reason="Não provisionado para teste."
        ),
    )
    assert discover(client, {"provider_id": "openai", "api_key": KEY}).status_code == 200


def test_custom_endpoint_and_empty_catalog_remain_explicit(client):
    client.test_state["payload"] = {"data": []}
    response = discover(client, {"provider_id": "custom", "endpoint": "http://localhost:9000/v1"})
    assert response.status_code == 200 and response.json()["models"] == []
    assert "authorization" not in client.test_state["requests"][0].headers
    assert client.app.state.receipts._items == {}


def test_ollama_discovery_only_tags_local_models(client):
    client.test_state["payload"] = {
        "models": [
            {"name": "llama3.2:latest"},
            {"name": "remote-alias", "remote_model": "cloud"},
        ]
    }
    response = discover(client, {"provider_id": "ollama"})
    assert response.status_code == 200
    assert response.json()["models"] == [{"id": "llama3.2:latest", "name": "llama3.2:latest"}]
    request = client.test_state["requests"][0]
    assert request.url.path == "/api/tags" and "authorization" not in request.headers


@pytest.mark.parametrize(
    "body",
    [
        {"provider_id": "openai", "api_key": KEY, "endpoint": "https://evil.invalid/v1"},
        {"provider_id": "openai"},
        {"provider_id": "custom"},
        {"provider_id": "custom", "endpoint": "http://remote.invalid"},
        {"provider_id": "ollama", "api_key": KEY},
        {"provider_id": "custom_ollama", "endpoint": "https://remote.invalid"},
        {"provider_id": "custom", "endpoint": "https://user:pass@test.invalid"},
        {"provider_id": "custom", "endpoint": "https://test.invalid?token=secret"},
        {
            "provider_id": "custom",
            "endpoint": "https://test.invalid",
            "secret_ref": "env:TEST_SECRET",
        },
        {"provider_id": "made-up", "api_key": KEY},
    ],
)
def test_invalid_selection_or_destination_never_sends_credentials(client, body):
    response = discover(client, body)
    assert response.status_code == 422 and KEY not in response.text
    assert client.test_state["requests"] == []


def test_discovery_authentication_and_csrf_run_before_provider(client):
    body = {"provider_id": "openai", "api_key": KEY}
    response = client.post("/api/v1/models/discover", json=body, headers={"origin": ORIGIN})
    assert response.status_code == 403
    client.cookies.clear()
    response = client.post("/api/v1/models/discover", json=body, headers={"origin": ORIGIN})
    assert response.status_code == 401
    assert client.test_state["requests"] == []


def test_reuse_reference_requires_same_agent_connection_and_explicit_opt_in(client):
    agent, reference = saved_agent(client)
    body = {"provider_id": "openai", "agent_id": str(agent.id), "secret_ref": reference}
    assert discover(client, body).status_code == 200
    assert client.test_state["requests"][-1].headers["authorization"] == f"Bearer {KEY}"
    before = len(client.test_state["requests"])
    for changed in (
        {"provider_id": "openai", "secret_ref": reference},
        body | {"api_key": "different-test-key"},
        body | {"provider_id": "custom", "endpoint": "https://elsewhere.invalid/v1"},
        body | {"secret_ref": f"vault:{uuid4()}"},
    ):
        assert discover(client, changed).status_code == 422
    assert len(client.test_state["requests"]) == before
    other, _ = saved_agent(client, secret="other-test-key")
    assert discover(client, body | {"agent_id": str(other.id)}).status_code == 422


def test_saved_env_reference_is_also_bound_to_existing_connection(client, monkeypatch):
    monkeypatch.setenv("DISCOVERY_TEST_ENV", KEY)
    config = connection_for_provider("openai", secret_ref="env:DISCOVERY_TEST_ENV").model_dump()
    config.update(model="gpt-4o", capabilities={"text": True, "tool_calls": False})
    with client.app.state.store.transaction() as unit:
        agent = unit.agents.create(Agent(name="Teste", provider_config=config))
    response = discover(
        client,
        {
            "provider_id": "openai",
            "agent_id": str(agent.id),
            "secret_ref": "env:DISCOVERY_TEST_ENV",
        },
    )
    assert response.status_code == 200 and KEY not in response.text
    assert (
        discover(
            client, {"provider_id": "openai", "secret_ref": "env:DISCOVERY_TEST_ENV"}
        ).status_code
        == 422
    )


@pytest.mark.parametrize("changed", ["agent_revision", "session_revoked"])
def test_inflight_discovery_rechecks_authorization_and_agent_revision(client, changed):
    agent, reference = saved_agent(client)

    def mutate():
        if changed == "session_revoked":
            client.app.state.identity.logout(client.cookies.get(COOKIE_NAME))
        else:
            with client.app.state.store.transaction() as unit:
                current = unit.agents.get(agent.id)
                unit.agents.update(
                    current.model_copy(update={"purpose": "Mudou"}),
                    expected_revision=current.revision,
                )

    client.test_state["hook"] = mutate
    response = discover(
        client, {"provider_id": "openai", "agent_id": str(agent.id), "secret_ref": reference}
    )
    assert response.status_code == (401 if changed == "session_revoked" else 409)
    assert "gpt-4o" not in response.text and KEY not in response.text
    assert client.app.state.receipts._items == {}


def test_deleted_vault_key_does_not_fallback_to_environment(client, monkeypatch):
    agent, reference = saved_agent(client)
    client.app.state.vault.delete(reference)
    monkeypatch.setenv("BEES_REQUEST_KEY", "must-not-be-used-as-fallback")
    response = discover(
        client, {"provider_id": "openai", "agent_id": str(agent.id), "secret_ref": reference}
    )
    assert response.status_code == 502 and response.json()["error"]["code"] == "secret_unavailable"
    assert client.test_state["requests"] == []


@pytest.mark.parametrize(
    "payload,code",
    [
        ({"data": {}}, "invalid_response"),
        ({"data": [{"id": "gpt-4o", "metadata": "x" * 4194304}]}, "response_too_large"),
    ],
)
def test_provider_failures_do_not_return_keys_or_raw_body(client, payload, code):
    client.test_state["payload"] = payload
    response = discover(client, {"provider_id": "openai", "api_key": KEY})
    assert response.status_code == 502 and response.json()["error"]["code"] == code
    assert KEY not in response.text and "metadata" not in response.text


def test_empty_legacy_agent_config_cannot_authorize_reference(client):
    with client.app.state.store.transaction() as unit:
        agent = unit.agents.create(Agent(name="Legado"))
    response = discover(
        client,
        {"provider_id": "openai", "agent_id": str(agent.id), "secret_ref": "env:SECRET_TEST"},
    )
    assert response.status_code == 422 and client.test_state["requests"] == []
