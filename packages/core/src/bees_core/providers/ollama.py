"""API nativa Ollama: confirma modelo local antes de enviar qualquer histórico."""

from typing import Any
from uuid import uuid4

import httpx
from pydantic import ValidationError

from bees_core.providers.base import HTTPAdapter, encode_json
from bees_core.providers.contracts import (
    ChatRequest,
    ChatResponse,
    Diagnostic,
    ProviderCapabilities,
    ProviderConfig,
    ToolCall,
    Usage,
    snapshots,
    validate_calls,
    validate_request,
)
from bees_core.providers.errors import ProviderError
from bees_core.providers.openai import wire_tools


def canonical_model(value: str) -> str:
    return value if ":" in value.rsplit("/", 1)[-1] else f"{value}:latest"


def wire_messages(request: ChatRequest) -> list[dict[str, Any]]:
    result = []
    index = 0
    while index < len(request.messages):
        message = request.messages[index]
        value: dict[str, Any] = {"role": message.role, "content": message.content or ""}
        if message.tool_calls:
            value["tool_calls"] = [
                {
                    "type": "function",
                    "function": {
                        "index": index,
                        "name": call.name,
                        "arguments": call.arguments,
                    },
                }
                for index, call in enumerate(message.tool_calls)
            ]
        result.append(value)
        if message.tool_calls:
            # Normalizar o bloco no wire sem editar o histórico canônico do Bees.
            block = request.messages[index + 1 : index + 1 + len(message.tool_calls)]
            outputs = {output.tool_call_id: output for output in block}
            for call in message.tool_calls:
                output = outputs[call.id]
                result.append({"role": "tool", "tool_name": call.name, "content": output.content})
            index += len(message.tool_calls)
        index += 1
    return result


class OllamaAdapter(HTTPAdapter):
    async def _local_model(
        self, client: httpx.AsyncClient, config: ProviderConfig
    ) -> ProviderCapabilities:
        tags = await self._json(client, config, "GET", "api/tags")
        models = tags.get("models")
        if not isinstance(models, list) or not all(isinstance(item, dict) for item in models):
            raise ProviderError("invalid_response")
        selected = [
            item
            for item in models
            if isinstance(item.get("name"), str)
            and canonical_model(item["name"]) == canonical_model(config.model)
        ]
        if len(selected) != 1:
            raise ProviderError("model_unavailable")
        if any(selected[0].get(key) not in (None, "") for key in ("remote_host", "remote_model")):
            raise ProviderError("local_model_required")
        show = await self._json(client, config, "POST", "api/show", body={"model": config.model})
        if any(show.get(key) not in (None, "") for key in ("remote_host", "remote_model")):
            raise ProviderError("local_model_required")
        capabilities = show.get("capabilities")
        if not isinstance(capabilities, list) or not all(
            isinstance(value, str) for value in capabilities
        ):
            raise ProviderError("invalid_response")
        actual = ProviderCapabilities(
            text="completion" in capabilities, tool_calls="tools" in capabilities
        )
        if not actual.text or (config.capabilities.tool_calls and not actual.tool_calls):
            raise ProviderError("unsupported_capability")
        return actual

    async def complete(self, config: ProviderConfig, request: ChatRequest) -> ChatResponse:
        config, request = validate_request(config, request)
        if config.kind != "ollama":
            raise ProviderError("invalid_config")
        body = {
            "model": config.model,
            "messages": wire_messages(request),
            "stream": False,
            "think": False,
        }
        if request.tools:
            body["tools"] = wire_tools(request)
        encode_json(body, config.max_request_bytes)
        async with self._session(config) as client:
            await self._local_model(client, config)
            data = await self._json(client, config, "POST", "api/chat", body=body)
        try:
            if data.get("done") is not True or data["message"].get("role") != "assistant":
                raise ValueError()
            raw = data["message"]
            if raw.get("thinking"):
                raise ProviderError("unsupported_capability")
            calls = []
            raw_calls = raw.get("tool_calls")
            if raw_calls is not None and (not isinstance(raw_calls, list) or len(raw_calls) > 64):
                raise ValueError()
            for value in raw_calls or []:
                function = value["function"]
                calls.append(
                    ToolCall(
                        id=f"ollama_{uuid4().hex}",
                        name=function["name"],
                        arguments=function["arguments"],
                    )
                )
            validate_calls(calls, request.tools)
            done_reason = data.get("done_reason", "stop")
            if calls and done_reason not in ("stop", "tool_calls"):
                # Não promover uma saída truncada a chamada completa de ferramenta.
                raise ValueError()
            counts = {}
            if "prompt_eval_count" in data:
                counts["input_tokens"] = data["prompt_eval_count"]
            if "eval_count" in data:
                counts["output_tokens"] = data["eval_count"]
            if "input_tokens" in counts and "output_tokens" in counts:
                counts["total_tokens"] = counts["input_tokens"] + counts["output_tokens"]
            usage = Usage(kind="reported", **counts) if counts else Usage()
            return ChatResponse(
                message={"role": "assistant", "content": raw.get("content"), "tool_calls": calls},
                finish_reason="tool_calls" if calls else done_reason,
                usage=usage,
            )
        except KeyError, TypeError, ValueError, AttributeError, ValidationError:
            raise ProviderError("invalid_response") from None

    async def diagnose(self, config: ProviderConfig) -> Diagnostic:
        config, _ = snapshots(config)
        if config.kind != "ollama":
            raise ProviderError("invalid_config")
        async with self._session(config) as client:
            actual = await self._local_model(client, config)
        return Diagnostic(
            capabilities=actual, message="Modelo local presente; capacidades consultadas no daemon."
        )
