"""Uma resposta de redirecionamento não confirma download nem remoção."""

import asyncio
from uuid import uuid4

import httpx
import pytest

from bees_core.providers.local_models import LocalModelError, LocalModelManager, PullInput


@pytest.mark.parametrize("status", [301, 302, 303, 304, 307, 308])
@pytest.mark.parametrize("operation", ["pull", "delete"])
def test_redirect_cannot_confirm_an_effect(status, operation):
    async def scenario():
        calls = []

        def backend(value):
            calls.append(value)
            return httpx.Response(
                status,
                headers={"Location": "http://127.0.0.1:11435/other"},
                content=b'{"status":"success"}\n',
            )

        manager = LocalModelManager(transport=httpx.MockTransport(backend))
        if operation == "delete":
            with pytest.raises(LocalModelError, match="^pull_failed$"):
                await manager.delete(None, "qwen3")
        else:
            value = PullInput(client_request_id=uuid4(), model="qwen3", confirm_download=True)
            pull = manager.start(value)
            await pull.task
            assert pull.status == "failed" and pull.error == "pull_failed"
            assert pull.finished_at is not None and manager.start(value) is pull
        assert len(calls) == 1 and calls[0].url.port == 11434

    asyncio.run(scenario())
