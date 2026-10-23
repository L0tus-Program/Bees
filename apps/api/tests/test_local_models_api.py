"""Preparo de modelo local pela interface: confirmação, progresso, falhas e cancelamento."""

import asyncio
import json
import time
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from bees_api.app import create_app
from bees_api.config import Settings
from bees_core.providers.local_models import LocalModelManager

ORIGIN = "http://127.0.0.1:8000"
PASSWORD = "senha teste modelo local 123456"


def headers(client):
    return {"Origin": ORIGIN, "X-Bees-CSRF": client.get("/api/v1/auth/status").json()["csrf_token"]}


def lines(*events):
    return "".join(json.dumps(event) + "\n" for event in events).encode()


class Ollama:
    def __init__(self):
        self.models = [{"name": "qwen3:1.7b", "size": 1_400_000_000, "modified_at": "2026-10-01"}]
        self.pulls = []
        self.deleted = []
        self.pull_response = None

    def __call__(self, request: httpx.Request):
        assert request.url.host == "127.0.0.1"
        if request.url.path == "/api/version":
            return httpx.Response(200, json={"version": "0.40.6"})
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": self.models})
        if request.url.path == "/api/delete":
            self.deleted.append(json.loads(request.content)["model"])
            return httpx.Response(200)
        if request.url.path == "/api/pull":
            body = json.loads(request.content)
            self.pulls.append(body["model"])
            return self.pull_response(body)
        return httpx.Response(404)


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
        backend = Ollama()
        client.app.state.local_models = LocalModelManager(transport=httpx.MockTransport(backend))
        yield client, backend


def wait(client, pull_id, status="running"):
    for _ in range(200):
        view = client.get(f"/api/v1/local-models/pulls/{pull_id}").json()
        if view["status"] != status:
            return view
        time.sleep(0.01)
    raise AssertionError(view)


def test_status_lists_installed_and_suggestions_and_requires_session(state):
    client, _ = state
    body = client.get("/api/v1/local-models").json()
    assert body["reachable"] and body["version"] == "0.40.6"
    assert body["endpoint"] == "http://127.0.0.1:11434"
    assert body["models"][0]["name"] == "qwen3:1.7b"
    assert all(item["download_bytes"] > 0 and item["source"] for item in body["suggested"])
    remote = client.get("/api/v1/local-models", params={"endpoint": "https://example.org"})
    assert remote.status_code == 503
    assert client.post("/api/v1/auth/logout", json={}, headers=headers(client)).status_code == 204
    assert client.get("/api/v1/local-models").status_code == 401


def test_pull_requires_confirmation_and_reports_progress(state):
    client, backend = state
    backend.pull_response = lambda body: httpx.Response(
        200,
        content=lines(
            {"status": "pulling manifest"},
            {"status": "pulling abc", "digest": "sha256:abc", "total": 100, "completed": 40},
            {"status": "pulling abc", "digest": "sha256:abc", "total": 100, "completed": 100},
            {"status": "verifying sha256 digest"},
            {"status": "writing manifest"},
            {"status": "success"},
        ),
    )
    request = {"client_request_id": str(uuid4()), "model": "llama3.2:3b"}
    refused = client.post("/api/v1/local-models/pulls", json=request, headers=headers(client))
    assert refused.status_code == 422 and backend.pulls == []
    started = client.post(
        "/api/v1/local-models/pulls",
        json=request | {"confirm_download": True},
        headers=headers(client),
    )
    assert started.status_code == 202
    view = wait(client, started.json()["id"])
    assert view["status"] == "completed" and view["completed_bytes"] == view["total_bytes"] == 100
    # Replay do mesmo pedido devolve o mesmo download, sem baixar de novo.
    replay = client.post(
        "/api/v1/local-models/pulls",
        json=request | {"confirm_download": True},
        headers=headers(client),
    )
    assert replay.json()["id"] == started.json()["id"] and backend.pulls == ["llama3.2:3b"]
    conflict = client.post(
        "/api/v1/local-models/pulls",
        json=request | {"confirm_download": True, "model": "qwen3:4b"},
        headers=headers(client),
    )
    assert conflict.status_code == 409


@pytest.mark.parametrize(
    ("response", "code"),
    [
        (
            lambda body: httpx.Response(
                200,
                content=lines(
                    {"status": "pulling manifest"},
                    {
                        "error": 'pull model manifest: Get "https://registry.ollama.ai/v2/x": '
                        "dial tcp: lookup registry.ollama.ai: no such host"
                    },
                ),
            ),
            "registry_unreachable",
        ),
        (
            lambda body: httpx.Response(200, content=lines({"error": "no space left on device"})),
            "insufficient_storage",
        ),
        (
            lambda body: httpx.Response(
                200, content=lines({"error": "pull model manifest: file does not exist"})
            ),
            "model_not_found",
        ),
        (
            lambda body: httpx.Response(200, content=lines({"status": "pulling manifest"})),
            "pull_failed",
        ),
    ],
)
def test_pull_failures_are_classified(state, response, code):
    client, backend = state
    backend.pull_response = response
    started = client.post(
        "/api/v1/local-models/pulls",
        json={"client_request_id": str(uuid4()), "model": "qwen3:8b", "confirm_download": True},
        headers=headers(client),
    )
    view = wait(client, started.json()["id"])
    assert view["status"] == "failed" and view["error"] == code


def test_backend_down_is_reported(state):
    client, _ = state

    def down(request):
        raise httpx.ConnectError("refused", request=request)

    client.app.state.local_models = LocalModelManager(transport=httpx.MockTransport(down))
    assert client.get("/api/v1/local-models").json()["reachable"] is False
    started = client.post(
        "/api/v1/local-models/pulls",
        json={"client_request_id": str(uuid4()), "model": "qwen3:4b", "confirm_download": True},
        headers=headers(client),
    )
    assert wait(client, started.json()["id"])["error"] == "backend_unavailable"


def test_cancel_running_pull_and_delete_requires_confirmation(state):
    client, backend = state

    async def slow():
        yield lines({"status": "pulling abc", "digest": "sha256:abc", "total": 10, "completed": 1})
        await asyncio.sleep(3600)

    backend.pull_response = lambda body: httpx.Response(200, content=slow())
    started = client.post(
        "/api/v1/local-models/pulls",
        json={"client_request_id": str(uuid4()), "model": "qwen3:4b", "confirm_download": True},
        headers=headers(client),
    ).json()
    for _ in range(100):
        if client.get(f"/api/v1/local-models/pulls/{started['id']}").json()["total_bytes"]:
            break
        time.sleep(0.01)
    busy = client.post(
        "/api/v1/local-models/delete",
        json={"model": "qwen3:4b", "confirm_delete": True},
        headers=headers(client),
    )
    assert busy.status_code == 409
    cancelled = client.post(
        f"/api/v1/local-models/pulls/{started['id']}/cancel", json={}, headers=headers(client)
    ).json()
    assert cancelled["status"] == "cancelled" and cancelled["completed_bytes"] == 1
    assert (
        client.post(
            "/api/v1/local-models/delete", json={"model": "qwen3:1.7b"}, headers=headers(client)
        ).status_code
        == 422
    )
    deleted = client.post(
        "/api/v1/local-models/delete",
        json={"model": "qwen3:1.7b", "confirm_delete": True},
        headers=headers(client),
    )
    assert deleted.status_code == 200 and backend.deleted == ["qwen3:1.7b"]
    invalid = client.post(
        "/api/v1/local-models/pulls",
        json={"client_request_id": str(uuid4()), "model": "x:cloud", "confirm_download": True},
        headers=headers(client),
    )
    assert invalid.status_code == 422
