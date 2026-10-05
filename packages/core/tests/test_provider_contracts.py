import pytest
from pydantic import ValidationError

from bees_core.providers.contracts import (
    ChatMessage,
    ChatRequest,
    ProviderCapabilities,
    ProviderConfig,
    ToolCall,
    ToolDefinition,
    Usage,
    validate_calls,
    validate_capabilities,
    validate_request,
)
from bees_core.providers.errors import ProviderError


def configuration(**changes) -> ProviderConfig:
    return ProviderConfig.model_validate(
        {
            "kind": "openai_compatible",
            "endpoint": "https://models.example/v1",
            "model": "test-model",
            "capabilities": {"text": True, "tool_calls": True},
        }
        | changes
    )


def tool() -> ToolDefinition:
    return ToolDefinition(
        name="lookup",
        parameters={
            "type": "object",
            "properties": {"city": {"type": "string"}},
            "required": ["city"],
            "additionalProperties": False,
        },
    )


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://models.example/v1",
        "ftp://127.0.0.1",
        "https://user:secret@models.example/v1",
        "https://models.example/v1?key=secret",
        "https://models.example/v1#secret",
        "https://models.example/a/../v1",
        "https://models.example\\evil/v1",
    ],
)
def test_unsafe_endpoints_are_rejected(endpoint: str) -> None:
    with pytest.raises(ValidationError):
        configuration(endpoint=endpoint)


@pytest.mark.parametrize("endpoint", ["http://127.0.0.1:11434", "http://[::1]:11434"])
def test_explicit_loopback_http_is_supported(endpoint: str) -> None:
    assert configuration(endpoint=endpoint).endpoint == endpoint


@pytest.mark.parametrize(
    "changes",
    [
        {"endpoint": "https://ollama.com"},
        {"model": "test:cloud"},
        {"model": "test-cloud"},
        {"model": "model@remote"},
        {"secret_ref": "env:OLLAMA_API_KEY"},
    ],
)
def test_ollama_local_does_not_implicitly_enable_cloud(changes: dict) -> None:
    with pytest.raises(ValidationError):
        configuration(**({"kind": "ollama", "endpoint": "http://127.0.0.1:11434"} | changes))


def test_local_model_with_dot_is_valid() -> None:
    assert (
        configuration(kind="ollama", endpoint="http://127.0.0.1:11434", model="llama3.2").model
        == "llama3.2"
    )


@pytest.mark.parametrize(
    "schema",
    [
        {"type": "array"},
        {"type": "object", "$ref": "https://untrusted.example/schema"},
        {"type": "object", "$ref": "#/$defs/recursive"},
        {"type": "object", "properties": {"x": {"type": "string", "pattern": "(a+)+"}}},
        {"type": "object", "properties": {"x": {"type": "array", "uniqueItems": True}}},
        {"type": "object", "anyOf": [{"type": "object"}]},
        {"type": "object", "unevaluatedProperties": False},
        {"type": "object", "required": "x"},
    ],
)
def test_schemas_reject_external_resolution_and_unbounded_keywords(schema: dict) -> None:
    with pytest.raises(ValidationError):
        ToolDefinition(name="lookup", parameters=schema)


def test_tool_response_name_and_arguments_are_validated() -> None:
    validate_calls([ToolCall(id="call1", name="lookup", arguments={"city": "São Paulo"})], [tool()])
    for call in [
        ToolCall(id="call1", name="other", arguments={}),
        ToolCall(id="call1", name="lookup", arguments={"city": 2}),
        ToolCall(id="call1", name="lookup", arguments={"city": "X", "extra": 1}),
    ]:
        with pytest.raises(ProviderError) as error:
            validate_calls([call], [tool()])
        assert error.value.code == "invalid_response"


@pytest.mark.parametrize(
    "messages",
    [
        [{"role": "tool", "content": "result", "tool_call_id": "missing"}],
        [{"role": "user", "content": "message", "tool_call_id": "call1"}],
        [{"role": "assistant", "tool_calls": [{"id": "call1", "name": "lookup", "arguments": {}}]}],
        [
            {
                "role": "assistant",
                "tool_calls": [{"id": "call1", "name": "lookup", "arguments": {}}],
            },
            {"role": "tool", "content": "result", "tool_call_id": "call1"},
            {"role": "tool", "content": "duplicate", "tool_call_id": "call1"},
        ],
    ],
)
def test_history_rejects_orphan_duplicate_and_pending_results(messages: list) -> None:
    with pytest.raises(ValidationError):
        ChatRequest(messages=messages)


def test_completed_history_does_not_require_disabled_tool_definition() -> None:
    request = ChatRequest(
        messages=[
            ChatMessage(
                role="assistant", tool_calls=[ToolCall(id="call1", name="old_tool", arguments={})]
            ),
            ChatMessage(role="tool", tool_call_id="call1", content="saved result"),
            ChatMessage(role="user", content="Continue sem usar a ferramenta antiga"),
        ]
    )
    assert validate_request(configuration(), request)[1] == request


def test_snapshot_blocks_model_copy_bypass_and_mutable_capability_inputs() -> None:
    config = configuration()
    mutated = config.model_copy(update={"endpoint": "http://unsafe.example"})
    with pytest.raises(ProviderError):
        validate_capabilities(mutated)
    request = ChatRequest(messages=[ChatMessage(role="user", content="Olá")], tools=[tool()])
    copied_config, copied_request = validate_request(config, request)
    request.tools[0].parameters["type"] = "array"
    assert copied_request.tools[0].parameters["type"] == "object"
    assert copied_config.capabilities == ProviderCapabilities(text=True, tool_calls=True)
    with pytest.raises(ProviderError):
        validate_request(config, request)


def test_capability_and_streaming_incompatibility_are_explicit() -> None:
    config = configuration(capabilities={"text": True, "tool_calls": False})
    requests = [
        ChatRequest(messages=[ChatMessage(role="user", content="Olá")], tools=[tool()]),
        ChatRequest(messages=[ChatMessage(role="user", content="Olá")], stream=True),
    ]
    for request in requests:
        with pytest.raises(ProviderError) as error:
            validate_request(config, request)
        assert error.value.code == "unsupported_capability"


def test_usage_distinguishes_unknown_reported_and_estimated() -> None:
    assert Usage().kind == "unknown"
    assert (
        Usage(kind="reported", input_tokens=10, output_tokens=4, total_tokens=14).kind == "reported"
    )
    assert Usage(kind="estimated", input_tokens=10).kind == "estimated"
    for fields in [
        {"input_tokens": 0},
        {"kind": "reported"},
        {"kind": "reported", "input_tokens": True},
        {"kind": "reported", "input_tokens": 1, "output_tokens": 2, "total_tokens": 9},
    ]:
        with pytest.raises(ValidationError):
            Usage(**fields)
