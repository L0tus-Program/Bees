"""Diagnóstico local interrompe corpos excessivos antes de acumular ou descomprimir."""

import asyncio
import gzip
from uuid import uuid4

import httpx
import pytest
from test_local_model_stream_bounds import Stream

from bees_core.providers.local_models import (
    MAX_CATALOG_BYTES,
    MAX_VERSION_BYTES,
    LocalModelManager,
    PullInput,
)

CASES = [("/api/version", MAX_VERSION_BYTES), ("/api/tags", MAX_CATALOG_BYTES)]


def valid_body(path):
    return b'{"version":"0.40.6"}' if path == "/api/version" else b'{"models":[]}'


async def query_and_recover(path, stream, *, headers=None, status=200):
    calls = []
    corrupt = True

    def backend(request):
        calls.append((request.method, request.url.path))
        assert request.headers["accept-encoding"] == "identity"
        if corrupt and request.url.path == path:
            return httpx.Response(status, headers=headers, stream=stream)
        return httpx.Response(200, content=valid_body(request.url.path))

    manager = LocalModelManager(transport=httpx.MockTransport(backend))
    value = PullInput(client_request_id=uuid4(), model="qwen3", confirm_download=True)
    pull = manager.start(value)
    await manager.cancel(pull.id)
    receipt = pull.view()
    failed = await manager.status(None)
    assert failed["reachable"] is False
    assert failed["version"] is None and failed["models"] == []
    assert failed["pulls"] == [receipt] and failed["suggested"]
    assert stream.closed and manager.start(value) is pull
    assert calls == [("GET", "/api/version")] + (
        [("GET", "/api/tags")] if path == "/api/tags" else []
    )
    corrupt = False
    recovered = await manager.status(None)
    assert recovered["reachable"] is True and recovered["version"] == "0.40.6"
    assert recovered["models"] == [] and recovered["pulls"] == [receipt]
    assert calls[-2:] == [("GET", "/api/version"), ("GET", "/api/tags")]
    assert len(calls) == (4 if path == "/api/tags" else 3)
    await manager.close()


@pytest.mark.parametrize(("path", "limit"), CASES)
@pytest.mark.parametrize("length", [None, "1"])
def test_oversized_stream_stops_even_without_trusted_length(path, limit, length):
    # JSON válido seguido de espaços: seria aceito caso o limite viesse só após parsing.
    stream = Stream([valid_body(path)] + [b" " * 16384] * (limit // 16384 + 10))
    headers = {} if length is None else {"Content-Length": length}
    asyncio.run(query_and_recover(path, stream, headers=headers))
    assert stream.read < len(stream.parts)


@pytest.mark.parametrize(("path", "limit"), CASES)
@pytest.mark.parametrize("length", ["excessive", "-1", "not-a-number"])
def test_invalid_or_excessive_length_is_refused_without_reading(path, limit, length):
    stream = Stream([b"private-backend-detail"])
    headers = {"Content-Length": str(limit + 1) if length == "excessive" else length}
    asyncio.run(query_and_recover(path, stream, headers=headers))
    assert stream.read == 0


@pytest.mark.parametrize(("path", "limit"), CASES)
@pytest.mark.parametrize("length", [False, True])
def test_exact_byte_limit_is_accepted_with_fragmentation(path, limit, length):
    async def scenario():
        body = valid_body(path)
        body += b" " * (limit - len(body))
        stream = Stream([body[i : i + 8191] for i in range(0, len(body), 8191)])

        def backend(request):
            if request.url.path == path:
                return httpx.Response(
                    200, headers={"Content-Length": str(limit)} if length else {}, stream=stream
                )
            return httpx.Response(200, content=valid_body(request.url.path))

        view = await LocalModelManager(transport=httpx.MockTransport(backend)).status(None)
        assert view["reachable"] is True and view["version"] == "0.40.6"
        assert view["models"] == [] and stream.closed and stream.read == len(stream.parts)

    asyncio.run(scenario())


@pytest.mark.parametrize(("path", "limit"), CASES)
def test_compressed_response_is_refused_before_decompression(path, limit):
    body = valid_body(path) + b" " * (limit + 1)
    stream = Stream([gzip.compress(body)])
    asyncio.run(query_and_recover(path, stream, headers={"Content-Encoding": "gzip"}))
    assert stream.read == 0


@pytest.mark.parametrize(("path", "limit"), CASES)
@pytest.mark.parametrize("status", [302, 404, 500])
def test_http_rejection_closes_without_reading_or_following(path, limit, status):
    stream = Stream([b"private-backend-detail"] * 10)
    asyncio.run(
        query_and_recover(
            path, stream, headers={"Location": "http://127.0.0.1:11435/other"}, status=status
        )
    )
    assert stream.read == 0


@pytest.mark.parametrize(("path", "limit"), CASES)
def test_preconsumed_body_without_length_cannot_bypass_limit(path, limit):
    async def scenario():
        def backend(request):
            body = valid_body(request.url.path)
            if request.url.path == path:
                body += b" " * limit
            response = httpx.Response(200, content=body)
            del response.headers["content-length"]
            return response

        view = await LocalModelManager(transport=httpx.MockTransport(backend)).status(None)
        assert view["reachable"] is False and view["models"] == []

    asyncio.run(scenario())


@pytest.mark.parametrize(("path", "limit"), CASES)
def test_cancellation_propagates_and_closes_open_response(path, limit):
    async def scenario():
        started = asyncio.Event()
        closed = asyncio.Event()
        calls = []

        class Pending(httpx.AsyncByteStream):
            async def __aiter__(self):
                started.set()
                await asyncio.Event().wait()
                yield b""

            async def aclose(self):
                closed.set()

        def backend(request):
            calls.append(request.url.path)
            if request.url.path == path:
                return httpx.Response(200, stream=Pending())
            return httpx.Response(200, content=valid_body(request.url.path))

        manager = LocalModelManager(transport=httpx.MockTransport(backend))
        query = asyncio.create_task(manager.status(None))
        await asyncio.wait_for(started.wait(), timeout=2)
        query.cancel()
        with pytest.raises(asyncio.CancelledError):
            await query
        assert closed.is_set()
        assert calls == (["/api/version"] if path == "/api/version" else ["/api/version", path])
        assert manager._pulls == {} and manager._requests == {}

    asyncio.run(scenario())
