"""Limites precisam interromper a leitura, não apenas cortar depois do EOF."""

import asyncio
import json
from uuid import uuid4

import httpx
import pytest

from bees_core.providers.local_models import LocalModelManager, PullInput


class Stream(httpx.AsyncByteStream):
    def __init__(self, parts):
        self.parts, self.read, self.closed = parts, 0, False

    async def __aiter__(self):
        for part in self.parts:
            self.read += 1
            yield part

    async def aclose(self):
        self.closed = True


def run_stream(stream, status=200):
    async def scenario():
        manager = LocalModelManager(
            transport=httpx.MockTransport(lambda _: httpx.Response(status, stream=stream))
        )
        pull = manager.start(
            PullInput(client_request_id=uuid4(), model="qwen3", confirm_download=True)
        )
        await pull.task
        assert stream.closed
        return pull

    return asyncio.run(scenario())


def test_line_without_newline_is_stopped_before_eof():
    stream = Stream([b"x" * 16384] * 20)
    pull = run_stream(stream)
    assert pull.status == "failed" and pull.error == "pull_failed"
    assert stream.read < len(stream.parts)


def test_http_error_reads_only_diagnostic_prefix():
    stream = Stream([b"no space left " + b"x" * 1010] + [b"x" * 1024] * 100)
    pull = run_stream(stream, 500)
    assert pull.status == "failed" and pull.error == "insufficient_storage"
    assert stream.read <= 4


@pytest.mark.parametrize("ending", [b"", b"\n", b"\r\n"])
def test_fragmented_utf8_and_last_line_are_preserved(ending):
    data = (
        json.dumps({"status": "pulling camada ação"}, ensure_ascii=False).encode()
        + b'\r\n\n{"status":"success"}'
        + ending
    )
    stream = Stream([data[index : index + 2] for index in range(0, len(data), 2)])
    assert run_stream(stream).status == "completed"


def test_line_exactly_at_byte_limit_is_accepted():
    prefix = b'{"status":"success","padding":"'
    suffix = b'"}'
    data = prefix + b"x" * (65536 - len(prefix) - len(suffix)) + suffix
    assert run_stream(Stream([data, b"\n"])).status == "completed"
