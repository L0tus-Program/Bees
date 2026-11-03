"""Responses stateless: texto, funções e raciocínio cifrado, sem executar ferramentas."""

import hashlib
import re

from pydantic import ValidationError

from bees_core.providers.base import HTTPAdapter, decode_json, encode_json
from bees_core.providers.contracts import (
    ChatMessage,
    ChatRequest,
    ChatResponse,
    ProviderConfig,
    ResponseFunctionCall,
    ResponseMessage,
    ResponseReasoning,
    ResponsesState,
    ResponseText,
    ToolCall,
    Usage,
    validate_calls,
    validate_request,
)
from bees_core.providers.errors import ProviderError


def uses_responses(config: ProviderConfig) -> bool:
    # Seleção antes do despacho. Nunca descobrir o protocolo repetindo uma geração.
    return config.api == "responses" or (
        config.api == "auto"
        and re.fullmatch(r"gpt-5\.6(?:-[a-zA-Z0-9_.-]+)?", config.model) is not None
    )


def binding(config: ProviderConfig) -> str:
    # Estado cifrado só volta à mesma conexão, modelo e referência de credencial.
    return hashlib.sha256(
        encode_json([config.endpoint, config.model, config.secret_ref], 16384)
    ).hexdigest()


def wire_input(config: ProviderConfig, request: ChatRequest) -> list[dict]:
    items = []
    for message in request.messages:
        if message.responses_state and message.responses_state.binding == binding(config):
            for item in message.responses_state.items:
                value = item.model_dump(mode="json", exclude_none=True)
                if isinstance(item, ResponseMessage):
                    for part in value["content"]:
                        if part["type"] == "refusal":
                            part.pop("annotations", None)
                items.append(value)
            continue
        if message.role == "tool":
            items.append(
                {
                    "type": "function_call_output",
                    "call_id": message.tool_call_id,
                    "output": message.content,
                }
            )
            continue
        if message.content is not None:
            content = [{"type": "input_text", "text": message.content}]
            if message.role == "assistant":
                content = [{"type": "output_text", "text": message.content, "annotations": []}]
            content.extend(
                {"type": "input_image", "image_url": f"data:{image.media_type};base64,{image.data}"}
                for image in message.images
            )
            items.append({"role": message.role, "content": content})
        items.extend(
            {
                "type": "function_call",
                "call_id": call.id,
                "name": call.name,
                "arguments": encode_json(call.arguments, config.max_request_bytes).decode("utf-8"),
            }
            for call in message.tool_calls
        )
    return items


class ResponsesAdapter(HTTPAdapter):
    async def complete(self, config: ProviderConfig, request: ChatRequest) -> ChatResponse:
        config, request = validate_request(config, request)
        if config.kind != "openai_compatible":
            raise ProviderError("invalid_config")
        body = {
            "model": config.model,
            "input": wire_input(config, request),
            "store": False,
            "stream": False,
            "include": ["reasoning.encrypted_content"],
        }
        if request.tools:
            body["tools"] = [
                {
                    "type": "function",
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.parameters,
                    "strict": False,
                }
                for tool in request.tools
            ]
        encode_json(body, config.max_request_bytes)
        async with self._session(config) as client:
            self._authorize_generation()
            data = await self._json(client, config, "POST", "responses", body=body)
        return self._response(config, request, data)

    def _response(self, config: ProviderConfig, request: ChatRequest, data: dict) -> ChatResponse:
        issue = "response_output"
        try:
            output = data["output"]
            if not isinstance(output, list) or not 1 <= len(output) <= 256:
                raise ValueError()
            calls, text, items = [], [], []
            refused = False
            for value in output:
                issue = "response_output"
                kind = value["type"]
                if kind == "reasoning":
                    issue = "reasoning_state"
                    items.append(
                        ResponseReasoning(
                            id=value["id"], encrypted_content=value["encrypted_content"]
                        )
                    )
                elif kind == "message":
                    issue = "message"
                    if value["role"] != "assistant" or value.get("status", "completed") not in (
                        {"completed", "incomplete"}
                        if data.get("status") == "incomplete"
                        else {"completed"}
                    ):
                        raise ValueError()
                    parts = value["content"]
                    if not isinstance(parts, list) or not 1 <= len(parts) <= 256:
                        raise ValueError()
                    content = []
                    for part in parts:
                        issue = "content"
                        if part["type"] == "output_text":
                            normalized = ResponseText(type="output_text", text=part["text"])
                            if not isinstance(normalized.text, str):
                                raise ValueError()
                            text.append(normalized.text)
                        elif part["type"] == "refusal":
                            normalized = ResponseText(type="refusal", refusal=part["refusal"])
                            if not isinstance(normalized.refusal, str):
                                raise ValueError()
                            text.append(normalized.refusal)
                            refused = True
                        else:
                            raise ValueError()
                        content.append(normalized)
                    issue = "message"
                    items.append(ResponseMessage(content=content, phase=value.get("phase")))
                elif kind == "function_call":
                    issue = "tool_call"
                    if value.get("status", "completed") != "completed" or not isinstance(
                        value["arguments"], str
                    ):
                        raise ValueError()
                    issue = "tool_arguments_json"
                    arguments = decode_json(value["arguments"].encode("utf-8"))
                    issue = "tool_call"
                    call = ToolCall(id=value["call_id"], name=value["name"], arguments=arguments)
                    calls.append(call)
                    items.append(
                        ResponseFunctionCall(
                            call_id=call.id,
                            name=call.name,
                            arguments=encode_json(arguments, config.max_response_bytes).decode(
                                "utf-8"
                            ),
                        )
                    )
                else:
                    raise ValueError()
            issue = "tool_calls"
            if len(calls) > 64:
                raise ValueError()
            validate_calls(calls, request.tools, proposals=self._tool_proposals)
            issue = "duplicate_tool_call"
            old_ids = {call.id for message in request.messages for call in message.tool_calls}
            if len({call.id for call in calls}) != len(calls) or any(
                call.id in old_ids for call in calls
            ):
                raise ValueError()
            issue = "finish_reason"
            status = data["status"]
            finish = "tool_calls" if calls else "content_filter" if refused else "stop"
            if refused and calls:
                raise ValueError()
            if status == "incomplete" and not calls:
                reason = (data.get("incomplete_details") or {}).get("reason")
                if reason not in {"max_output_tokens", "content_filter"}:
                    raise ValueError()
                finish = "length" if reason == "max_output_tokens" else "content_filter"
            elif status != "completed":
                raise ValueError()
            issue = "usage"
            raw_usage = data.get("usage")
            usage = Usage()
            if raw_usage is not None:
                if not isinstance(raw_usage, dict):
                    raise ValueError()
                counts = {
                    key: raw_usage[key]
                    for key in ("input_tokens", "output_tokens", "total_tokens")
                    if key in raw_usage
                }
                if counts:
                    usage = Usage(kind="reported", **counts)
            issue = "content"
            message = ChatMessage(
                role="assistant",
                content="\n".join(text) if text else None,
                tool_calls=calls,
                responses_state=ResponsesState(binding=binding(config), items=items),
            )
            return ChatResponse(message=message, finish_reason=finish, usage=usage)
        except ProviderError as error:
            if error.code == "invalid_response" and issue == "tool_arguments_json":
                raise ProviderError("invalid_response", response_issue=issue) from None
            raise
        except KeyError, TypeError, ValueError, AttributeError, ValidationError:
            raise ProviderError("invalid_response", response_issue=issue) from None
