import asyncio
import json

import pytest
from starlette.responses import JSONResponse

from bees_api import safety
from bees_api.safety import RequestSafetyMiddleware


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


async def drive(messages, *, headers=(), limit=16, receive_delay=0):
    sent = []
    called = []
    reads = []
    iterator = iter(messages)

    async def receive():
        reads.append(True)
        await asyncio.sleep(receive_delay)
        return next(iterator)

    async def send(message):
        sent.append(message)

    async def application(scope, receive, send):
        called.append(True)
        message = await receive()
        await JSONResponse({"length": len(message["body"])})(scope, receive, send)

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/v1/test",
        "headers": list(headers),
    }
    await RequestSafetyMiddleware(application, max_body_bytes=limit)(scope, receive, send)
    status = next(
        (message["status"] for message in sent if message["type"] == "http.response.start"), None
    )
    raw = b"".join(
        message.get("body", b"") for message in sent if message["type"] == "http.response.body"
    )
    response_headers = next(
        (dict(message["headers"]) for message in sent if message["type"] == "http.response.start"),
        {},
    )
    return status, json.loads(raw) if raw else None, response_headers, called, reads


def chunk(body, more=False):
    return {"type": "http.request", "body": body, "more_body": more}


@pytest.mark.anyio
async def test_chunked_body_is_replayed_once_at_exact_limit() -> None:
    status, body, headers, called, reads = await drive([chunk(b"a" * 8, True), chunk(b"b" * 8)])
    assert status == 200
    assert body == {"length": 16}
    assert len(reads) == 2
    assert len(called) == 1
    assert headers[b"x-content-type-options"] == b"nosniff"
    assert headers[b"x-frame-options"] == b"DENY"
    assert headers[b"referrer-policy"] == b"no-referrer"
    assert b"frame-ancestors 'none'" in headers[b"content-security-policy"]


@pytest.mark.anyio
@pytest.mark.parametrize("declared", [None, b"0", b"1"])
async def test_actual_stream_limit_applies_without_trusting_content_length(declared) -> None:
    values = [(b"content-length", declared)] if declared is not None else []
    status, body, headers, called, reads = await drive(
        [chunk(b"a" * 8, True), chunk(b"b" * 9), chunk(b"never read")], headers=values
    )
    assert status == 413
    assert body["error"]["code"] == "body_too_large"
    assert headers[b"cache-control"] == b"no-store"
    assert called == []
    assert len(reads) == 2


@pytest.mark.anyio
async def test_oversized_content_length_rejected_before_reading_any_chunk() -> None:
    status, body, _, called, reads = await drive(
        [chunk(b"small")], headers=[(b"content-length", b"17")]
    )
    assert status == 413
    assert body["error"]["code"] == "body_too_large"
    assert called == []
    assert reads == []


@pytest.mark.anyio
@pytest.mark.parametrize(
    "values",
    [
        [(b"content-length", b"-1")],
        [(b"content-length", b"nonsense")],
        [(b"content-length", b"1"), (b"content-length", b"1")],
    ],
)
async def test_invalid_or_duplicate_content_length_rejected(values) -> None:
    status, body, _, called, reads = await drive([chunk(b"a")], headers=values)
    assert status == 400
    assert body["error"]["code"] == "invalid_length"
    assert called == []
    assert reads == []


@pytest.mark.anyio
async def test_large_chunk_is_rejected_before_copying_it_into_buffer(monkeypatch) -> None:
    original = bytearray

    class LimitedBuffer(original):
        def extend(self, value):
            assert len(self) + len(value) <= 16, "Não alocar chunk que já excede limite."
            super().extend(value)

    monkeypatch.setattr(safety, "bytearray", LimitedBuffer, raising=False)
    status, _, _, called, _ = await drive([chunk(b"x" * 100000)])
    assert status == 413
    assert called == []


@pytest.mark.anyio
async def test_slow_request_timeout_never_runs_application(monkeypatch) -> None:
    real_timeout = asyncio.timeout
    monkeypatch.setattr(safety.asyncio, "timeout", lambda _: real_timeout(0.01))
    status, body, _, called, _ = await drive([chunk(b"a")], receive_delay=1)
    assert status == 408
    assert body["error"]["code"] == "body_timeout"
    assert called == []


@pytest.mark.anyio
async def test_disconnect_does_not_dispatch_partial_request() -> None:
    status, body, _, called, reads = await drive([chunk(b"a", True), {"type": "http.disconnect"}])
    assert status is None
    assert body is None
    assert called == []
    assert len(reads) == 2
