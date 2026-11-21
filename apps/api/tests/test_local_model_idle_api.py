"""Timeout do preparo produz recibo consultável, sem repetição de efeitos."""

import asyncio
from uuid import uuid4

import httpx
import pytest
from test_local_models_api import headers, lines, wait
from test_local_models_api import state as state

from bees_core.providers.local_models import LocalModelManager


@pytest.mark.parametrize("stage", ["headers", "progress", "error"])
def test_idle_failure_is_sanitized_replayable_and_allows_explicit_recovery(state, stage):
    client, _ = state
    calls = []

    class TimedOut(httpx.AsyncByteStream):
        closed = False

        async def __aiter__(self):
            if stage == "progress":
                yield lines(
                    {"status": "pulling x", "digest": "sha256:x", "total": 100, "completed": 40}
                )
            else:
                yield b"private-incomplete-error"
            raise httpx.ReadTimeout("private-backend-detail")

        async def aclose(self):
            self.closed = True

    stream = TimedOut()

    def backend(request):
        calls.append(request)
        assert request.extensions["timeout"]["read"] == 120
        if len(calls) == 1:
            if stage == "headers":
                raise httpx.ReadTimeout("private-backend-detail")
            return httpx.Response(500 if stage == "error" else 200, stream=stream)
        return httpx.Response(200, content=lines({"status": "success"}))

    client.app.state.local_models = LocalModelManager(transport=httpx.MockTransport(backend))
    value = {"client_request_id": str(uuid4()), "model": "qwen3", "confirm_download": True}
    auth = headers(client)
    path = "/api/v1/local-models/pulls"
    accepted = client.post(path, json=value, headers=auth)
    assert accepted.status_code == 202
    terminal = wait(client, accepted.json()["id"])
    assert terminal["status"] == "failed" and terminal["error"] == "pull_failed"
    assert terminal["finished_at"] is not None
    assert terminal["completed_bytes"] == (40 if stage == "progress" else 0)
    assert stream.closed or stage == "headers"
    assert "private" not in str(terminal)
    assert client.get(f"{path}/{terminal['id']}").json() == terminal
    assert client.post(path, json=value, headers=auth).json() == terminal
    assert client.post(f"{path}/{terminal['id']}/cancel", json={}, headers=auth).json() == terminal
    assert len(calls) == 1
    fresh = client.post(path, json=value | {"client_request_id": str(uuid4())}, headers=auth)
    assert fresh.status_code == 202 and fresh.json()["id"] != terminal["id"]
    assert wait(client, fresh.json()["id"])["status"] == "completed"
    assert client.post(path, json=value, headers=auth).json() == terminal
    assert len(calls) == 2


@pytest.mark.parametrize("stage", ["fragment", "blank", "error"])
def test_record_deadline_publishes_terminal_receipt_without_retry(state, stage):
    client, _ = state
    calls = []

    class Slow(httpx.AsyncByteStream):
        closed = False
        fragments = 0

        async def __aiter__(self):
            if stage != "error":
                yield lines(
                    {"status": "pulling x", "digest": "sha256:x", "total": 100, "completed": 40}
                )
            for _ in range(30):
                await asyncio.sleep(0.02)
                self.fragments += 1
                yield b"\r\n" if stage == "blank" else b" "
            yield lines({"status": "success"})

        async def aclose(self):
            self.closed = True

    stream = Slow()

    def backend(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(500 if stage == "error" else 200, stream=stream)
        return httpx.Response(200, content=lines({"status": "success"}))

    client.app.state.local_models = LocalModelManager(
        transport=httpx.MockTransport(backend), pull_idle_timeout=0.15
    )
    value = {"client_request_id": str(uuid4()), "model": "qwen3", "confirm_download": True}
    auth = headers(client)
    path = "/api/v1/local-models/pulls"
    accepted = client.post(path, json=value, headers=auth)
    assert accepted.status_code == 202
    terminal = wait(client, accepted.json()["id"])
    assert terminal["status"] == "failed" and terminal["error"] == "pull_failed"
    assert terminal["completed_bytes"] == (0 if stage == "error" else 40)
    assert terminal["finished_at"] is not None
    assert stream.closed and stream.fragments < 30
    assert client.get(f"{path}/{terminal['id']}").json() == terminal
    receipt = client.get(f"/api/v1/local-models/pull-requests/{value['client_request_id']}")
    assert receipt.json() == {"client_request_id": value["client_request_id"], "pull": terminal}
    assert client.post(path, json=value, headers=auth).json() == terminal
    assert client.post(f"{path}/{terminal['id']}/cancel", json={}, headers=auth).json() == terminal
    assert len(calls) == 1
    fresh = client.post(path, json=value | {"client_request_id": str(uuid4())}, headers=auth)
    assert fresh.status_code == 202
    assert wait(client, fresh.json()["id"])["status"] == "completed"
    assert client.post(path, json=value, headers=auth).json() == terminal
    assert len(calls) == 2


@pytest.mark.parametrize("cancel", [False, True])
def test_header_budget_keeps_sanitized_receipt_and_human_recovery(state, cancel):
    client, _ = state
    calls = []
    entered, interrupted = asyncio.Event(), asyncio.Event()

    async def backend(request):
        calls.append(request)
        if len(calls) == 1:
            entered.set()
            try:
                await asyncio.sleep(0.5)
            except asyncio.CancelledError:
                interrupted.set()
                raise
        return httpx.Response(200, content=lines({"status": "success"}))

    client.app.state.local_models = LocalModelManager(
        transport=httpx.MockTransport(backend), pull_idle_timeout=0.15
    )
    value = {"client_request_id": str(uuid4()), "model": "qwen3", "confirm_download": True}
    auth = headers(client)
    path = "/api/v1/local-models/pulls"
    accepted = client.post(path, json=value, headers=auth)
    assert accepted.status_code == 202
    client.portal.call(entered.wait)
    alias = value | {"client_request_id": str(uuid4())}
    assert client.post(path, json=alias, headers=auth).json()["id"] == accepted.json()["id"]
    blocked = client.post(
        "/api/v1/local-models/delete",
        json={"model": "qwen3", "confirm_delete": True},
        headers=auth,
    )
    assert blocked.status_code == 409 and blocked.json()["error"]["code"] == "model_in_use"
    if cancel:
        terminal = client.post(
            f"{path}/{accepted.json()['id']}/cancel", json={}, headers=auth
        ).json()
    else:
        terminal = wait(client, accepted.json()["id"])
    assert terminal["status"] == ("cancelled" if cancel else "failed")
    assert terminal["error"] == (None if cancel else "pull_failed")
    assert terminal["finished_at"] is not None and terminal["completed_bytes"] == 0
    assert interrupted.is_set()
    assert client.get(f"{path}/{terminal['id']}").json() == terminal
    for prior in (value, alias):
        receipt = client.get(f"/api/v1/local-models/pull-requests/{prior['client_request_id']}")
        assert receipt.json() == {"client_request_id": prior["client_request_id"], "pull": terminal}
        assert client.post(path, json=prior, headers=auth).json() == terminal
    assert client.post(f"{path}/{terminal['id']}/cancel", json={}, headers=auth).json() == terminal
    assert len(calls) == 1
    fresh = client.post(path, json=value | {"client_request_id": str(uuid4())}, headers=auth)
    assert wait(client, fresh.json()["id"])["status"] == "completed"
    assert len(calls) == 2
    assert client.post(path, json=value, headers=auth).json() == terminal
