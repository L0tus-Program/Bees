"""Remoção tem prazo total mesmo com headers lentos ou fechamento pendente."""

import asyncio
from uuid import uuid4

import httpx
import pytest

from bees_core.providers.local_models import LocalModelError, LocalModelManager, PullInput


@pytest.mark.parametrize("stage", ["headers", "close"])
@pytest.mark.parametrize("outcome", ["deadline", "cancel"])
def test_delete_releases_reservation_without_retry_after_interruption(stage, outcome):
    async def scenario():
        entered, interrupted = asyncio.Event(), asyncio.Event()
        calls = []
        slow = True

        async def stall():
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                interrupted.set()

        class Unreadable(httpx.AsyncByteStream):
            async def __aiter__(self):
                raise AssertionError("O corpo não deve ser lido.")
                yield b""  # pragma: no cover

            async def aclose(self):
                if slow and stage == "close":
                    await stall()

        async def backend(request):
            calls.append(request.method)
            if request.method == "DELETE":
                if slow and stage == "headers":
                    await stall()
                return httpx.Response(200, stream=Unreadable())
            return httpx.Response(200, content=b'{"status":"success"}\n')

        manager = LocalModelManager(
            transport=httpx.MockTransport(backend), timeout=0.1 if outcome == "deadline" else 5
        )
        operation = asyncio.create_task(manager.delete(None, "qwen3"))
        try:
            await asyncio.wait_for(entered.wait(), 2)
            with pytest.raises(LocalModelError, match="^model_in_use$"):
                manager.start(
                    PullInput(client_request_id=uuid4(), model="qwen3", confirm_download=True)
                )
            with pytest.raises(LocalModelError, match="^model_in_use$"):
                await manager.delete(None, "qwen3")
            if outcome == "cancel":
                operation.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await operation
            else:
                with pytest.raises(LocalModelError, match="^backend_unavailable$"):
                    await asyncio.wait_for(operation, 2)
            assert interrupted.is_set() and calls == ["DELETE"]
            slow = False
            await manager.delete(None, "qwen3")
            pull = manager.start(
                PullInput(client_request_id=uuid4(), model="qwen3", confirm_download=True)
            )
            await pull.task
            assert pull.status == "completed" and calls == ["DELETE", "DELETE", "POST"]
        finally:
            operation.cancel()
            await asyncio.gather(operation, return_exceptions=True)
            await manager.close()

    asyncio.run(scenario())


def test_real_http_trickling_headers_disconnect_at_total_deadline():
    async def scenario():
        disconnected = asyncio.Event()
        calls, handlers, errors = [], set(), []

        async def backend(reader, writer):
            task = asyncio.current_task()
            handlers.add(task)
            try:
                head = await reader.readuntil(b"\r\n\r\n")
                length = next(
                    int(line.split(b":", 1)[1])
                    for line in head.split(b"\r\n")
                    if line.lower().startswith(b"content-length:")
                )
                await reader.readexactly(length)
                calls.append(head.split(b"\r\n")[0])
                writer.write(b"HTTP/1.1 200 OK\r\nX-Slow: ")
                await writer.drain()
                while True:
                    try:
                        if await asyncio.wait_for(reader.read(1), 0.02) == b"":
                            disconnected.set()
                            break
                    except TimeoutError:
                        writer.write(b"x")
                        await writer.drain()
            except Exception as error:
                errors.append(error)
            finally:
                writer.close()
                await writer.wait_closed()
                handlers.discard(task)

        async with await asyncio.start_server(backend, "127.0.0.1", 0) as server:
            endpoint = f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}"
            manager = LocalModelManager(timeout=0.5)
            try:
                with pytest.raises(LocalModelError, match="^backend_unavailable$"):
                    await asyncio.wait_for(manager.delete(endpoint, "qwen3"), 3)
                await asyncio.wait_for(disconnected.wait(), 2)
                assert calls == [b"DELETE /api/delete HTTP/1.1"] and not errors
                assert not manager._deleting
            finally:
                await manager.close()
                for task in list(handlers):
                    task.cancel()
                await asyncio.gather(*handlers, return_exceptions=True)

    asyncio.run(scenario())
