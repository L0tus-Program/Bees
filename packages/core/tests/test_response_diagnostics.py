"""Classificação fechada de respostas incompatíveis, sem valores privados de validação."""

import asyncio
import json

import httpx
import pytest
from test_provider_adapters import Resolver, config, handler, request, text_response

from bees_core.providers.base import create_adapter
from bees_core.providers.errors import ProviderError


@pytest.mark.parametrize("kind", ["openai_compatible", "ollama"])
@pytest.mark.parametrize(
    "case,issue",
    [
        ("missing_message", "message"),
        ("no_content", "content"),
        ("tool_calls", "tool_calls"),
        ("arguments", "tool_arguments_json"),
        ("schema", "tool_arguments_schema"),
        ("not_offered", "tool_not_offered"),
        ("finish", "finish_reason"),
        ("usage", "usage"),
    ],
)
def test_response_issue_is_specific_and_never_contains_response_values(kind, case, issue):
    payload = text_response(kind)
    message = (
        payload["choices"][0]["message"] if kind == "openai_compatible" else payload["message"]
    )
    if case == "missing_message":
        (payload["choices"][0] if kind == "openai_compatible" else payload).pop("message")
    elif case == "no_content":
        message["content"] = None
    elif case == "tool_calls":
        message["tool_calls"] = {"PRIVATE": "raw"}
    elif case in {"arguments", "schema", "not_offered"}:
        function = {
            "name": "not_offered" if case == "not_offered" else "lookup",
            "arguments": {"city": 123} if case == "schema" else {"city": "PRIVATE"},
        }
        if kind == "openai_compatible":
            function["arguments"] = (
                "PRIVATE-invalid-json" if case == "arguments" else json.dumps(function["arguments"])
            )
            payload["choices"][0]["finish_reason"] = "tool_calls"
        elif case == "arguments":
            function["arguments"] = "PRIVATE-invalid-object"
        message["tool_calls"] = [{"id": "call-1", "type": "function", "function": function}]
    elif case == "finish":
        if kind == "openai_compatible":
            payload["choices"][0]["finish_reason"] = "PRIVATE"
        else:
            payload["done_reason"] = "PRIVATE"
    elif case == "usage":
        if kind == "openai_compatible":
            payload["usage"] = {"prompt_tokens": -1}
        else:
            payload["prompt_eval_count"] = -1
    seen = []
    adapter = create_adapter(kind, transport=httpx.MockTransport(handler(kind, payload, seen)))
    with pytest.raises(ProviderError) as error:
        asyncio.run(adapter.complete(config(kind), request(tools=True)))
    assert error.value.code == "invalid_response" and error.value.response_issue == issue
    assert "PRIVATE" not in str(error.value) and error.value.provider_message is None


def test_http_success_error_redacts_actual_key_and_reflected_request():
    resolver = Resolver()
    selected = config("openai_compatible", secret_ref="env:FIXTURE_KEY")
    prompt = request()

    def respond(incoming):
        secret = incoming.headers["Authorization"].removeprefix("Bearer ")
        return httpx.Response(
            200, json={"error": {"message": f"Failure: {secret} {prompt.messages[0].content}"}}
        )

    adapter = create_adapter("openai_compatible", resolver, transport=httpx.MockTransport(respond))
    with pytest.raises(ProviderError) as error:
        asyncio.run(adapter.complete(selected, prompt))
    assert error.value.response_issue == "error_envelope"
    assert error.value.upstream_status is None and not error.value.undelivered
    assert prompt.messages[0].content not in error.value.provider_message
    assert "TEST-SECRET" not in error.value.provider_message


@pytest.mark.parametrize("reason", ["PRIVATE", ["PRIVATE"]])
def test_diagnostic_does_not_accept_arbitrary_reason(reason):
    with pytest.raises(ValueError):
        ProviderError("invalid_response", response_issue=reason)
