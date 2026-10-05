import asyncio
import copy
import json
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from threading import Event, Lock
from uuid import UUID

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from pydantic import SecretStr

from bees_api import onboarding
from bees_api.app import create_app
from bees_api.auth import COOKIE_NAME
from bees_api.config import Settings
from bees_core.providers.base import create_adapter
from bees_core.storage.store import StoreError

ORIGIN = "http://127.0.0.1:8000"
PASSWORD = "senha apenas de teste 123456"
API_KEY = "TEST-PRIVATE-MODEL-CREDENTIAL"


def model_body() -> dict:
    return {
        "config": {
            "kind": "openai_compatible",
            "endpoint": "https://model.example/v1",
            "model": "model-for-tests",
            "capabilities": {"text": True, "tool_calls": False},
        },
        "api_key": API_KEY,
    }


def headers(client) -> dict:
    return {
        "Origin": ORIGIN,
        "X-Bees-CSRF": client.get("/api/v1/auth/status").json()["csrf_token"],
    }


def setup(client) -> None:
    token = client.app.state.identity.issue_bootstrap()
    result = client.post(
        "/api/v1/auth/setup",
        json={"bootstrap_token": token, "name": "Pessoa de teste", "password": PASSWORD},
        headers={"Origin": ORIGIN},
    )
    assert result.status_code == 200


def probe(client, body=None) -> str:
    result = client.post("/api/v1/models/test", json=body or model_body(), headers=headers(client))
    assert result.status_code == 200, result.text
    assert result.json()["status"] == "ok"
    assert API_KEY not in result.text
    return result.json()["validation_token"]


def creation_body(token: str) -> dict:
    return model_body() | {
        "name": "Abelha de teste",
        "purpose": "Organizar ideias",
        "instructions": "Responda em português.",
        "validation_token": token,
    }


@pytest.fixture
def environment(tmp_path, monkeypatch):
    seen = []

    def respond(request):
        seen.append(request)
        assert request.headers["authorization"] == f"Bearer {API_KEY}"
        if request.url.path == "/v1/models":
            assert request.method == "GET"
            return httpx.Response(200, json={"data": [{"id": "model-for-tests"}]})
        assert request.url.path == "/v1/chat/completions"
        body = json.loads(request.content)
        assert body["store"] is False
        assert body["stream"] is False
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {"role": "assistant", "content": "Plano de teste persistido."},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            },
        )

    transport = httpx.MockTransport(respond)
    monkeypatch.setattr(
        onboarding,
        "create_adapter",
        lambda kind, resolver: create_adapter(kind, resolver, transport=transport),
    )
    settings = Settings(
        data_dir=tmp_path / "state",
        web_dist=tmp_path / "missing",
        vault_key=SecretStr(Fernet.generate_key().decode()),
    )
    return settings, transport, seen


@pytest.fixture
def client(environment):
    settings, transport, _ = environment
    with TestClient(create_app(settings), base_url=ORIGIN) as result:
        result.app.state.providers.transport = transport
        setup(result)
        yield result


def test_authenticated_setup_create_vault_chat_restart_and_replay(environment) -> None:
    settings, transport, seen = environment
    with TestClient(create_app(settings), base_url=ORIGIN) as client:
        client.app.state.providers.transport = transport
        setup(client)
        dashboard = client.get("/api/v1/onboarding").json()
        assert dashboard["agents"] == []
        assert dashboard["vault"]["available"] is True
        assert all(item["status"] == "planned" for item in dashboard["environments"])
        body = creation_body(probe(client))
        response = client.post("/api/v1/agents", json=body, headers=headers(client))
        assert response.status_code == 201, response.text
        agent = response.json()
        assert agent["provider_config"]["secret_ref"].startswith("vault:")
        assert API_KEY not in response.text
        assert (
            client.app.state.resolver.resolve(
                agent["provider_config"]["secret_ref"]
            ).get_secret_value()
            == API_KEY
        )
        with client.app.state.store.transaction(write=False) as unit:
            stored = unit.agents.get(UUID(agent["id"]))
            assert API_KEY not in stored.model_dump_json()
            assert body["validation_token"] not in stored.model_dump_json()
            assert client.cookies.get(COOKIE_NAME) not in stored.model_dump_json()
        encrypted = list((settings.data_dir / "vault").glob("*.secret"))
        assert len(encrypted) == 1
        assert API_KEY.encode() not in encrypted[0].read_bytes()
        response = client.post(
            f"/api/v1/agents/{agent['id']}/chat",
            json={"conversation_id": agent["conversation_id"], "content": "Planeje uma tarefa."},
            headers=headers(client),
        )
        assert response.status_code == 200, response.text
        assert response.json()["message"]["content"] == "Plano de teste persistido."
        assert response.json()["usage"]["kind"] == "reported"
        cookie = client.cookies.get(COOKIE_NAME)
        csrf = headers(client)["X-Bees-CSRF"]
    with TestClient(create_app(settings), base_url=ORIGIN) as restarted:
        restarted.cookies.set(COOKIE_NAME, cookie)
        restarted.app.state.providers.transport = transport
        assert headers(restarted)["X-Bees-CSRF"] == csrf
        replay = restarted.post("/api/v1/agents", json=body, headers=headers(restarted))
        assert replay.status_code == 201
        assert replay.json()["id"] == agent["id"]
        assert len(restarted.get("/api/v1/onboarding").json()["agents"]) == 1
        history = restarted.get(
            f"/api/v1/agents/{agent['id']}/messages",
            params={"conversation_id": agent["conversation_id"]},
        ).json()
        assert [item["role"] for item in history["messages"]] == ["user", "assistant"]
        assert history["messages"][-1]["content"] == "Plano de teste persistido."
        assert len(list((settings.data_dir / "vault").glob("*.secret"))) == 1
    assert [request.url.path for request in seen] == ["/v1/models", "/v1/chat/completions"]


@pytest.mark.parametrize("change", ["model", "key", "session"])
def test_receipt_is_bound_to_configuration_key_and_session(client, environment, change) -> None:
    body = creation_body(probe(client))
    if change == "model":
        body["config"]["model"] = "another-valid-model"
    elif change == "key":
        body["api_key"] = "different-test-credential"
    else:
        response = client.post(
            "/api/v1/auth/login", json={"password": PASSWORD}, headers={"Origin": ORIGIN}
        )
        assert response.status_code == 200
    response = client.post("/api/v1/agents", json=body, headers=headers(client))
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "validation_required"
    assert client.get("/api/v1/onboarding").json()["agents"] == []
    assert list((environment[0].data_dir / "vault").glob("*.secret")) == []


def test_committed_receipt_replay_rejects_changed_name_and_key(client) -> None:
    original = creation_body(probe(client))
    first = client.post("/api/v1/agents", json=original, headers=headers(client))
    assert first.status_code == 201
    for modified in (original | {"name": "Changed"}, original | {"api_key": "changed-key"}):
        response = client.post("/api/v1/agents", json=modified, headers=headers(client))
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "command_conflict"
    assert len(client.get("/api/v1/onboarding").json()["agents"]) == 1


def test_expired_receipt_requires_new_probe(client) -> None:
    token = probe(client)
    receipt = client.app.state.receipts
    expiry, signature = receipt._items[token]
    receipt._items[token] = (expiry - 301, signature)
    response = client.post("/api/v1/agents", json=creation_body(token), headers=headers(client))
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "validation_required"


def test_unauthenticated_and_missing_csrf_never_contact_provider(client, environment) -> None:
    seen = environment[2]
    response = client.post("/api/v1/models/test", json=model_body(), headers={"Origin": ORIGIN})
    assert response.status_code == 403
    response = client.post(
        "/api/v1/agents", json=creation_body("A" * 43), headers={"Origin": ORIGIN}
    )
    assert response.status_code == 403
    client.cookies.clear()
    assert client.get("/api/v1/onboarding").status_code == 401
    assert (
        client.post(
            "/api/v1/models/test", json=model_body(), headers={"Origin": ORIGIN}
        ).status_code
        == 401
    )
    assert seen == []


def test_revocation_while_probe_runs_cannot_issue_receipt(client, monkeypatch) -> None:
    async def respond(request):
        client.app.state.identity.logout(client.cookies.get(COOKIE_NAME))
        return httpx.Response(200, json={"data": [{"id": "model-for-tests"}]})

    transport = httpx.MockTransport(respond)
    monkeypatch.setattr(
        onboarding,
        "create_adapter",
        lambda kind, resolver: create_adapter(kind, resolver, transport=transport),
    )
    response = client.post("/api/v1/models/test", json=model_body(), headers=headers(client))
    assert response.status_code == 401
    assert "validation_token" not in response.text
    assert client.app.state.receipts._items == {}


def test_database_commit_failure_rolls_back_and_removes_new_secret(
    client, environment, monkeypatch
) -> None:
    body = creation_body(probe(client))
    store = client.app.state.store
    original = store.transaction

    @contextmanager
    def fail_commit(*args, **kwargs):
        with original(*args, **kwargs) as unit:
            yield unit
            if kwargs.get("source") == "web_onboarding":
                raise StoreError("Falha controlada antes de confirmar.")

    monkeypatch.setattr(store, "transaction", fail_commit)
    response = client.post("/api/v1/agents", json=body, headers=headers(client))
    assert response.status_code == 409
    with original(write=False) as unit:
        assert unit.agents.list() == []
        assert unit.conversations.list() == []
    assert list((environment[0].data_dir / "vault").glob("*.secret")) == []


def test_diagnose_failure_never_produces_receipt_or_saves_credential(
    client, environment, monkeypatch
) -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(401, json={"error": API_KEY}))
    monkeypatch.setattr(
        onboarding,
        "create_adapter",
        lambda kind, resolver: create_adapter(kind, resolver, transport=transport),
    )
    response = client.post(
        "/api/v1/models/test", json=copy.deepcopy(model_body()), headers=headers(client)
    )
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "authentication_failed"
    assert API_KEY not in response.text
    assert client.app.state.receipts._items == {}
    assert list((environment[0].data_dir / "vault").glob("*.secret")) == []


def create_test_agent(client) -> dict:
    response = client.post(
        "/api/v1/agents", json=creation_body(probe(client)), headers=headers(client)
    )
    assert response.status_code == 201, response.text
    return response.json()


def chat_response() -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "choices": [
                {"message": {"role": "assistant", "content": "Resposta."}, "finish_reason": "stop"}
            ]
        },
    )


def test_same_conversation_rejects_concurrent_chat_before_input_or_network(client) -> None:
    agent = create_test_agent(client)
    entered = Event()
    release = Event()
    requests = []

    async def respond(request):
        requests.append(request)
        entered.set()
        assert await asyncio.to_thread(release.wait, 5), "Teste não liberou o transporte falso."
        return chat_response()

    client.app.state.providers.transport = httpx.MockTransport(respond)
    post_headers = headers(client)
    url = f"/api/v1/agents/{agent['id']}/chat"
    body = {"conversation_id": agent["conversation_id"], "content": "Primeira mensagem"}
    with ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(client.post, url, json=body, headers=post_headers)
        try:
            assert entered.wait(3), "A primeira chamada não chegou ao transporte falso."
            second = client.post(url, json=body | {"content": "Duplicada"}, headers=post_headers)
            assert second.status_code == 409
            assert second.json()["error"]["code"] == "conversation_busy"
        finally:
            release.set()
        assert first.result(timeout=5).status_code == 200
    assert len(requests) == 1
    history = client.get(
        f"/api/v1/agents/{agent['id']}/messages",
        params={"conversation_id": agent["conversation_id"]},
    ).json()["messages"]
    assert [item["role"] for item in history] == ["user", "assistant"]
    assert history[0]["content"] == "Primeira mensagem"


def test_global_two_call_limit_rejects_third_request_before_network(client, environment) -> None:
    agents = [create_test_agent(client), create_test_agent(client)]
    entered = Event()
    release = Event()
    lock = Lock()
    requests = []

    async def respond(request):
        with lock:
            requests.append(request)
            if len(requests) == 2:
                entered.set()
        assert await asyncio.to_thread(release.wait, 5), "Teste não liberou transporte."
        return chat_response()

    client.app.state.providers.transport = httpx.MockTransport(respond)
    post_headers = headers(client)
    with ThreadPoolExecutor(max_workers=2) as pool:
        running = [
            pool.submit(
                client.post,
                f"/api/v1/agents/{agent['id']}/chat",
                json={"conversation_id": agent["conversation_id"], "content": "Mensagem"},
                headers=post_headers,
            )
            for agent in agents
        ]
        try:
            assert entered.wait(3), "As duas chamadas não chegaram ao transporte."
            probes_before = len(environment[2])
            response = client.post("/api/v1/models/test", json=model_body(), headers=post_headers)
            assert response.status_code == 429
            assert response.json()["error"]["code"] == "model_busy"
            assert len(environment[2]) == probes_before
        finally:
            release.set()
        assert all(future.result(timeout=5).status_code == 200 for future in running)
    assert len(requests) == 2


def test_provider_failure_releases_conversation_slot_for_next_attempt(client) -> None:
    agent = create_test_agent(client)
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(503) if len(requests) == 1 else chat_response()

    client.app.state.providers.transport = httpx.MockTransport(respond)
    url = f"/api/v1/agents/{agent['id']}/chat"
    body = {"conversation_id": agent["conversation_id"], "content": "Mensagem"}
    first = client.post(url, json=body, headers=headers(client))
    assert first.status_code == 502
    assert first.json()["error"]["code"] == "provider_unavailable"
    second = client.post(url, json=body, headers=headers(client))
    assert second.status_code == 200
    assert len(requests) == 2
