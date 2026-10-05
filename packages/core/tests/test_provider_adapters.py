import asyncio
import json

import httpx
import pytest
from pydantic import SecretStr

from bees_core.providers.base import create_adapter
from bees_core.providers.contracts import (
    ChatMessage,
    ChatRequest,
    ProviderCapabilities,
    ProviderConfig,
    ToolCall,
    ToolDefinition,
)
from bees_core.providers.errors import ProviderError


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def config(kind: str, **changes) -> ProviderConfig:
    return ProviderConfig.model_validate(
        {
            "kind": kind,
            "endpoint": "https://models.example/v1"
            if kind == "openai_compatible"
            else "http://127.0.0.1:11434",
            "model": "test-model",
            "capabilities": {"text": True, "tool_calls": True},
        }
        | changes
    )


def request(*, tools: bool = False) -> ChatRequest:
    return ChatRequest(
        messages=[ChatMessage(role="user", content="DADO DE TESTE")],
        tools=[
            ToolDefinition(
                name="lookup",
                parameters={
                    "type": "object",
                    "properties": {"city": {"type": "string"}},
                    "required": ["city"],
                    "additionalProperties": False,
                },
            )
        ]
        if tools
        else [],
    )


def text_response(kind: str) -> dict:
    if kind == "openai_compatible":
        return {
            "choices": [
                {
                    "message": {"role": "assistant", "content": "Resposta de teste"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        }
    return {
        "message": {"role": "assistant", "content": "Resposta de teste"},
        "done": True,
        "done_reason": "stop",
        "prompt_eval_count": 10,
        "eval_count": 5,
    }


def handler(kind: str, payload: dict, seen: list[httpx.Request]):
    def respond(incoming: httpx.Request) -> httpx.Response:
        seen.append(incoming)
        if incoming.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "test-model:latest"}]})
        if incoming.url.path == "/api/show":
            return httpx.Response(200, json={"capabilities": ["completion", "tools"]})
        return httpx.Response(200, json=payload)

    return respond


class Resolver:
    def __init__(self) -> None:
        self.calls = 0

    def resolve(self, ref: str) -> SecretStr:
        self.calls += 1
        return SecretStr("TEST-SECRET")


@pytest.mark.anyio
@pytest.mark.parametrize("kind", ["openai_compatible", "ollama"])
async def test_text_contract_normalized_on_both_backends(kind: str) -> None:
    seen = []
    adapter = create_adapter(
        kind, transport=httpx.MockTransport(handler(kind, text_response(kind), seen))
    )
    response = await adapter.complete(config(kind), request())
    assert response.message.content == "Resposta de teste"
    assert response.usage.kind == "reported"
    assert response.usage.total_tokens == 15
    body = json.loads(seen[-1].content)
    assert body["stream"] is False
    if kind == "openai_compatible":
        assert body["store"] is False
        assert len(seen) == 1
    else:
        assert [item.url.path for item in seen] == ["/api/tags", "/api/show", "/api/chat"]
        assert all(b"DADO DE TESTE" not in item.content for item in seen[:-1])


@pytest.mark.anyio
@pytest.mark.parametrize("kind", ["openai_compatible", "ollama"])
async def test_tool_call_and_results_roundtrip_without_executing_tools(kind: str) -> None:
    seen = []
    payload = text_response(kind)
    raw_call = {"function": {"name": "lookup", "arguments": {"city": "São Paulo"}}}
    if kind == "openai_compatible":
        raw_call |= {"id": "call1", "type": "function"}
        raw_call["function"]["arguments"] = json.dumps(raw_call["function"]["arguments"])
        payload["choices"][0]["message"] = {
            "role": "assistant",
            "content": None,
            "tool_calls": [raw_call],
        }
        payload["choices"][0]["finish_reason"] = "tool_calls"
    else:
        payload["message"] = {"role": "assistant", "content": "", "tool_calls": [raw_call]}
    adapter = create_adapter(kind, transport=httpx.MockTransport(handler(kind, payload, seen)))
    original = request(tools=True)
    response = await adapter.complete(config(kind), original)
    call = response.message.tool_calls[0]
    assert call.arguments == {"city": "São Paulo"}
    assert call.name == "lookup"
    follow_up = ChatRequest(
        messages=[
            *original.messages,
            response.message,
            ChatMessage(role="tool", content="Resultado de teste", tool_call_id=call.id),
        ],
        tools=original.tools,
    )
    text = text_response(kind)
    adapter = create_adapter(kind, transport=httpx.MockTransport(handler(kind, text, seen)))
    assert (await adapter.complete(config(kind), follow_up)).message.content == "Resposta de teste"
    message = json.loads(seen[-1].content)["messages"][-1]
    assert message["role"] == "tool"
    assert (
        message.get("tool_call_id") == call.id
        if kind == "openai_compatible"
        else message["tool_name"] == "lookup"
    )


@pytest.mark.anyio
async def test_ollama_normalizes_out_of_order_results_for_same_tool() -> None:
    seen = []
    history = ChatRequest(
        messages=[
            ChatMessage(
                role="assistant",
                tool_calls=[
                    ToolCall(id="call1", name="old_tool", arguments={"city": "A"}),
                    ToolCall(id="call2", name="old_tool", arguments={"city": "B"}),
                ],
            ),
            ChatMessage(role="tool", tool_call_id="call2", content="Result B"),
            ChatMessage(role="tool", tool_call_id="call1", content="Result A"),
        ]
    )
    adapter = create_adapter(
        "ollama", transport=httpx.MockTransport(handler("ollama", text_response("ollama"), seen))
    )
    await adapter.complete(config("ollama"), history)
    wire = json.loads(seen[-1].content)["messages"]
    assert [message["content"] for message in wire[1:]] == ["Result A", "Result B"]
    assert history.messages[1].content == "Result B"


@pytest.mark.anyio
async def test_capability_rejection_precedes_secret_and_network() -> None:
    resolver = Resolver()
    seen = []
    adapter = create_adapter(
        "openai_compatible",
        resolver,
        transport=httpx.MockTransport(handler("openai_compatible", {}, seen)),
    )
    with pytest.raises(ProviderError) as error:
        await adapter.complete(
            config(
                "openai_compatible",
                secret_ref="env:TEST_KEY",
                capabilities=ProviderCapabilities(text=True, tool_calls=False),
            ),
            request(tools=True),
        )
    assert error.value.code == "unsupported_capability"
    assert resolver.calls == 0
    assert seen == []


@pytest.mark.anyio
@pytest.mark.parametrize(
    "status,code",
    [
        (401, "authentication_failed"),
        (429, "rate_limited"),
        (503, "provider_unavailable"),
        (302, "redirect_refused"),
        (400, "provider_rejected"),
    ],
)
async def test_errors_are_sanitized_without_retry_or_redirect(status: int, code: str) -> None:
    resolver = Resolver()
    seen = []

    def respond(incoming):
        seen.append(incoming)
        return httpx.Response(
            status,
            json={"error": "TEST-SECRET and PRIVATE PROMPT"},
            headers={"location": "https://unintended.example"},
        )

    adapter = create_adapter("openai_compatible", resolver, transport=httpx.MockTransport(respond))
    with pytest.raises(ProviderError) as error:
        await adapter.complete(config("openai_compatible", secret_ref="env:TEST_KEY"), request())
    assert error.value.code == code
    assert "TEST-SECRET" not in repr(error.value)
    assert "PRIVATE PROMPT" not in str(error.value)
    assert len(seen) == 1
    assert seen[0].headers["authorization"] == "Bearer TEST-SECRET"


@pytest.mark.anyio
@pytest.mark.parametrize("field", ["remote_host", "remote_model"])
async def test_ollama_refuses_cloud_alias_before_sending_prompt(field: str) -> None:
    seen = []

    def respond(incoming):
        seen.append(incoming)
        return httpx.Response(
            200, json={"models": [{"name": "test-model:latest", field: "cloud-target"}]}
        )

    adapter = create_adapter("ollama", transport=httpx.MockTransport(respond))
    with pytest.raises(ProviderError) as error:
        await adapter.complete(config("ollama"), request())
    assert error.value.code == "local_model_required"
    assert [item.url.path for item in seen] == ["/api/tags"]


@pytest.mark.anyio
async def test_ollama_show_must_support_declared_tools() -> None:
    seen = []

    def respond(incoming):
        seen.append(incoming)
        if incoming.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "test-model:latest"}]})
        return httpx.Response(200, json={"capabilities": ["completion"]})

    with pytest.raises(ProviderError) as error:
        await create_adapter("ollama", transport=httpx.MockTransport(respond)).complete(
            config("ollama"), request()
        )
    assert error.value.code == "unsupported_capability"
    assert all(item.url.path != "/api/chat" for item in seen)


class TrackingStream(httpx.AsyncByteStream):
    def __init__(self, chunks: list[bytes], delay: float = 0) -> None:
        self.chunks = chunks
        self.delay = delay
        self.closed = False

    async def __aiter__(self):
        for chunk in self.chunks:
            await asyncio.sleep(self.delay)
            yield chunk

    async def aclose(self):
        self.closed = True


@pytest.mark.anyio
async def test_response_byte_limit_closes_stream() -> None:
    stream = TrackingStream([b"x" * 700, b"y" * 700])
    transport = httpx.MockTransport(
        lambda _: httpx.Response(200, stream=stream, headers={"content-type": "application/json"})
    )
    with pytest.raises(ProviderError) as error:
        await create_adapter("openai_compatible", transport=transport).complete(
            config("openai_compatible", max_response_bytes=1024), request()
        )
    assert error.value.code == "response_too_large"
    assert stream.closed


@pytest.mark.anyio
async def test_total_deadline_interrupts_slow_chunks_and_closes_stream() -> None:
    stream = TrackingStream([b"{", b"}"] * 100, delay=0.02)
    transport = httpx.MockTransport(
        lambda _: httpx.Response(200, stream=stream, headers={"content-type": "application/json"})
    )
    with pytest.raises(ProviderError) as error:
        await create_adapter("openai_compatible", transport=transport).complete(
            config("openai_compatible", deadline_seconds=0.05), request()
        )
    assert error.value.code == "timeout"
    assert stream.closed


@pytest.mark.anyio
async def test_cancellation_propagates_and_closes_stream() -> None:
    stream = TrackingStream([b"{}"], delay=1)
    transport = httpx.MockTransport(
        lambda _: httpx.Response(200, stream=stream, headers={"content-type": "application/json"})
    )
    task = asyncio.create_task(
        create_adapter("openai_compatible", transport=transport).complete(
            config("openai_compatible"), request()
        )
    )
    await asyncio.sleep(0.02)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert stream.closed


@pytest.mark.anyio
@pytest.mark.parametrize(
    "payload",
    [
        {"choices": []},
        {"choices": [{"message": {"role": "user", "content": "X"}, "finish_reason": "stop"}]},
        {
            "choices": [
                {
                    "message": {"role": "assistant", "content": "X", "tool_calls": {}},
                    "finish_reason": "stop",
                }
            ]
        },
        {
            "choices": [
                {"message": {"role": "assistant", "content": "X"}, "finish_reason": "stop"}
            ],
            "usage": [],
        },
    ],
)
async def test_malformed_provider_responses_are_rejected(payload: dict) -> None:
    adapter = create_adapter(
        "openai_compatible",
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload)),
    )
    with pytest.raises(ProviderError) as error:
        await adapter.complete(config("openai_compatible"), request())
    assert error.value.code == "invalid_response"


@pytest.mark.anyio
async def test_unknown_tool_is_rejected_without_execution() -> None:
    payload = text_response("openai_compatible")
    payload["choices"][0]["message"]["tool_calls"] = [
        {
            "id": "call1",
            "type": "function",
            "function": {"name": "execute_shell", "arguments": "{}"},
        }
    ]
    adapter = create_adapter(
        "openai_compatible",
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload)),
    )
    with pytest.raises(ProviderError) as error:
        await adapter.complete(config("openai_compatible"), request(tools=True))
    assert error.value.code == "invalid_response"


@pytest.mark.anyio
async def test_openai_diagnose_checks_selected_model_not_inferred_capabilities() -> None:
    transport = httpx.MockTransport(
        lambda _: httpx.Response(200, json={"data": [{"id": "test-model"}]})
    )
    diagnostic = await create_adapter("openai_compatible", transport=transport).diagnose(
        config("openai_compatible")
    )
    assert diagnostic.status == "ok"
    assert diagnostic.capabilities.tool_calls is True
    transport = httpx.MockTransport(lambda _: httpx.Response(200, json={"data": [{"id": "other"}]}))
    with pytest.raises(ProviderError) as error:
        await create_adapter("openai_compatible", transport=transport).diagnose(
            config("openai_compatible")
        )
    assert error.value.code == "model_unavailable"


@pytest.mark.anyio
@pytest.mark.parametrize("done_reason", ["length", "unexpected", None, False])
async def test_ollama_rejects_truncated_or_malformed_tool_completion(done_reason) -> None:
    payload = text_response("ollama")
    payload["message"]["tool_calls"] = [
        {"function": {"name": "lookup", "arguments": {"city": "São Paulo"}}}
    ]
    payload["done_reason"] = done_reason
    adapter = create_adapter(
        "ollama", transport=httpx.MockTransport(handler("ollama", payload, []))
    )
    with pytest.raises(ProviderError) as error:
        await adapter.complete(config("ollama"), request(tools=True))
    assert error.value.code == "invalid_response"
