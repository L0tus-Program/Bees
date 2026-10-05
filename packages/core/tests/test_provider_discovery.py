import asyncio

import httpx
import pytest
from pydantic import SecretStr, ValidationError

from bees_core.providers.catalog import connection_for_provider
from bees_core.providers.contracts import ConnectionConfig, ProviderCapabilities, ProviderConfig
from bees_core.providers.discovery import DiscoveryAdapter
from bees_core.providers.errors import ProviderError
from bees_core.providers.ollama import OllamaAdapter
from bees_core.providers.openai import OpenAICompatibleAdapter


class Resolver:
    def __init__(self):
        self.calls = []

    def resolve(self, reference):
        self.calls.append(reference)
        return SecretStr("discovery-test-only-secret")


@pytest.mark.anyio
@pytest.mark.parametrize(
    "provider,items,expected",
    [
        (
            "openai",
            [
                {"id": "gpt-4o"},
                {"id": "o3"},
                {"id": "gpt-prompter"},
                {"id": "gpt-5-pro"},
                {"id": "gpt-5-codex"},
                {"id": "gpt-3.5-turbo-instruct"},
                {"id": "gpt-4-base"},
                {"id": "gpt-4o-audio-preview"},
                {"id": "gpt-image-1"},
                {"id": "gpt-4o-mini-tts"},
                {"id": "gpt-4o-transcribe"},
                {"id": "gpt-4o-realtime-preview"},
                {"id": "text-embedding-3-small"},
                {"id": "omni-moderation-latest"},
            ],
            ["gpt-4o", "gpt-prompter", "o3"],
        ),
        (
            "gemini",
            [
                {"id": "gemini-2.5-flash"},
                {"id": "gemini-3-pro"},
                {"id": "models/gemini-3-flash"},
                {"id": "gemini-2.5-flash-preview-tts"},
                {"id": "gemini-2.0-flash-image"},
                {"id": "text-embedding-004"},
            ],
            ["gemini-2.5-flash", "gemini-3-pro", "models/gemini-3-flash"],
        ),
        (
            "openrouter",
            [
                {
                    "id": "vendor/text",
                    "name": "Text",
                    "architecture": {
                        "input_modalities": ["text", "image"],
                        "output_modalities": ["text"],
                    },
                },
                {"id": "vendor/legacy", "architecture": {"output_modalities": ["text"]}},
                {"id": "vendor/image", "architecture": {"output_modalities": ["image"]}},
                {
                    "id": "vendor/image-input",
                    "architecture": {"input_modalities": ["image"], "output_modalities": ["text"]},
                },
            ],
            ["vendor/text", "vendor/legacy"],
        ),
    ],
)
async def test_named_catalog_filters_and_get_only(provider, items, expected):
    seen = []
    resolver = Resolver()
    config = connection_for_provider(provider, secret_ref="env:DISCOVERY_TEST")

    def handler(request):
        seen.append(request)
        assert request.method == "GET" and request.content == b""
        assert request.url == config.endpoint + "/models"
        assert request.headers["authorization"] == "Bearer discovery-test-only-secret"
        return httpx.Response(200, json={"data": items, "secret": "never-return-me"})

    models = await DiscoveryAdapter(resolver, httpx.MockTransport(handler)).discover(
        config, provider
    )
    assert [model.id for model in models] == expected
    assert len(seen) == 1 and resolver.calls == ["env:DISCOVERY_TEST"]
    assert "never-return-me" not in repr(models)


@pytest.mark.anyio
async def test_ollama_lists_only_present_local_models_without_show_or_download():
    seen = []

    def handler(request):
        seen.append(request)
        assert request.method == "GET" and request.url.path == "/api/tags"
        assert "authorization" not in request.headers and request.content == b""
        return httpx.Response(
            200,
            json={
                "models": [
                    {"name": "llama3.2:latest"},
                    {"name": "alias:latest", "remote_model": "cloud-model"},
                    {"name": "another:latest", "remote_host": "https://remote.invalid"},
                    {"name": "gemma-cloud:latest"},
                    {"name": "local@remote"},
                ]
            },
        )

    models = await DiscoveryAdapter(transport=httpx.MockTransport(handler)).discover(
        connection_for_provider("ollama"), "ollama"
    )
    assert [model.id for model in models] == ["llama3.2:latest"] and len(seen) == 1


@pytest.mark.anyio
@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"data": None},
        {"data": {}},
        {"data": ["model"]},
        {"data": [{"id": 123}]},
        {"data": [{"id": "bad\nmodel"}]},
        {"data": [{"id": " x"}]},
        {"data": [{"id": "x", "name": None}]},
        {"data": [{"id": "x"}, {"id": "x"}]},
        {"data": [{"id": "x" * 201}]},
        {"data": [{"id": "x", "name": "n" * 241}]},
        {"data": [{"id": str(i)} for i in range(4097)]},
    ],
)
async def test_malformed_catalog_is_rejected(payload):
    adapter = DiscoveryAdapter(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload))
    )
    with pytest.raises(ProviderError) as error:
        await adapter.discover(
            connection_for_provider("custom", endpoint="https://test.invalid/v1"), "custom"
        )
    assert error.value.code == "invalid_response"


@pytest.mark.anyio
@pytest.mark.parametrize(
    "architecture",
    [
        None,
        {},
        {"output_modalities": "text"},
        {"output_modalities": [123]},
        {"output_modalities": ["text"], "input_modalities": "text"},
    ],
)
async def test_openrouter_malformed_modality_cannot_assert_text(architecture):
    adapter = DiscoveryAdapter(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200, json={"data": [{"id": "vendor/model", "architecture": architecture}]}
            )
        )
    )
    with pytest.raises(ProviderError) as error:
        await adapter.discover(connection_for_provider("openrouter"), "openrouter")
    assert error.value.code == "invalid_response"


@pytest.mark.anyio
async def test_custom_catalog_empty_is_list_not_validation_and_name_is_plain_data():
    config = connection_for_provider("custom", endpoint="http://localhost:9000/v1")
    for items in ([], [{"id": "private/model", "name": "<img src=x onerror=alert(1)>"}]):
        models = await DiscoveryAdapter(
            transport=httpx.MockTransport(
                lambda _, items=items: httpx.Response(200, json={"data": items})
            )
        ).discover(config, "custom")
        assert [model.model_dump() for model in models] == items


@pytest.mark.anyio
@pytest.mark.parametrize(
    "status,code",
    [
        (302, "redirect_refused"),
        (401, "authentication_failed"),
        (403, "access_denied"),
        (429, "rate_limited"),
        (503, "provider_unavailable"),
    ],
)
async def test_catalog_http_errors_never_return_provider_body(status, code):
    adapter = DiscoveryAdapter(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                status,
                text="discovery-test-only-secret",
                headers={"location": "https://elsewhere.invalid"},
            )
        )
    )
    with pytest.raises(ProviderError) as error:
        await adapter.discover(
            connection_for_provider("custom", endpoint="https://test.invalid"), "custom"
        )
    assert error.value.code == code and "discovery-test-only-secret" not in str(error.value)


@pytest.mark.anyio
async def test_catalog_bound_is_separate_from_generation_and_used_by_diagnosis():
    payload = {"data": [{"id": "gpt-4o", "ignored": "x" * 1100000}]}
    transport = httpx.MockTransport(lambda _: httpx.Response(200, json=payload))
    config = ProviderConfig(
        kind="openai_compatible",
        endpoint="https://test.invalid/v1",
        model="gpt-4o",
        capabilities=ProviderCapabilities(),
    )
    assert config.max_response_bytes == 1048576 and config.max_catalog_bytes == 4194304
    assert (await OpenAICompatibleAdapter(transport=transport).diagnose(config)).status == "ok"
    connection = ConnectionConfig.model_validate(
        {k: v for k, v in config.model_dump().items() if k not in ("model", "capabilities")}
    )
    assert len(await DiscoveryAdapter(transport=transport).discover(connection, "custom")) == 1
    small = connection.model_copy(update={"max_catalog_bytes": 1024})
    with pytest.raises(ProviderError) as error:
        await DiscoveryAdapter(transport=transport).discover(small, "custom")
    assert error.value.code == "response_too_large"


class LargeStream(httpx.AsyncByteStream):
    closed = False

    async def __aiter__(self):
        yield b'{"data":['
        yield b" " * 4194304

    async def aclose(self):
        self.closed = True


@pytest.mark.anyio
async def test_chunked_response_is_bounded_and_closed():
    stream = LargeStream()
    adapter = DiscoveryAdapter(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200, stream=stream, headers={"content-type": "application/json"}
            )
        )
    )
    with pytest.raises(ProviderError) as error:
        await adapter.discover(
            connection_for_provider("custom", endpoint="https://test.invalid"), "custom"
        )
    assert error.value.code == "response_too_large" and stream.closed


@pytest.mark.anyio
async def test_catalog_deadline_cancellation_no_retry():
    seen = []

    async def respond(request):
        seen.append(request)
        await asyncio.sleep(0.1)
        return httpx.Response(200, json={"data": []})

    config = connection_for_provider("custom", endpoint="https://test.invalid").model_copy(
        update={"deadline_seconds": 0.01}
    )
    with pytest.raises(ProviderError) as error:
        await DiscoveryAdapter(transport=httpx.MockTransport(respond)).discover(config, "custom")
    assert error.value.code == "timeout" and len(seen) == 1


@pytest.mark.parametrize(
    "provider,endpoint",
    [
        ("openai", "https://evil.invalid/v1"),
        ("ollama", "http://192.168.1.2:11434"),
        ("custom", "http://evil.invalid"),
        ("custom", "https://user:secret@test.invalid"),
        ("custom", "https://test.invalid?key=secret"),
        ("custom_ollama", "https://remote.invalid"),
    ],
)
def test_connection_boundary_before_secret_or_network(provider, endpoint):
    with pytest.raises(ProviderError) as error:
        connection_for_provider(provider, endpoint=endpoint)
    assert error.value.code == "invalid_config"


def test_connection_has_no_model_and_secret_error_inputs_hidden():
    connection = connection_for_provider("ollama")
    assert "model" not in connection.model_dump()
    with pytest.raises(ValidationError) as error:
        ConnectionConfig(
            kind="openai_compatible", endpoint="https://test.invalid", api_key="secret"
        )
    assert "secret" not in str(error.value)


@pytest.mark.anyio
async def test_ollama_diagnosis_uses_catalog_limit_but_show_keeps_response_limit():
    config = ProviderConfig(
        kind="ollama",
        endpoint="http://localhost:11434",
        model="llama3.2:latest",
        capabilities=ProviderCapabilities(),
    )

    def handler(request):
        if request.url.path == "/api/tags":
            return httpx.Response(
                200,
                json={
                    "models": [
                        {
                            "name": "llama3.2:latest",
                            "ignored": "x" * 1100000,
                        }
                    ]
                },
            )
        return httpx.Response(200, json={"capabilities": ["completion"]})

    adapter = OllamaAdapter(transport=httpx.MockTransport(handler))
    assert (await adapter.diagnose(config)).status == "ok"
    limited = config.model_copy(update={"max_catalog_bytes": 1024})
    with pytest.raises(ProviderError) as error:
        await adapter.diagnose(limited)
    assert error.value.code == "response_too_large"

    def oversized_show(request):
        if request.url.path == "/api/tags":
            return handler(request)
        return httpx.Response(200, json={"capabilities": ["completion"], "ignored": "x" * 1100000})

    with pytest.raises(ProviderError) as error:
        await OllamaAdapter(transport=httpx.MockTransport(oversized_show)).diagnose(config)
    assert error.value.code == "response_too_large"
