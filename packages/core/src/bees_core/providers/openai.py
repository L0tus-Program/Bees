"""Chat Completions compatível; estado fica no Bees e store é false."""

from typing import Any

from pydantic import ValidationError

from bees_core.providers.base import HTTPAdapter, decode_json, encode_json
from bees_core.providers.contracts import (
    ChatMessage,
    ChatRequest,
    ChatResponse,
    Diagnostic,
    ProviderConfig,
    ToolCall,
    Usage,
    snapshots,
    validate_calls,
    validate_request,
)
from bees_core.providers.errors import ProviderError


def wire_message(message: ChatMessage) -> dict[str, Any]:
    result: dict[str, Any] = {"role": message.role, "content": message.content}
    if message.tool_call_id is not None:
        result["tool_call_id"] = message.tool_call_id
    if message.tool_calls:
        result["tool_calls"] = [
            {
                "id": call.id,
                "type": "function",
                "function": {
                    "name": call.name,
                    "arguments": encode_json(call.arguments, 16777216).decode("utf-8"),
                },
            }
            for call in message.tool_calls
        ]
    return result


def wire_tools(request: ChatRequest) -> list[dict]:
    return [
        {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.parameters,
            },
        }
        for tool in request.tools
    ]


def reported_usage(value: dict | None) -> Usage:
    if value is None:
        return Usage()
    if not isinstance(value, dict):
        raise ValueError("Uso informado precisa ser objeto.")
    names = {
        "prompt_tokens": "input_tokens",
        "completion_tokens": "output_tokens",
        "total_tokens": "total_tokens",
    }
    counts = {target: value[source] for source, target in names.items() if source in value}
    return Usage(kind="reported", **counts) if counts else Usage()


class OpenAICompatibleAdapter(HTTPAdapter):
    async def complete(self, config: ProviderConfig, request: ChatRequest) -> ChatResponse:
        config, request = validate_request(config, request)
        if config.kind != "openai_compatible":
            raise ProviderError("invalid_config")
        body = {
            "model": config.model,
            "messages": [wire_message(msg) for msg in request.messages],
            "stream": False,
            "store": False,
        }
        if request.tools:
            body["tools"] = wire_tools(request)
        # Falhar tamanho antes de resolver credencial ou abrir conexão.
        encode_json(body, config.max_request_bytes)
        async with self._session(config) as client:
            self._authorize_generation()
            data = await self._json(client, config, "POST", "chat/completions", body=body)
        try:
            choices = data["choices"]
            if not isinstance(choices, list) or len(choices) != 1:
                raise ValueError()
            choice = choices[0]
            raw = choice["message"]
            if raw.get("role") != "assistant":
                raise ValueError()
            calls = []
            raw_calls = raw.get("tool_calls")
            if raw_calls is not None and (not isinstance(raw_calls, list) or len(raw_calls) > 64):
                raise ValueError()
            for value in raw_calls or []:
                if value.get("type") != "function" or not isinstance(
                    value["function"]["arguments"], str
                ):
                    raise ValueError()
                calls.append(
                    ToolCall(
                        id=value["id"],
                        name=value["function"]["name"],
                        arguments=decode_json(value["function"]["arguments"].encode("utf-8")),
                    )
                )
            validate_calls(calls, request.tools)
            old_ids = {call.id for item in request.messages for call in item.tool_calls}
            if any(call.id in old_ids for call in calls):
                raise ValueError()
            message = ChatMessage(role="assistant", content=raw.get("content"), tool_calls=calls)
            return ChatResponse(
                message=message,
                finish_reason=choice["finish_reason"],
                usage=reported_usage(data.get("usage")),
            )
        except KeyError, TypeError, ValueError, AttributeError, ValidationError:
            raise ProviderError("invalid_response") from None

    async def diagnose(self, config: ProviderConfig) -> Diagnostic:
        config, _ = snapshots(config)
        if config.kind != "openai_compatible":
            raise ProviderError("invalid_config")
        catalog = ProviderConfig.model_validate(
            config.model_dump() | {"max_response_bytes": config.max_catalog_bytes}
        )
        async with self._session(catalog) as client:
            data = await self._json(client, catalog, "GET", "models")
        models = data.get("data")
        if (
            not isinstance(models, list)
            or len(models) > 4096
            or not all(isinstance(item, dict) for item in models)
        ):
            raise ProviderError("invalid_response")
        if config.model not in {
            item.get("id") for item in models if isinstance(item.get("id"), str)
        }:
            raise ProviderError("model_unavailable")
        return Diagnostic(capabilities=config.capabilities)
