"""Preparo de modelo local pela interface: confirmação, progresso, falhas e cancelamento."""

import asyncio
import json
import time
from concurrent.futures import ThreadPoolExecutor
from threading import Event
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


def wait(client, pull_id, status="running", deadline=15.0):
    # Prazo generoso: runners de CI podem atrasar a tarefa de download em segundo plano.
    end = time.monotonic() + deadline
    while True:
        view = client.get(f"/api/v1/local-models/pulls/{pull_id}").json()
        if view["status"] != status:
            return view
        if time.monotonic() > end:
            raise AssertionError(view)
        time.sleep(0.02)


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


def test_invalid_catalog_is_not_reported_as_empty_available_backend(state):
    client, backend = state
    backend.models = [{"name": "qwen3:1.7b"}, {"size": 100}]
    failed = client.get("/api/v1/local-models")
    assert failed.status_code == 200
    assert failed.json()["reachable"] is False and failed.json()["models"] == []
    assert failed.json()["suggested"]
    backend.models = []
    recovered = client.get("/api/v1/local-models").json()
    assert recovered["reachable"] is True and recovered["models"] == []
    assert backend.pulls == backend.deleted == []


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
    end = time.monotonic() + 15
    while not client.get(f"/api/v1/local-models/pulls/{started['id']}").json()["total_bytes"]:
        assert time.monotonic() < end
        time.sleep(0.02)
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


def test_shared_pull_replay_after_cancellation_keeps_id_and_rejects_conflicts(state):
    client, backend = state

    async def slow():
        yield lines({"status": "pulling manifest"})
        await asyncio.sleep(3600)

    backend.pull_response = lambda _: httpx.Response(200, content=slow())
    original = {"client_request_id": str(uuid4()), "model": "qwen3", "confirm_download": True}
    alias = original | {"client_request_id": str(uuid4()), "model": "qwen3:latest"}

    def start(value):
        return client.post("/api/v1/local-models/pulls", json=value, headers=headers(client))

    started = start(original)
    assert started.status_code == 202
    shared = start(alias)
    assert shared.status_code == 202 and shared.json()["id"] == started.json()["id"]
    cancelled = client.post(
        f"/api/v1/local-models/pulls/{started.json()['id']}/cancel",
        json={},
        headers=headers(client),
    )
    assert cancelled.status_code == 200 and cancelled.json()["status"] == "cancelled"
    assert backend.pulls == ["qwen3:latest"]
    for value in (original, alias):
        replay = start(value)
        assert replay.status_code == 202 and replay.json() == cancelled.json()
        for changes in ({"model": "qwen3:4b"}, {"endpoint": "http://127.0.0.1:11435"}):
            conflict = start(value | changes)
            assert (
                conflict.status_code == 409 and conflict.json()["error"]["code"] == "pull_conflict"
            )
    assert backend.pulls == ["qwen3:latest"]


def test_delete_in_flight_refuses_concurrent_pull_and_delete(state):
    client, _ = state
    entered = Event()
    release = asyncio.Event()
    calls = []

    async def backend(value):
        calls.append(value)
        if value.method == "DELETE":
            entered.set()
            await asyncio.wait_for(release.wait(), 15)
        return httpx.Response(200, content=lines({"status": "success"}))

    client.app.state.local_models = LocalModelManager(transport=httpx.MockTransport(backend))
    auth = headers(client)
    delete = {"model": "qwen3", "confirm_delete": True}
    pull = {"model": "qwen3:latest", "client_request_id": str(uuid4()), "confirm_download": True}
    with ThreadPoolExecutor(max_workers=1) as executor:
        deleting = executor.submit(
            client.post, "/api/v1/local-models/delete", json=delete, headers=auth
        )
        try:
            assert entered.wait(15)
            for path, body in (("pulls", pull), ("delete", delete)):
                refused = client.post(f"/api/v1/local-models/{path}", json=body, headers=auth)
                assert refused.status_code == 409
                assert refused.json()["error"]["code"] == "model_in_use"
            assert len(calls) == 1
        finally:
            client.portal.call(release.set)
        assert deleting.result(timeout=15).status_code == 200
    started = client.post("/api/v1/local-models/pulls", json=pull, headers=auth)
    assert started.status_code == 202
    assert wait(client, started.json()["id"])["status"] == "completed"
    assert [call.method for call in calls] == ["DELETE", "POST"]
