"""Fluxo HTTP real de loopback; o servidor é um dublê de protocolo, não um modelo."""

import asyncio
import json
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from bees_core.models import Agent, Conversation, Memory, Task
from bees_core.providers.contracts import ChatMessage, ProviderConfig, ToolDefinition
from bees_core.providers.service import ProviderService
from bees_core.storage.database import Database
from bees_core.storage.store import StateStore


@contextmanager
def protocol_server():
    received = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _respond(self, value):
            body = json.dumps(value).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            received.append((self.path, None))
            if self.path == "/api/tags":
                self._respond(
                    {"models": [{"name": "wire-model:latest", "model": "wire-model:latest"}]}
                )
            elif self.path == "/v1/models":
                self._respond({"data": [{"id": "wire-model"}]})
            else:
                self.send_error(404)

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            received.append((self.path, body))
            if self.path == "/api/show":
                self._respond({"capabilities": ["completion", "tools"]})
                return
            assert self.path in ("/api/chat", "/v1/chat/completions")
            messages = body["messages"]
            result = next(
                (message for message in reversed(messages) if message["role"] == "tool"), None
            )
            local = self.path == "/api/chat"
            if result is None:
                function = {"name": "lookup", "arguments": {"topic": "Bees"}}
                if not local:
                    function["arguments"] = json.dumps(function["arguments"])
                call = {"function": function}
                if not local:
                    call.update(id="wire_call_1", type="function")
                message = {"role": "assistant", "content": "", "tool_calls": [call]}
                finish = "tool_calls"
            else:
                if local:
                    assert result["tool_name"] == "lookup"
                else:
                    assert result["tool_call_id"]
                assert result["content"] == "Dado de teste, sem efeito externo"
                message = {"role": "assistant", "content": "Resultado recebido."}
                finish = "stop"
            if local:
                self._respond(
                    {
                        "model": body["model"],
                        "message": message,
                        "done": True,
                        "done_reason": "stop",
                        "prompt_eval_count": 10,
                        "eval_count": 3,
                    }
                )
            else:
                self._respond(
                    {
                        "model": body["model"],
                        "choices": [{"index": 0, "message": message, "finish_reason": finish}],
                        "usage": {"prompt_tokens": 10, "completion_tokens": 3, "total_tokens": 13},
                    }
                )

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", received
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def config(kind, endpoint):
    return ProviderConfig(
        kind=kind,
        endpoint=endpoint + ("/v1" if kind == "openai_compatible" else ""),
        model="wire-model" if kind == "openai_compatible" else "wire-model:latest",
        capabilities={"text": True, "tool_calls": True},
    )


@pytest.mark.parametrize("first_kind", ["openai_compatible", "ollama"])
def test_real_http_tool_roundtrip_switch_and_reopen(tmp_path, monkeypatch, first_kind):
    # Um proxy herdado não deve mudar o destino nem interceptar dados.
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("ALL_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("NO_PROXY", "")
    database = Database(tmp_path / "state.sqlite3")
    database.initialize()
    tool = ToolDefinition(
        name="lookup",
        description="Consulta de teste",
        parameters={
            "type": "object",
            "properties": {"topic": {"type": "string"}},
            "required": ["topic"],
            "additionalProperties": False,
        },
    )
    with protocol_server() as (endpoint, received):
        agent = Agent(
            name="Abelha",
            instructions="Instrução de teste.",
            provider_config=config(first_kind, endpoint).model_dump(mode="json"),
        )
        conversation = Conversation(agent_id=agent.id)
        memory = Memory(
            agent_id=agent.id,
            scope="agent",
            content="Memória não solicitada",
            source="external_unconfirmed",
        )
        preference = Memory(
            agent_id=agent.id, scope="agent", content="Prefiro respostas em português."
        )
        task = Task(agent_id=agent.id, title="Tarefa preservada", objective="Objetivo preservado")
        with StateStore(database).transaction() as unit:
            unit.agents.create(agent)
            unit.conversations.create(conversation)
            unit.memories.create(memory)
            unit.memories.create(preference)
            unit.tasks.create(task)
        service = ProviderService(database)

        async def flow():
            first = await service.chat(agent.id, conversation.id, "Consulte Bees", tools=[tool])
            assert first.message.tool_calls[0].arguments == {"topic": "Bees"}
            result = ChatMessage(
                role="tool",
                tool_call_id=first.message.tool_calls[0].id,
                content="Dado de teste, sem efeito externo",
            )
            final = await service.chat(agent.id, conversation.id, result, tools=[tool])
            assert final.message.content == "Resultado recebido."
            assert final.usage.kind == "reported"
            assert final.usage.total_tokens == 13
            other = "ollama" if first_kind == "openai_compatible" else "openai_compatible"
            service.set_config(agent.id, config(other, endpoint), expected_revision=1)
            continued = await service.chat(agent.id, conversation.id, "Continue", tools=[tool])
            assert continued.message.content == "Resultado recebido."

        asyncio.run(flow())
        chats = [body for path, body in received if path in ("/api/chat", "/v1/chat/completions")]
        assert len(chats) == 3
        assert chats[0]["messages"][0]["content"] == agent.instructions
        assert "Memória não solicitada" not in json.dumps(chats, ensure_ascii=False)
        assert all(preference.content in json.dumps(body, ensure_ascii=False) for body in chats)
        assert all(body["stream"] is False for body in chats)
    reopened = StateStore(Database(database.path))
    with reopened.transaction(write=False) as unit:
        assert unit.agents.get(agent.id).id == agent.id
        assert unit.agents.get(agent.id).revision == 2
        assert unit.memories.get(memory.id) == memory
        assert unit.memories.get(preference.id) == preference
        assert unit.tasks.get(task.id) == task
        messages = unit.messages.list(conversation_id=conversation.id)
        assert len(messages) == 6
        assert messages[-1].content == "Resultado recebido."
