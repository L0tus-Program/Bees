"""Inatividade de transporte encerra acompanhamento sem prazo total do download."""

import asyncio
import json
from uuid import uuid4

import httpx
import pytest

from bees_core.providers.local_models import LocalModelError, LocalModelManager, PullInput


def request():
    return PullInput(client_request_id=uuid4(), model="qwen3", confirm_download=True)


def test_download_has_finite_idle_budget_without_changing_other_phases():
    async def scenario():
        calls = []

        def backend(value):
            calls.append(value)
            budget = value.extensions["timeout"]
            assert budget["connect"] == budget["write"] == budget["pool"] == 7
            assert budget["read"] == (120 if value.url.path == "/api/pull" else 7)
            if value.url.path == "/api/version":
                return httpx.Response(200, json={"version": "test"})
            if value.url.path == "/api/tags":
                return httpx.Response(200, json={"models": []})
            return httpx.Response(200, content=b'{"status":"success"}\n')

        manager = LocalModelManager(transport=httpx.MockTransport(backend), timeout=7)
        assert (await manager.status(None))["reachable"]
        pull = manager.start(request())
        await pull.task
        assert pull.status == "completed"
        await manager.delete(None, "other")
        assert [r.method for r in calls] == ["GET", "GET", "POST", "DELETE"]
        await manager.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("stage", ["headers", "progress", "error"])
def test_timeout_closes_preserves_progress_aliases_and_explicit_recovery(stage):
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        calls = []

        class Stalled(httpx.AsyncByteStream):
            closed = False

            async def __aiter__(self):
                if stage == "progress":
                    yield (
                        b'{"status":"pulling x","digest":"sha256:x","total":100,"completed":40}\n'
                    )
                elif stage == "error":
                    yield b"private-prefix"
                entered.set()
                await release.wait()
                raise httpx.ReadTimeout("private-timeout-detail")
                yield b""  # pragma: no cover

            async def aclose(self):
                self.closed = True

        stream = Stalled()

        async def backend(value):
            calls.append(value)
            if len(calls) == 1:
                assert value.extensions["timeout"]["read"] == 120
                if stage == "headers":
                    entered.set()
                    await release.wait()
                    raise httpx.ReadTimeout("private-header-detail")
                return httpx.Response(500 if stage == "error" else 200, stream=stream)
            return httpx.Response(200, content=b'{"status":"success"}\n')

        manager = LocalModelManager(transport=httpx.MockTransport(backend))
        value = request()
        pull = manager.start(value)
        alias = value.model_copy(update={"client_request_id": uuid4()})
        assert manager.start(alias) is pull
        await entered.wait()
        with pytest.raises(LocalModelError, match="model_in_use"):
            await manager.delete(None, "qwen3:latest")
        release.set()
        await pull.task
        assert pull.task.exception() is None
        assert pull.status == "failed" and pull.error == "pull_failed"
        assert stream.closed or stage == "headers"
        receipt = pull.view()
        assert receipt["finished_at"] is not None
        assert receipt["completed_bytes"] == (40 if stage == "progress" else 0)
        assert receipt["total_bytes"] == (100 if stage == "progress" else 0)
        assert "private" not in str(receipt)
        for prior in (value, alias):
            assert manager.start(prior).view() == receipt
        await manager.cancel(pull.id)
        await manager.close()
        assert pull.view() == receipt and len(calls) == 1
        fresh = manager.start(value.model_copy(update={"client_request_id": uuid4()}))
        await fresh.task
        assert fresh.status == "completed" and fresh.id != pull.id
        for prior in (value, alias):
            assert manager.start(prior).view() == receipt
        assert len(calls) == 2
        await manager.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("mode", ["headers", "progress", "404", "500", "cancel", "regular"])
def test_real_http_idle_disconnect_and_regular_progress(mode):
    async def scenario():
        sent, disconnected = asyncio.Event(), asyncio.Event()
        handlers = set()
        errors = []

        async def serve(reader, writer):
            task = asyncio.current_task()
            handlers.add(task)
            try:
                head = await reader.readuntil(b"\r\n\r\n")
                length = next(
                    int(line.split(b":", 1)[1])
                    for line in head.lower().split(b"\r\n")
                    if line.startswith(b"content-length:")
                )
                await reader.readexactly(length)
                status = int(mode) if mode in {"404", "500"} else 200
                if mode != "headers":
                    writer.write(
                        f"HTTP/1.1 {status} Test\r\nContent-Type: application/x-ndjson\r\n"
                        "Connection: close\r\n\r\n".encode()
                    )
                    if mode in {"progress", "cancel"}:
                        writer.write(
                            b'{"status":"pulling x","digest":"sha256:x",'
                            b'"total":100,"completed":40}\n'
                        )
                    elif mode in {"404", "500"}:
                        writer.write(b"private-incomplete-error")
                    await writer.drain()
                sent.set()
                if mode == "regular":
                    # Duração total maior que o prazo entre leituras, sem timeout global.
                    for index in range(12):
                        writer.write(json.dumps({"status": f"pulling {index}"}).encode() + b"\n")
                        await writer.drain()
                        await asyncio.sleep(0.2)
                    writer.write(b'{"status":"success"}\n')
                    await writer.drain()
                else:
                    assert await reader.read() == b""
                    disconnected.set()
            except Exception as error:
                errors.append(error)
            finally:
                writer.close()
                await writer.wait_closed()
                handlers.discard(task)

        server = await asyncio.start_server(serve, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        budget = 2 if mode == "regular" else 0.5
        manager = LocalModelManager(pull_idle_timeout=budget)
        value = request().model_copy(update={"endpoint": f"http://127.0.0.1:{port}"})
        try:
            pull = manager.start(value)
            await asyncio.wait_for(sent.wait(), 10)
            if mode == "cancel":
                await manager.cancel(pull.id)
            if mode != "cancel":
                await asyncio.wait_for(asyncio.shield(pull.task), 10)
            assert pull.status == (
                "completed" if mode == "regular" else "cancelled" if mode == "cancel" else "failed"
            )
            if mode not in {"cancel", "regular"}:
                assert pull.error == "pull_failed"
            if mode == "progress":
                assert pull.view()["completed_bytes"] == 40
            if mode != "regular":
                await asyncio.wait_for(disconnected.wait(), 10)
            assert manager.start(value) is pull
            assert not errors
        finally:
            await manager.close()
            server.close()
            await server.wait_closed()
            if handlers:
                await asyncio.wait_for(asyncio.gather(*handlers), 10)

    asyncio.run(scenario())
