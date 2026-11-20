"""An explicit malformed error must never turn into a successful preparation."""

import asyncio
import json
from uuid import uuid4

import httpx
import pytest
from test_local_model_stream_bounds import Stream

from bees_core.providers.local_models import LocalModelManager, PullInput


@pytest.mark.parametrize(
    "error", [None, False, 0, 1.5, ["private-detail"], {"no space": "private-detail"}]
)
@pytest.mark.parametrize("same_event", [False, True])
def test_invalid_error_closes_preserves_progress_and_requires_explicit_new_request(
    error, same_event
):
    async def scenario():
        calls = []
        event = {"error": error}
        if same_event:
            event["status"] = "success"
        stream = Stream(
            [
                b'{"status":"pulling x","digest":"sha256:x","total":100,"completed":40}\n',
                (json.dumps(event) + "\n").encode(),
                b'{"status":"success"}\n',
            ]
        )

        def backend(request):
            calls.append((request.method, request.url.path))
            if len(calls) == 1:
                return httpx.Response(200, stream=stream)
            return httpx.Response(200, content=b'{"status":"success"}\n')

        manager = LocalModelManager(transport=httpx.MockTransport(backend))
        value = PullInput(client_request_id=uuid4(), model="qwen3", confirm_download=True)
        pull = manager.start(value)
        alias = value.model_copy(update={"client_request_id": uuid4()})
        assert manager.start(alias) is pull
        await pull.task
        assert pull.status == "failed" and pull.error == "pull_failed"
        assert pull.task.exception() is None and stream.closed and stream.read == 2
        terminal = pull.view()
        assert terminal["finished_at"] is not None
        assert terminal["total_bytes"] == 100 and terminal["completed_bytes"] == 40
        assert "private-detail" not in str(terminal)
        for previous in (value, alias):
            assert manager.start(previous) is pull
        assert await manager.cancel(pull.id) is pull
        await manager.close()
        assert pull.view() == terminal and calls == [("POST", "/api/pull")]

        fresh = manager.start(value.model_copy(update={"client_request_id": uuid4()}))
        await fresh.task
        assert fresh.id != pull.id and fresh.status == "completed"
        for previous in (value, alias):
            assert manager.start(previous) is pull
        assert pull.view() == terminal and calls == [("POST", "/api/pull")] * 2
        await manager.close()

    asyncio.run(scenario())
