"""Timeout do preparo produz recibo consultável, sem repetição de efeitos."""

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
