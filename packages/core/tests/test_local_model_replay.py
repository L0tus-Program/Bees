"""UUIDs que compartilham um download não podem despachar outra tentativa no replay."""

import asyncio
from uuid import uuid4

import httpx
import pytest

from bees_core.providers.local_models import LocalModelError, LocalModelManager, PullInput


def request(**changes):
    return PullInput(
        **({"client_request_id": uuid4(), "model": "qwen3", "confirm_download": True} | changes)
    )


@pytest.mark.parametrize("outcome", ["completed", "failed", "cancelled"])
def test_shared_requests_replay_terminal_record_even_during_new_attempt(outcome):
    async def scenario():
        calls = []
        entered = asyncio.Event()
        release = asyncio.Event()

        async def backend(value):
            calls.append(value)
            entered.set()
            await release.wait()
            content = (
                b'{"error":"no space left"}\n' if outcome == "failed" else b'{"status":"success"}\n'
            )
            return httpx.Response(200, content=content)

        manager = LocalModelManager(transport=httpx.MockTransport(backend))
        original = request()
        pull = manager.start(original)
        await asyncio.wait_for(entered.wait(), 5)
        # Nomes normalizados e UUIDs independentes continuam compartilhando o efeito.
        aliases = [request(model="qwen3:latest") for _ in range(3)]
        for value in aliases:
            assert manager.start(value) is pull
        assert len(calls) == 1
        if outcome == "cancelled":
            await manager.cancel(pull.id)
        else:
            release.set()
            await pull.task
        assert pull.status == outcome
        terminal = pull.view()
        for value in [original, *aliases]:
            assert manager.start(value) is pull
            assert pull.view() == terminal
        assert len(calls) == 1

        # UUID novo representa nova decisão; UUID antigo continua preso ao resultado anterior.
        fresh = manager.start(request())
        try:
            assert fresh.id != pull.id
            for value in [original, *aliases]:
                assert manager.start(value) is pull
            assert pull.view() == terminal
        finally:
            await manager.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("terminal", [False, True])
@pytest.mark.parametrize("changes", [{"model": "qwen3:4b"}, {"endpoint": "http://127.0.0.1:11435"}])
def test_shared_uuid_conflicts_before_new_dispatch(terminal, changes):
    async def scenario():
        calls = []

        def backend(value):
            calls.append(value)
            return httpx.Response(200, content=b'{"status":"success"}\n')

        manager = LocalModelManager(transport=httpx.MockTransport(backend))
        pull = manager.start(request())
        alias = request()
        assert manager.start(alias) is pull
        if terminal:
            await pull.task
        try:
            with pytest.raises(LocalModelError, match="^pull_conflict$"):
                manager.start(alias.model_copy(update=changes))
            assert manager.start(alias) is pull
            assert len(calls) == int(terminal)
        finally:
            await manager.close()

    asyncio.run(scenario())
