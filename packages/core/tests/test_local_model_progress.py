"""Progresso exibido nunca deve aceitar valores negativos ou booleanos."""

import asyncio
import json
from uuid import uuid4

import httpx
import pytest

from bees_core.providers.local_models import LocalModelManager, PullInput


@pytest.mark.parametrize("field", ["total", "completed"])
@pytest.mark.parametrize("invalid", [-1, True, False, 1.5, "10", None])
def test_invalid_progress_fails_without_redispatch(field, invalid):
    async def scenario():
        calls = []
        event = {"status": "pulling layer", "digest": "sha256:x", "total": 10, "completed": 1}
        event[field] = invalid

        def backend(value):
            calls.append(value)
            return httpx.Response(
                200, content=(json.dumps(event) + '\n{"status":"success"}\n').encode()
            )

        manager = LocalModelManager(transport=httpx.MockTransport(backend))
        value = PullInput(client_request_id=uuid4(), model="qwen3", confirm_download=True)
        pull = manager.start(value)
        await pull.task
        assert pull.status == "failed" and pull.error == "pull_failed"
        assert pull.view()["completed_bytes"] >= 0
        assert manager.start(value) is pull and len(calls) == 1

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "counts,expected", [({"total": 0}, 0), ({"total": 10}, 0), ({"total": 10, "completed": 20}, 10)]
)
def test_valid_progress_keeps_zero_optional_and_clamped_values(counts, expected):
    async def scenario():
        event = {"status": "pulling layer", "digest": "sha256:x"} | counts
        transport = httpx.MockTransport(
            lambda _: httpx.Response(
                200, content=(json.dumps(event) + '\n{"status":"success"}').encode()
            )
        )
        manager = LocalModelManager(transport=transport)
        pull = manager.start(
            PullInput(client_request_id=uuid4(), model="qwen3", confirm_download=True)
        )
        await pull.task
        assert pull.status == "completed" and pull.view()["completed_bytes"] == expected

    asyncio.run(scenario())
