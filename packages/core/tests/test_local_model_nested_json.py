"""Malformed backend JSON must leave a terminal, replayable preparation record."""

import asyncio
import json
from uuid import uuid4

import httpx
import pytest
from test_local_model_stream_bounds import Stream

from bees_core.providers.local_models import LocalModelManager, PullInput

PRIVATE = b"private-backend-detail"


def nested_json(*, objects=False, depth=2000):
    start, end = (b'{"x":', b"}") if objects else (b"[", b"]")
    return start * depth + b'"' + PRIVATE + b'"' + end * depth


@pytest.mark.parametrize("objects", [False, True])
@pytest.mark.parametrize("newline", [False, True])
def test_nested_stream_fails_closes_and_preserves_replay_until_explicit_new_request(
    monkeypatch, objects, newline
):
    async def scenario():
        calls = []
        progress = b'{"status":"pulling layer","digest":"sha256:x","total":100,"completed":40}\n'
        bad = b'{"status":"success","extra":' + nested_json(objects=objects) + b"}"
        assert len(bad) < 65536
        # Native decoder stack limits differ by OS/Python build. Inject only the
        # decoder failure here; the catalog test below uses real nested parsing.
        loads = json.loads

        def decode(value, *args, **kwargs):
            if value == bad.decode():
                raise RecursionError(PRIVATE.decode())
            return loads(value, *args, **kwargs)

        monkeypatch.setattr(json, "loads", decode)
        parts = [progress, bad + (b"\n" if newline else b"")]
        if newline:
            parts.append(b'{"status":"success"}\n')
        stream = Stream(parts)

        def backend(value):
            calls.append((value.method, value.url.path))
            if len(calls) == 1:
                return httpx.Response(200, stream=stream)
            return httpx.Response(200, content=b'{"status":"success"}\n')

        manager = LocalModelManager(transport=httpx.MockTransport(backend))
        value = PullInput(client_request_id=uuid4(), model="qwen3", confirm_download=True)
        pull = manager.start(value)
        # Multiple accepted UUIDs may share the same in-flight download.
        alias = value.model_copy(update={"client_request_id": uuid4()})
        assert manager.start(alias) is pull
        await pull.task
        assert pull.task.exception() is None
        assert stream.closed and stream.read == 2
        terminal = pull.view()
        assert terminal["status"] == "failed" and terminal["error"] == "pull_failed"
        assert terminal["finished_at"] is not None
        assert terminal["total_bytes"] == 100 and terminal["completed_bytes"] == 40
        assert PRIVATE.decode() not in str(terminal)
        for previous in (value, alias):
            assert manager.start(previous) is pull
        assert await manager.cancel(pull.id) is pull
        await manager.close()
        assert pull.view() == terminal
        assert calls == [("POST", "/api/pull")]

        fresh = manager.start(value.model_copy(update={"client_request_id": uuid4()}))
        await fresh.task
        assert fresh.id != pull.id and fresh.status == "completed"
        for previous in (value, alias):
            assert manager.start(previous) is pull
        assert pull.view() == terminal
        assert calls == [("POST", "/api/pull")] * 2
        await manager.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("path", ["/api/version", "/api/tags"])
def test_nested_catalog_is_unavailable_preserves_receipts_and_recovers_by_reading(path):
    async def scenario():
        calls = []
        corrupt = True

        def backend(value):
            calls.append((value.method, value.url.path))
            body = b'{"version":"0.40.6"}' if value.url.path == "/api/version" else b'{"models":[]}'
            if corrupt and value.url.path == path:
                body = body[:-1] + b',"extra":' + nested_json(depth=100000) + b"}"
            return httpx.Response(200, content=body)

        manager = LocalModelManager(transport=httpx.MockTransport(backend))
        value = PullInput(client_request_id=uuid4(), model="qwen3", confirm_download=True)
        pull = manager.start(value)
        await manager.cancel(pull.id)  # Preserve an existing receipt without dispatch.
        terminal = pull.view()
        for _ in range(2):
            failed = await manager.status(None)
            assert failed["reachable"] is False and failed["models"] == []
            assert failed["version"] is None and failed["suggested"]
            assert failed["pulls"] == [terminal]
            assert PRIVATE.decode() not in str(failed)
        corrupt = False
        recovered = await manager.status(None)
        assert recovered["reachable"] is True and recovered["version"] == "0.40.6"
        assert recovered["models"] == [] and recovered["pulls"] == [terminal]
        assert manager.start(value) is pull
        assert calls == [("GET", "/api/version"), ("GET", "/api/tags")] * 3
        await manager.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("objects", [False, True])
def test_nested_status_is_refused_without_stringifying_private_structure(objects):
    async def scenario():
        calls = []
        content = b'{"status":' + nested_json(objects=objects) + b'}\n{"status":"success"}\n'
        assert len(content) < 65536

        def backend(value):
            calls.append(value)
            return httpx.Response(200, content=content)

        manager = LocalModelManager(transport=httpx.MockTransport(backend))
        value = PullInput(client_request_id=uuid4(), model="qwen3", confirm_download=True)
        pull = manager.start(value)
        await pull.task
        assert pull.status == "failed" and pull.error == "pull_failed"
        assert pull.finished_at is not None
        assert PRIVATE.decode() not in str(pull.view())
        assert manager.start(value) is pull and len(calls) == 1
        await manager.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("event", [{}, {"status": None}])
def test_absent_or_null_phase_can_still_reach_valid_success(event):
    async def scenario():
        content = (json.dumps(event) + '\n{"status":"success"}\n').encode()
        manager = LocalModelManager(
            transport=httpx.MockTransport(lambda _: httpx.Response(200, content=content))
        )
        pull = manager.start(
            PullInput(client_request_id=uuid4(), model="qwen3", confirm_download=True)
        )
        await pull.task
        assert pull.status == "completed" and pull.error is None
        await manager.close()

    asyncio.run(scenario())
