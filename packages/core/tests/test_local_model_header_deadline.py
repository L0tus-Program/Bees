"""Headers incompletos não podem prender o acompanhamento de preparo local."""

import asyncio
from uuid import uuid4

import httpx
import pytest

from bees_core.providers.local_models import LocalModelError, LocalModelManager, PullInput


@pytest.mark.parametrize("mode", ["timeout", "cancel", "regular"])
def test_header_budget_preserves_replay_cancellation_and_long_downloads(mode):
    async def scenario():
        entered, interrupted = asyncio.Event(), asyncio.Event()
        calls = []

        class Regular(httpx.AsyncByteStream):
            closed = False

            async def __aiter__(self):
                for _ in range(8):
                    await asyncio.sleep(0.03)
                    yield b'{"status":"verifying"}\n'
                yield b'{"status":"success"}\n'

            async def aclose(self):
                self.closed = True

        stream = Regular()

        async def backend(request):
            calls.append(request)
            if len(calls) > 1:
                return httpx.Response(200, content=b'{"status":"success"}\n')
            entered.set()
            if mode == "regular":
                await asyncio.sleep(0.03)
                return httpx.Response(200, stream=stream)
            try:
                await asyncio.sleep(0.5)
            except asyncio.CancelledError:
                interrupted.set()
                raise
            return httpx.Response(200, content=b'{"status":"success"}\n')

        manager = LocalModelManager(transport=httpx.MockTransport(backend), pull_idle_timeout=0.12)
        value = PullInput(client_request_id=uuid4(), model="qwen3", confirm_download=True)
        alias = value.model_copy(update={"client_request_id": uuid4()})
        pull = manager.start(value)
        assert manager.start(alias) is pull
        await entered.wait()
        with pytest.raises(LocalModelError, match="model_in_use"):
            await manager.delete(None, "qwen3")
        if mode == "cancel":
            await manager.cancel(pull.id)
        else:
            await asyncio.wait_for(asyncio.shield(pull.task), 2)
        assert (
            pull.status
            == {"timeout": "failed", "cancel": "cancelled", "regular": "completed"}[mode]
        )
        assert pull.error == ("pull_failed" if mode == "timeout" else None)
        assert interrupted.is_set() == (mode != "regular")
        assert stream.closed == (mode == "regular")
        receipt = pull.view()
        assert receipt["finished_at"] is not None
        for prior in (value, alias):
            assert manager.start(prior).view() == receipt
            assert manager.request(prior.client_request_id).view() == receipt
        await manager.cancel(pull.id)
        await manager.close()
        assert len(calls) == 1 and pull.view() == receipt
        fresh = manager.start(value.model_copy(update={"client_request_id": uuid4()}))
        await fresh.task
        assert fresh.status == "completed" and len(calls) == 2
        for prior in (value, alias):
            assert manager.start(prior).view() == receipt
        await manager.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("status", [200, 404, 500])
def test_real_http_partial_headers_timeout_without_retry(status):
    async def scenario():
        calls, handlers, errors = [], set(), []
        disconnected = asyncio.Event()
        sent = []

        async def serve(reader, writer):
            handlers.add(asyncio.current_task())
            peer_closed = None
            try:
                head = await reader.readuntil(b"\r\n\r\n")
                length = next(
                    int(line.split(b":", 1)[1])
                    for line in head.lower().split(b"\r\n")
                    if line.startswith(b"content-length:")
                )
                body = await reader.readexactly(length)
                calls.append((head.split(b"\r\n", 1)[0], body))
                writer.write(f"HTTP/1.1 {status} Test\r\nX-Partial: ".encode())
                await writer.drain()
                peer_closed = asyncio.create_task(reader.read())
                for index in range(30):
                    done, _ = await asyncio.wait({peer_closed}, timeout=0.03)
                    if done:
                        assert peer_closed.result() == b""
                        disconnected.set()
                        break
                    writer.write(b"x")
                    await writer.drain()
                    sent.append(index)
                else:
                    writer.write(b"\r\nConnection: close\r\n\r\n" + b'{"status":"success"}\n')
                    await writer.drain()
            except BrokenPipeError, ConnectionResetError:
                disconnected.set()
            except Exception as error:
                errors.append(error)
            finally:
                if peer_closed is not None:
                    peer_closed.cancel()
                    await asyncio.gather(peer_closed, return_exceptions=True)
                writer.close()
                try:
                    await writer.wait_closed()
                except BrokenPipeError, ConnectionResetError:
                    pass
                handlers.discard(asyncio.current_task())

        server = await asyncio.start_server(serve, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        manager = LocalModelManager(pull_idle_timeout=0.3)
        try:
            value = PullInput(
                client_request_id=uuid4(),
                endpoint=f"http://127.0.0.1:{port}",
                model="qwen3",
                confirm_download=True,
            )
            pull = manager.start(value)
            await asyncio.wait_for(asyncio.shield(pull.task), 5)
            assert pull.status == "failed" and pull.error == "pull_failed"
            assert pull.view()["finished_at"] is not None
            await asyncio.wait_for(disconnected.wait(), 2)
            assert len(sent) < 30
            assert len(calls) == 1 and calls[0][0] == b"POST /api/pull HTTP/1.1"
            assert manager.start(value) is pull
            await manager.cancel(pull.id)
            assert len(calls) == 1 and not errors
        finally:
            await manager.close()
            server.close()
            await server.wait_closed()
            if handlers:
                await asyncio.wait_for(asyncio.gather(*handlers), 5)

    asyncio.run(scenario())
