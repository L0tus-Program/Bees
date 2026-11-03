"""Cancelamento do preparo local antes de a coroutine iniciar o transporte."""

import asyncio
from datetime import UTC, datetime
from uuid import uuid4

import httpx
import pytest

from bees_core.providers.local_models import LocalModelManager, PullInput


def request():
    return PullInput(client_request_id=uuid4(), model="qwen3:1.7b", confirm_download=True)


@pytest.mark.parametrize("shutdown", [False, True])
def test_cancel_before_first_dispatch_finishes_record_and_preserves_replay(shutdown):
    async def scenario():
        calls = []

        def backend(value):
            calls.append(value)
            return httpx.Response(200, content=b'{"status":"success"}\n')

        finished = datetime(2026, 11, 3, tzinfo=UTC)
        manager = LocalModelManager(transport=httpx.MockTransport(backend), clock=lambda: finished)
        value = request()
        pull = manager.start(value)
        # Sem yield: _run ainda não entrou no try/finally.
        if shutdown:
            await manager.close()
        else:
            assert await manager.cancel(pull.id) is pull
        assert pull.task.done() and pull.task.cancelled()
        assert pull.status == "cancelled"
        assert pull.finished_at == finished
        assert calls == []
        assert manager.start(value) is pull
        assert pull.status == "cancelled"
        assert await manager.cancel(pull.id) is pull
        assert pull.finished_at == finished

        # Uma nova decisão com UUID novo pode preparar o mesmo modelo.
        fresh = manager.start(request())
        assert fresh.id != pull.id
        await fresh.task
        assert fresh.status == "completed"
        assert len(calls) == 1
        await manager.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("success", [False, True])
def test_cancel_preserves_terminal_result_and_finished_at(success):
    async def scenario():
        now = datetime(2026, 11, 3, tzinfo=UTC)
        content = b'{"status":"success"}\n' if success else b'{"error":"no space left"}\n'
        manager = LocalModelManager(
            transport=httpx.MockTransport(lambda _: httpx.Response(200, content=content)),
            clock=lambda: now,
        )
        pull = manager.start(request())
        await pull.task
        expected = pull.view()
        assert pull.status == ("completed" if success else "failed")
        now = datetime(2026, 11, 4, tzinfo=UTC)
        assert await manager.cancel(pull.id) is pull
        await manager.close()
        assert pull.view() == expected

    asyncio.run(scenario())
