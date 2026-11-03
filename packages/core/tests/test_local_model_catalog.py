"""Catálogo inválido não pode virar inventário vazio ou parcial disponível."""

import asyncio
from uuid import uuid4

import httpx
import pytest

from bees_core.providers.local_models import LocalModelManager, PullInput


@pytest.mark.parametrize(
    ("version", "tags"),
    [
        ([], {"models": []}),
        ({}, {"models": []}),
        ({"version": " "}, {"models": []}),
        ({"version": 1}, {"models": []}),
        ({"version": "0.40.6"}, []),
        ({"version": "0.40.6"}, {}),
        ({"version": "0.40.6"}, {"models": None}),
        ({"version": "0.40.6"}, {"models": {}}),
        ({"version": "0.40.6"}, {"models": [None]}),
        ({"version": "0.40.6"}, {"models": [{"name": " "}]}),
        ({"version": "0.40.6"}, {"models": [{"name": "ok"}, {}]}),
        *[
            ({"version": "0.40.6"}, {"models": [{"name": "ok", "size": size}]})
            for size in (-1, True, "100", 1.5)
        ],
        ({"version": "0.40.6"}, {"models": [{"name": "ok"}] * 4097}),
    ],
)
def test_invalid_snapshot_is_unavailable_and_new_query_can_recover(version, tags):
    async def scenario():
        calls = []
        responses = {"/api/version": version, "/api/tags": tags}

        def backend(value):
            calls.append((value.method, value.url.path))
            return httpx.Response(200, json=responses[value.url.path])

        manager = LocalModelManager(transport=httpx.MockTransport(backend))
        # Acompanhamento anterior continua disponível mesmo se o catálogo falhar.
        pull = manager.start(
            PullInput(client_request_id=uuid4(), model="qwen3", confirm_download=True)
        )
        await manager.cancel(pull.id)
        unavailable = await manager.status(None)
        assert unavailable["reachable"] is False
        assert unavailable["version"] is None and unavailable["models"] == []
        assert unavailable["pulls"] == [pull.view()] and unavailable["suggested"]
        responses.update({"/api/version": {"version": "0.40.6"}, "/api/tags": {"models": []}})
        recovered = await manager.status(None)
        assert recovered["reachable"] is True and recovered["models"] == []
        assert recovered["pulls"] == unavailable["pulls"]
        assert calls == [("GET", "/api/version"), ("GET", "/api/tags")] * 2

    asyncio.run(scenario())


def test_optional_metadata_and_extra_fields_do_not_hide_valid_models():
    async def scenario():
        def backend(value):
            return httpx.Response(
                200,
                json={"version": "0.40.6", "extra": True}
                if value.url.path == "/api/version"
                else {"models": [{"name": "a"}, {"name": "b", "size": 0, "extra": True}]},
            )

        view = await LocalModelManager(transport=httpx.MockTransport(backend)).status(None)
        assert view["reachable"] is True
        assert view["models"] == [
            {"name": "a", "size_bytes": None, "modified_at": None},
            {"name": "b", "size_bytes": 0, "modified_at": None},
        ]

    asyncio.run(scenario())
