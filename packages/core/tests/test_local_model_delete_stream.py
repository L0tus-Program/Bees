"""Remoção depende do status HTTP, sem aguardar corpo privado ou ilimitado."""

import asyncio
import json
from uuid import uuid4

import httpx
import pytest

from bees_core.providers.local_models import LocalModelError, LocalModelManager, PullInput


class Unreadable(httpx.AsyncByteStream):
    def __init__(self):
        self.reads = 0
        self.closed = False

    async def __aiter__(self):
        self.reads += 1
        raise AssertionError("O corpo da remoção não deve ser lido.")
        yield b""  # pragma: no cover

    async def aclose(self):
        self.closed = True


def pull_input():
    return PullInput(client_request_id=uuid4(), model="qwen3", confirm_download=True)


@pytest.mark.parametrize("status", [200, 201, 204, 301, 302, 307, 308, 400, 404, 500])
@pytest.mark.parametrize(
    "headers",
    [{}, {"Content-Length": "999999999999"}, {"Content-Encoding": "gzip"}],
)
def test_delete_closes_response_without_reading_body_and_releases_target(status, headers):
    async def scenario():
        stream = Unreadable()
        calls = []

        def backend(request):
            calls.append((request.method, request.url.path))
            if request.method == "DELETE":
                return httpx.Response(
                    status,
                    headers=headers | {"Location": "http://127.0.0.1:11435/other"},
                    stream=stream,
                )
            return httpx.Response(200, content=b'{"status":"success"}\n')

        manager = LocalModelManager(transport=httpx.MockTransport(backend))
        try:
            if status < 300:
                await manager.delete(None, "qwen3")
            else:
                error = "model_not_found" if status == 404 else "pull_failed"
                with pytest.raises(LocalModelError, match=f"^{error}$"):
                    await manager.delete(None, "qwen3")
            assert stream.closed and stream.reads == 0
            assert calls == [("DELETE", "/api/delete")]
            pull = manager.start(pull_input())
            await pull.task
            assert pull.status == "completed"
            assert calls == [("DELETE", "/api/delete"), ("POST", "/api/pull")]
        finally:
            await manager.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("status", [200, 404, 500])
def test_delete_real_http_returns_without_waiting_for_body(status):
    async def scenario():
        disconnected = asyncio.Event()
        calls = []
        handlers = []

        async def backend(reader, writer):
            handlers.append(asyncio.current_task())
            try:
                head = await reader.readuntil(b"\r\n\r\n")
                length = next(
                    int(line.split(b":", 1)[1])
                    for line in head.split(b"\r\n")
                    if line.lower().startswith(b"content-length:")
                )
                body = await reader.readexactly(length)
                calls.append((head.split(b"\r\n")[0], json.loads(body)))
                # O backend anuncia um corpo enorme e não o envia: só a conexão
                # fechada pelo cliente permite que este handler termine.
                writer.write(
                    f"HTTP/1.1 {status} Result\r\nContent-Length: 999999999999\r\n\r\n".encode()
                )
                await writer.drain()
                assert await reader.read() == b""
                disconnected.set()
            finally:
                writer.close()
                await writer.wait_closed()

        async with await asyncio.start_server(backend, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            manager = LocalModelManager(timeout=2)
            try:
                operation = manager.delete(f"http://127.0.0.1:{port}", "qwen3")
                if status == 200:
                    await asyncio.wait_for(operation, 5)
                else:
                    code = "model_not_found" if status == 404 else "pull_failed"
                    with pytest.raises(LocalModelError, match=f"^{code}$"):
                        await asyncio.wait_for(operation, 5)
                await asyncio.wait_for(disconnected.wait(), 5)
                await asyncio.wait_for(asyncio.gather(*handlers), 5)
                assert calls == [(b"DELETE /api/delete HTTP/1.1", {"model": "qwen3:latest"})]
            finally:
                await manager.close()
                for task in handlers:
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*handlers, return_exceptions=True)

    asyncio.run(scenario())


@pytest.mark.parametrize("outcome", ["success", "transport_error", "cancelled"])
def test_delete_reserves_target_until_response_close_finishes(outcome):
    async def scenario():
        closing, release = asyncio.Event(), asyncio.Event()
        calls = []

        class Closing(Unreadable):
            async def aclose(self):
                closing.set()
                try:
                    await release.wait()
                    if outcome == "transport_error":
                        raise httpx.ReadError("private transport detail")
                finally:
                    self.closed = True

        stream = Closing()

        def backend(request):
            calls.append(request.method)
            if request.method == "DELETE":
                return httpx.Response(200, stream=stream)
            return httpx.Response(200, content=b'{"status":"success"}\n')

        manager = LocalModelManager(transport=httpx.MockTransport(backend))
        deleting = asyncio.create_task(manager.delete(None, "qwen3"))
        try:
            await asyncio.wait_for(closing.wait(), 2)
            with pytest.raises(LocalModelError, match="^model_in_use$"):
                manager.start(pull_input())
            with pytest.raises(LocalModelError, match="^model_in_use$"):
                await manager.delete(None, "qwen3")
            assert calls == ["DELETE"]
            if outcome == "cancelled":
                deleting.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await deleting
            else:
                release.set()
                if outcome == "transport_error":
                    with pytest.raises(LocalModelError, match="^backend_unavailable$"):
                        await deleting
                else:
                    await deleting
            assert stream.closed and stream.reads == 0
            pull = manager.start(pull_input())
            await pull.task
            assert pull.status == "completed" and calls == ["DELETE", "POST"]
        finally:
            release.set()
            if not deleting.done():
                deleting.cancel()
            await asyncio.gather(deleting, return_exceptions=True)
            await manager.close()

    asyncio.run(scenario())
