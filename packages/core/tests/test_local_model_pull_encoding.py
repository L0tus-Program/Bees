"""Preparo recusa compressão antes de consumir ou descomprimir o corpo."""

import asyncio
import gzip
from uuid import uuid4

import httpx
import pytest
from test_local_model_stream_bounds import Stream

from bees_core.providers.local_models import LocalModelManager, PullInput


@pytest.mark.parametrize("status", [200, 404, 500])
@pytest.mark.parametrize("encoding", ["gzip", "GZip", "deflate", "br", "identity, gzip", "unknown"])
def test_compressed_pull_closes_without_reading_and_preserves_replay(status, encoding):
    async def scenario():
        calls = []
        stream = Stream([gzip.compress(b'{"status":"success"}\n' + b" " * 100000)])

        def backend(request):
            calls.append(request)
            if len(calls) == 1:
                return httpx.Response(status, headers={"Content-Encoding": encoding}, stream=stream)
            return httpx.Response(200, content=b'{"status":"success"}\n')

        manager = LocalModelManager(transport=httpx.MockTransport(backend))
        value = PullInput(client_request_id=uuid4(), model="qwen3", confirm_download=True)
        pull = manager.start(value)
        # Dois UUIDs podem compartilhar o mesmo download em andamento.
        shared = value.model_copy(update={"client_request_id": uuid4()})
        assert manager.start(shared) is pull
        await pull.task
        assert pull.status == "failed" and pull.error == "pull_failed"
        assert stream.closed and stream.read == 0
        receipt = pull.view()
        assert receipt["finished_at"] is not None
        for request in (value, shared):
            assert manager.start(request).view() == receipt
        await manager.cancel(pull.id)
        await manager.close()
        assert pull.view() == receipt and len(calls) == 1
        recovered = manager.start(value.model_copy(update={"client_request_id": uuid4()}))
        await recovered.task
        assert recovered.status == "completed" and len(calls) == 2

    asyncio.run(scenario())


@pytest.mark.parametrize("encoding", [None, "identity", "IDENTITY"])
@pytest.mark.parametrize(
    ("status", "body", "expected"),
    [
        (200, b'{"status":"success"}\n', "completed"),
        (404, b"not found", "model_not_found"),
        (500, b"no space left", "insufficient_storage"),
    ],
)
def test_pull_requests_identity_and_preserves_uncompressed_contract(
    status, body, expected, encoding
):
    async def scenario():
        stream = Stream([body])

        def backend(request):
            assert request.headers["accept-encoding"] == "identity"
            return httpx.Response(
                status,
                headers={} if encoding is None else {"Content-Encoding": encoding},
                stream=stream,
            )

        manager = LocalModelManager(transport=httpx.MockTransport(backend))
        pull = manager.start(
            PullInput(client_request_id=uuid4(), model="qwen3", confirm_download=True)
        )
        await pull.task
        assert stream.closed and stream.read == 1
        assert (pull.status if status == 200 else pull.error) == expected
        await manager.close()

    asyncio.run(scenario())
