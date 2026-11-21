"""Fragmentos de transporte não comprovam progresso do preparo local."""

import asyncio
from uuid import uuid4

import httpx
import pytest

from bees_core.providers.local_models import LocalModelError, LocalModelManager, PullInput


@pytest.mark.parametrize("mode", ["fragment", "blank", "error", "regular", "cancel"])
def test_record_deadline_preserves_receipts_and_explicit_recovery(mode):
    async def scenario():
        entered = asyncio.Event()
        calls = []

        class Stream(httpx.AsyncByteStream):
            closed = False
            fragments = 0

            async def __aiter__(self):
                if mode != "error":
                    yield (
                        b'{"status":"pulling x","digest":"sha256:x","total":100,"completed":40}\n'
                    )
                entered.set()
                for _ in range(20):
                    await asyncio.sleep(0.02)
                    self.fragments += 1
                    yield (
                        b'{"status":"verifying"}\n'
                        if mode == "regular"
                        else b" \r\n"
                        if mode == "blank"
                        else b" "
                    )
                yield b'{"status":"success"}\n'

            async def aclose(self):
                self.closed = True

        stream = Stream()

        def backend(value):
            calls.append(value)
            if len(calls) > 1:
                return httpx.Response(200, content=b'{"status":"success"}\n')
            return httpx.Response(500 if mode == "error" else 200, stream=stream)

        manager = LocalModelManager(transport=httpx.MockTransport(backend), pull_idle_timeout=0.15)
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
            await pull.task
        assert pull.status == (
            "completed" if mode == "regular" else "cancelled" if mode == "cancel" else "failed"
        )
        assert pull.error == ("pull_failed" if mode in {"fragment", "blank", "error"} else None)
        assert stream.closed
        if mode in {"fragment", "blank", "error"}:
            assert stream.fragments < 20
        receipt = pull.view()
        assert receipt["finished_at"] is not None
        assert receipt["completed_bytes"] == (0 if mode == "error" else 40)
        for prior in (value, alias):
            assert manager.start(prior).view() == receipt
        await manager.cancel(pull.id)
        await manager.close()
        assert pull.view() == receipt and len(calls) == 1
        fresh = manager.start(value.model_copy(update={"client_request_id": uuid4()}))
        await fresh.task
        assert fresh.status == "completed" and len(calls) == 2
        for prior in (value, alias):
            assert manager.start(prior).view() == receipt
        await manager.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("mode", ["fragment", "blank", "error", "success_open", "regular"])
def test_real_http_fragments_do_not_renew_record_deadline(mode):
    async def scenario():
        handlers = set()
        sent = []
        disconnected = asyncio.Event()
        errors = []

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
                await reader.readexactly(length)
                status = 500 if mode == "error" else 200
                writer.write(f"HTTP/1.1 {status} Test\r\nConnection: close\r\n\r\n".encode())
                if mode != "error":
                    writer.write(
                        b'{"status":"pulling x","digest":"sha256:x","total":100,"completed":40}\n'
                    )
                if mode == "success_open":
                    writer.write(b'{"status":"success"}\n')
                await writer.drain()
                peer_closed = asyncio.create_task(reader.read())
                for index in range(30):
                    done, _ = await asyncio.wait({peer_closed}, timeout=0.03)
                    if done:
                        assert peer_closed.result() == b""
                        disconnected.set()
                        break
                    writer.write(
                        b'{"status":"verifying"}\n'
                        if mode == "regular"
                        else b"\r\n"
                        if mode == "blank"
                        else b" "
                    )
                    await writer.drain()
                    sent.append(index)
                else:
                    writer.write(b'{"status":"success"}\n')
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
            assert pull.status == ("completed" if mode == "regular" else "failed")
            assert pull.error == (None if mode == "regular" else "pull_failed")
            if mode != "regular":
                await asyncio.wait_for(disconnected.wait(), 5)
                assert len(sent) < 30
            assert pull.view()["completed_bytes"] == (0 if mode == "error" else 40)
            assert manager.start(value) is pull
            assert not errors
        finally:
            await manager.close()
            server.close()
            await server.wait_closed()
            if handlers:
                await asyncio.wait_for(asyncio.gather(*handlers), 5)

    asyncio.run(scenario())
