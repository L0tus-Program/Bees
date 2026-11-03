"""Responses e regressões dos provedores: transporte controlado, sem gerações pagas."""

import asyncio
import json

import httpx
import pytest
from pydantic import ValidationError
from test_agent_tools import Case
from test_image_providers import image
from test_provider_adapters import Resolver, config, request, text_response
from test_provider_service import fixture_state, remote_config, service

from bees_core.agent_tools.cycle import AgentTaskWorker
from bees_core.providers.base import create_adapter
from bees_core.providers.contracts import ChatMessage, ChatRequest, ProviderConfig
from bees_core.providers.errors import ProviderError
from bees_core.storage.database import Database


def output(*items, **changes):
    return {
        "status": "completed",
        "output": list(items),
        "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
    } | changes


def text(value="Resposta", phase="final_answer"):
    return {
        "type": "message",
        "role": "assistant",
        "status": "completed",
        "phase": phase,
        "content": [{"type": "output_text", "text": value, "annotations": []}],
    }


def reasoning(number=1):
    return {
        "type": "reasoning",
        "id": f"rs_{number}",
        "encrypted_content": f"OPAQUE_{number}",
        "summary": [{"type": "summary_text", "text": "PRIVATE SUMMARY"}],
    }


def function(number=1, **changes):
    return {
        "type": "function_call",
        "id": f"fc_{number}",
        "call_id": f"call_{number}",
        "name": "lookup",
        "arguments": '{"city":"São Paulo"}',
        "status": "completed",
    } | changes


@pytest.mark.parametrize(
    "model,api,route",
    [
        ("gpt-5.6-terra", "auto", "/v1/responses"),
        ("gpt-5.6-sol", "auto", "/v1/responses"),
        ("gpt-5.6-luna-2026-10-01", "auto", "/v1/responses"),
        ("gpt-5.6", "auto", "/v1/responses"),
        ("gpt-5.5", "auto", "/v1/chat/completions"),
        ("gpt-5.4-mini", "auto", "/v1/chat/completions"),
        ("gpt-5.60", "auto", "/v1/chat/completions"),
        ("other-provider/gpt-5.6-terra", "auto", "/v1/chat/completions"),
        ("other-model", "auto", "/v1/chat/completions"),
        ("other-model", "responses", "/v1/responses"),
        ("gpt-5.6-terra", "chat_completions", "/v1/chat/completions"),
    ],
)
def test_api_selection_before_dispatch_preserves_other_models(model, api, route):
    seen, guards = [], []

    def respond(incoming):
        seen.append(incoming)
        return httpx.Response(
            200,
            json=output(text())
            if route.endswith("/responses")
            else text_response("openai_compatible"),
        )

    adapter = create_adapter(
        "openai_compatible",
        transport=httpx.MockTransport(respond),
        before_generation=lambda: guards.append(True),
    )
    response = asyncio.run(
        adapter.complete(config("openai_compatible", model=model, api=api), request(tools=True))
    )
    assert len(seen) == len(guards) == 1 and seen[0].url.path == route
    assert response.usage.total_tokens == 15
    body = json.loads(seen[0].content)
    assert body["store"] is False and body["stream"] is False
    assert "reasoning_effort" not in body and "reasoning" not in body
    assert (
        body["tools"][0].get("strict", body["tools"][0].get("function", {}).get("strict")) is False
    )
    assert ("messages" in body) == route.endswith("/chat/completions")


def test_stateless_parallel_tools_and_reasoning_survive_service_restart(tmp_path):
    selected = ProviderConfig.model_validate(
        remote_config(tool_calls=True).model_dump() | {"model": "gpt-5.6-terra"}
    )
    database, _, agent, conversation = fixture_state(tmp_path, config=selected)
    seen = []

    def respond(incoming):
        body = json.loads(incoming.content)
        seen.append(body)
        assert incoming.url.path == "/v1/responses"
        if len(seen) == 1:
            return httpx.Response(
                200,
                json=output(
                    reasoning() | {"encrypted_content": "OPAQUE_1" * 5000},
                    text("Vou consultar.", "commentary"),
                    function(),
                    function(2),
                ),
            )
        return httpx.Response(200, json=output(reasoning(2), text("Concluído")))

    tools = request(tools=True).tools
    response = asyncio.run(
        service(database, respond).chat(agent.id, conversation.id, "Pesquise", tools=tools)
    )
    assert [call.id for call in response.message.tool_calls] == ["call_1", "call_2"]
    restarted = service(Database(database.path), respond)
    history = restarted.load_history(agent.id, conversation.id)
    assert history[-1].responses_state == response.message.responses_state
    result = asyncio.run(
        restarted.chat(
            agent.id,
            conversation.id,
            [
                ChatMessage(role="tool", tool_call_id="call_2", content="Resultado B"),
                ChatMessage(role="tool", tool_call_id="call_1", content="Resultado A"),
            ],
            tools=tools,
        )
    )
    assert result.message.content == "Concluído" and len(seen) == 2
    items = seen[-1]["input"]
    assert [item.get("type", "message") for item in items[-6:]] == [
        "reasoning",
        "message",
        "function_call",
        "function_call",
        "function_call_output",
        "function_call_output",
    ]
    assert items[-6]["encrypted_content"] == "OPAQUE_1" * 5000
    assert items[-5]["phase"] == "commentary"
    assert {item["call_id"]: item["output"] for item in items[-2:]} == {
        "call_1": "Resultado A",
        "call_2": "Resultado B",
    }
    assert "PRIVATE SUMMARY" not in json.dumps(seen)
    assert all(
        not any(key in body for key in ("previous_response_id", "conversation")) for body in seen
    )


@pytest.mark.parametrize(
    "change",
    [{"endpoint": "https://other.example/v1"}, {"model": "other"}, {"secret_ref": "env:OTHER"}],
)
def test_encrypted_state_is_not_sent_to_another_connection_or_model(change):
    seen = []
    adapter = create_adapter(
        "openai_compatible",
        Resolver(),
        transport=httpx.MockTransport(
            lambda incoming: (
                seen.append(incoming) or httpx.Response(200, json=output(reasoning(), text()))
            )
        ),
    )
    original = config("openai_compatible", api="responses")
    response = asyncio.run(adapter.complete(original, request()))
    follow = ChatRequest(
        messages=[*request().messages, response.message, ChatMessage(role="user", content="Siga")]
    )
    asyncio.run(
        adapter.complete(ProviderConfig.model_validate(original.model_dump() | change), follow)
    )
    assert "OPAQUE" not in seen[-1].content.decode()
    assert "Resposta" in seen[-1].content.decode()


@pytest.mark.parametrize("code", [400, 404, 429, 500])
def test_rejections_do_not_retry_or_fallback(code):
    seen = []

    def respond(incoming):
        seen.append(incoming)
        return httpx.Response(
            code,
            json={"error": {"message": "Rejected Bearer PRIVATE https://private.invalid/token"}},
        )

    adapter = create_adapter("openai_compatible", transport=httpx.MockTransport(respond))
    with pytest.raises(ProviderError) as error:
        asyncio.run(
            adapter.complete(
                config("openai_compatible", model="gpt-5.6-terra"), request(tools=True)
            )
        )
    assert len(seen) == 1 and seen[0].url.path == "/v1/responses"
    assert error.value.upstream_status == code
    assert (
        "PRIVATE" not in error.value.provider_message
        and "private.invalid" not in error.value.provider_message
    )


@pytest.mark.parametrize(
    "payload,issue",
    [
        ({"status": "completed", "output": []}, "response_output"),
        (output({"type": "web_search_call"}), "response_output"),
        (output({"type": "reasoning", "id": "rs_1"}, function()), "reasoning_state"),
        (output(function(arguments="PRIVATE-invalid-json")), "tool_arguments_json"),
        (output(function(arguments='{"city":123}')), "tool_arguments_schema"),
        (output(function(name="unknown")), "tool_not_offered"),
        (output(function(), function()), "duplicate_tool_call"),
        (
            output(
                function(), status="incomplete", incomplete_details={"reason": "max_output_tokens"}
            ),
            "finish_reason",
        ),
        (output(text(), status="in_progress"), "finish_reason"),
        (output(text(), usage={"input_tokens": -1}), "usage"),
        (output(text(), usage={"input_tokens": 1, "output_tokens": 1, "total_tokens": 4}), "usage"),
    ],
)
def test_invalid_responses_have_closed_diagnosis_and_never_execute(payload, issue):
    seen = []
    adapter = create_adapter(
        "openai_compatible",
        transport=httpx.MockTransport(
            lambda incoming: seen.append(incoming) or httpx.Response(200, json=payload)
        ),
    )
    with pytest.raises(ProviderError) as error:
        asyncio.run(
            adapter.complete(config("openai_compatible", api="responses"), request(tools=True))
        )
    assert error.value.code == "invalid_response" and error.value.response_issue == issue
    assert len(seen) == 1 and "PRIVATE" not in str(error.value)


def test_responses_images_and_local_capability_guard():
    picture = image()
    prompt = ChatRequest(messages=[ChatMessage(role="user", content="Descreva", images=[picture])])
    seen = []
    adapter = create_adapter(
        "openai_compatible",
        transport=httpx.MockTransport(
            lambda incoming: seen.append(incoming) or httpx.Response(200, json=output(text()))
        ),
    )
    with pytest.raises(ProviderError) as error:
        asyncio.run(adapter.complete(config("openai_compatible", api="responses"), prompt))
    assert error.value.code == "image_vision_required" and not seen
    asyncio.run(
        adapter.complete(
            config("openai_compatible", api="responses", capabilities={"vision": True}), prompt
        )
    )
    assert json.loads(seen[-1].content)["input"][0]["content"][1] == {
        "type": "input_image",
        "image_url": f"data:image/png;base64,{picture.data}",
    }


def test_refusal_partial_text_and_unknown_usage():
    def complete(payload):
        adapter = create_adapter(
            "openai_compatible",
            transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload)),
        )
        return asyncio.run(
            adapter.complete(config("openai_compatible", api="responses"), request())
        )

    message = text()
    message["content"] = [{"type": "refusal", "refusal": "Pedido recusado"}]
    refused = complete(output(message, usage=None))
    assert refused.finish_reason == "content_filter" and refused.usage.kind == "unknown"
    message = text("Parcial") | {"status": "incomplete"}
    partial = complete(
        output(message, status="incomplete", incomplete_details={"reason": "max_output_tokens"})
    )
    assert partial.finish_reason == "length" and partial.message.content == "Parcial"


def test_old_configs_are_unchanged_and_ollama_cannot_select_openai_api():
    original = config("openai_compatible").model_dump(mode="json")
    assert "api" not in original
    assert ProviderConfig.model_validate(original).api == "auto"
    with pytest.raises(ValidationError):
        config("ollama", api="responses")


def test_responses_error_redacts_encrypted_state_and_tool_results():
    seen = []

    def respond(incoming):
        seen.append(incoming)
        if len(seen) == 1:
            return httpx.Response(200, json=output(reasoning(), function()))
        return httpx.Response(
            400,
            json={
                "error": {
                    "param": "tools[0].parameters",
                    "message": "Rejected OPAQUE_1 PRIVATE_TOOL_RESULT",
                }
            },
        )

    adapter = create_adapter("openai_compatible", transport=httpx.MockTransport(respond))
    selected = config("openai_compatible", api="responses")
    prompt = request(tools=True)
    first = asyncio.run(adapter.complete(selected, prompt))
    follow = ChatRequest(
        messages=[
            *prompt.messages,
            first.message,
            ChatMessage(role="tool", tool_call_id="call_1", content="PRIVATE_TOOL_RESULT"),
        ],
        tools=prompt.tools,
    )
    with pytest.raises(ProviderError) as error:
        asyncio.run(adapter.complete(selected, follow))
    assert error.value.code == "tool_schema_rejected"
    assert error.value.parameter == "tools.parameters"
    assert "OPAQUE_1" not in error.value.provider_message
    assert "PRIVATE_TOOL_RESULT" not in error.value.provider_message
    assert len(seen) == 2


def test_responses_state_must_match_canonical_message():
    adapter = create_adapter(
        "openai_compatible",
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=output(text()))),
    )
    first = asyncio.run(adapter.complete(config("openai_compatible", api="responses"), request()))
    raw = first.message.model_dump(mode="json")
    raw["responses_state"]["items"][0]["content"][0]["text"] = "DIFFERENT"
    with pytest.raises(ValidationError):
        ChatMessage.model_validate(raw)

    adapter = create_adapter(
        "openai_compatible",
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=output(function()))),
    )
    first = asyncio.run(
        adapter.complete(config("openai_compatible", api="responses"), request(tools=True))
    )
    raw = first.message.model_dump(mode="json")
    raw["responses_state"]["items"][0]["arguments"] = "[" * 2000 + "0" + "]" * 2000
    with pytest.raises(ValidationError):
        ChatMessage.model_validate(raw)


def test_agent_worker_resumes_saved_responses_tool_call_without_repeating_model_or_effect(tmp_path):
    case = Case(
        tmp_path,
        [
            httpx.Response(
                200,
                json=output(
                    reasoning(),
                    function(name="web_fetch", arguments='{"url":"https://example.org/abelhas"}'),
                ),
            ),
            httpx.Response(200, json=output(reasoning(2), text("Pesquisa concluída"))),
        ],
        model="gpt-5.6-terra",
    )
    assert case.work()  # Resposta e estado cifrado persistidos antes da ferramenta.

    def respond(incoming):
        assert incoming.url.path == "/v1/responses"
        case.requests.append(json.loads(incoming.content))
        return case.script.pop(0)

    case.worker = AgentTaskWorker(
        Database(case.database.path),
        transport=httpx.MockTransport(respond),
        web=case.web,
        artifacts=case.artifacts,
    )
    case.drain()
    detail = case.detail()
    assert detail.task.status == "completed" and detail.task.calls_started == 2
    assert len(case.requests) == 2 and len(case.results()) == 1
    items = case.requests[-1]["input"]
    assert (
        next(item for item in items if item.get("type") == "reasoning")["encrypted_content"]
        == "OPAQUE_1"
    )
    assert len([item for item in items if item.get("type") == "function_call_output"]) == 1
    assert (
        len(
            [
                action
                for action in detail.actions
                if action.tool_name == "web.fetch" and action.status == "confirmed"
            ]
        )
        == 1
    )
