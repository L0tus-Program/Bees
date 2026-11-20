"""Diagnóstico tem prazo compartilhado, mesmo com bytes chegando continuamente."""

import asyncio
from uuid import uuid4

import httpx
import pytest

from bees_core.providers.local_models import LocalModelManager, PullInput


def body(path):
    return b'{"version":"test"}' if path == "/api/version" else b'{"models":[]}'


@pytest.mark.parametrize("path", ["/api/version", "/api/tags"])
@pytest.mark.parametrize("stage", ["headers", "body"])
def test_deadline_closes_without_partial_inventory_and_next_query_recovers(path, stage):
    async def scenario():
        calls = []
        closed = asyncio.Event()
        slow = True

        class Slow(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield body(path)
                while True:
                    await asyncio.sleep(0.01)
                    yield b" "

            async def aclose(self):
                closed.set()

        async def backend(request):
            calls.append((request.method, request.url.path))
            if slow and request.url.path == path:
                if stage == "headers":
                    await asyncio.Event().wait()
                return httpx.Response(200, stream=Slow())
            return httpx.Response(200, content=body(request.url.path))

        manager = LocalModelManager(transport=httpx.MockTransport(backend), timeout=0.1)
        value = PullInput(client_request_id=uuid4(), model="qwen3", confirm_download=True)
        pull = manager.start(value)
        await manager.cancel(pull.id)
        receipt = pull.view()
        try:
            view = await asyncio.wait_for(manager.status(None), 2)
            assert not view["reachable"] and view["version"] is None and view["models"] == []
            assert view["suggested"] and view["pulls"] == [receipt]
            assert closed.is_set() == (stage == "body")
            expected = [("GET", "/api/version")]
            if path == "/api/tags":
                expected.append(("GET", path))
            assert calls == expected
            assert manager.start(value).view() == receipt
            slow = False
            recovered = await manager.status(None)
            assert recovered["reachable"] and recovered["version"] == "test"
            assert recovered["pulls"] == [receipt]
            assert calls == expected + [("GET", "/api/version"), ("GET", "/api/tags")]
        finally:
            await manager.close()

    asyncio.run(scenario())


def test_version_and_catalog_share_the_budget():
    async def scenario():
        entered = asyncio.Event()
        cancelled = asyncio.Event()

        async def backend(request):
            if request.url.path == "/api/version":
                await asyncio.sleep(0.2)
            else:
                entered.set()
                try:
                    await asyncio.sleep(0.2)
                except asyncio.CancelledError:
                    cancelled.set()
                    raise
            return httpx.Response(200, content=body(request.url.path))

        manager = LocalModelManager(transport=httpx.MockTransport(backend), timeout=0.3)
        view = await asyncio.wait_for(manager.status(None), 2)
        assert entered.is_set() and cancelled.is_set()
        assert not view["reachable"] and view["models"] == []

    asyncio.run(scenario())


@pytest.mark.parametrize("path", ["/api/version", "/api/tags"])
def test_real_http_trickle_disconnects_before_eof(path):
    async def scenario():
        calls, handlers, errors = [], set(), []
        disconnected = asyncio.Event()

        async def serve(reader, writer):
            task = asyncio.current_task()
            handlers.add(task)
            try:
                request = await reader.readuntil(b"\r\n\r\n")
                route = request.split(b" ")[1].decode()
                calls.append(route)
                writer.write(b"HTTP/1.1 200 OK\r\nConnection: close\r\n\r\n" + body(route))
                await writer.drain()
                if route == path:
                    while True:
                        try:
                            if await asyncio.wait_for(reader.read(1), 0.02) == b"":
                                disconnected.set()
                                break
                        except TimeoutError:
                            writer.write(b" ")
                            await writer.drain()
            except Exception as error:
                errors.append(error)
            finally:
                writer.close()
                await writer.wait_closed()
                handlers.discard(task)

        server = await asyncio.start_server(serve, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        manager = LocalModelManager(timeout=0.5)
        try:
            view = await asyncio.wait_for(manager.status(f"http://127.0.0.1:{port}"), 5)
            assert not view["reachable"] and view["version"] is None and view["models"] == []
            await asyncio.wait_for(disconnected.wait(), 2)
            assert calls == (["/api/version"] if path == "/api/version" else ["/api/version", path])
            assert not errors
        finally:
            await manager.close()
            server.close()
            await server.wait_closed()
            for task in list(handlers):
                task.cancel()
            await asyncio.gather(*handlers, return_exceptions=True)

    asyncio.run(scenario())
