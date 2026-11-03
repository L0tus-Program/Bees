"""Remoção e download concorrentes precisam respeitar o mesmo modelo e destino."""

import asyncio
from uuid import uuid4

import httpx
import pytest

from bees_core.providers.local_models import LocalModelError, LocalModelManager, PullInput


def request(**changes):
    return PullInput(
        **({"client_request_id": uuid4(), "model": "qwen3", "confirm_download": True} | changes)
    )


@pytest.mark.parametrize("operation", ["pull", "delete"])
def test_delete_in_flight_refuses_conflicting_effect_without_dispatch(operation):
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        calls = []

        async def backend(value):
            calls.append(value)
            if value.method == "DELETE":
                entered.set()
                await release.wait()
            return httpx.Response(200, content=b'{"status":"success"}\n')

        manager = LocalModelManager(transport=httpx.MockTransport(backend))
        deleting = asyncio.create_task(manager.delete(None, "qwen3"))
        try:
            await asyncio.wait_for(entered.wait(), 5)
            value = request(model="qwen3:latest", endpoint="http://127.0.0.1:11434/")
            with pytest.raises(LocalModelError, match="^model_in_use$"):
                if operation == "pull":
                    manager.start(value)
                else:
                    await asyncio.wait_for(manager.delete(value.endpoint, value.model), 5)
            assert len(calls) == 1
            release.set()
            await deleting
            # O pedido recusado não consumiu o UUID nem criou acompanhamento.
            pull = manager.start(value)
            await pull.task
            assert pull.status == "completed"
            assert [call.method for call in calls] == ["DELETE", "POST"]
        finally:
            release.set()
            await deleting
            await manager.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("outcome", ["success", "http_error", "transport_error", "cancelled"])
def test_delete_releases_exclusion_after_request_ends(outcome):
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        calls = []

        async def backend(value):
            calls.append(value)
            if value.method == "DELETE":
                entered.set()
                await release.wait()
                if outcome == "transport_error":
                    raise httpx.ConnectError("refused", request=value)
                if outcome == "http_error":
                    return httpx.Response(500)
            return httpx.Response(200, content=b'{"status":"success"}\n')

        manager = LocalModelManager(transport=httpx.MockTransport(backend))
        deleting = asyncio.create_task(manager.delete(None, "qwen3"))
        try:
            await asyncio.wait_for(entered.wait(), 5)
            with pytest.raises(LocalModelError, match="^model_in_use$"):
                manager.start(request())
            if outcome == "cancelled":
                deleting.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await deleting
            else:
                release.set()
                if outcome == "success":
                    await deleting
                else:
                    with pytest.raises(LocalModelError):
                        await deleting
            pull = manager.start(request())
            await pull.task
            assert pull.status == "completed"
            assert [call.method for call in calls] == ["DELETE", "POST"]
        finally:
            release.set()
            if not deleting.done():
                await deleting
            await manager.close()

    asyncio.run(scenario())


def test_delete_does_not_block_other_targets_or_terminal_replay():
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        calls = []

        async def backend(value):
            calls.append(value)
            if value.method == "DELETE" and value.url.port == 11434:
                entered.set()
                await release.wait()
            return httpx.Response(200, content=b'{"status":"success"}\n')

        manager = LocalModelManager(transport=httpx.MockTransport(backend))
        value = request()
        original = manager.start(value)
        await original.task
        terminal = original.view()
        deleting = asyncio.create_task(manager.delete(None, "qwen3"))
        try:
            await asyncio.wait_for(entered.wait(), 5)
            assert manager.start(value) is original
            assert original.view() == terminal
            with pytest.raises(LocalModelError, match="^pull_conflict$"):
                manager.start(value.model_copy(update={"model": "qwen3:4b"}))
            other_model = manager.start(request(model="qwen3:4b"))
            other_endpoint = manager.start(request(endpoint="http://127.0.0.1:11435"))
            await other_model.task
            await other_endpoint.task
            await manager.delete("http://127.0.0.1:11435", "qwen3")
            assert other_model.status == other_endpoint.status == "completed"
            assert len(calls) == 5
        finally:
            release.set()
            await deleting
            await manager.close()

    asyncio.run(scenario())
