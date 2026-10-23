"""Canais Telegram/Discord: pareamento, deduplicação, modos e encaminhamento de respostas."""

import asyncio
import json
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr
from test_agent_tools import PAGE, public, tool_calls
from test_tasks_execution import answer, fixture_state

from bees_core.agent_tools.cycle import AgentTaskWorker
from bees_core.agent_tools.web import WebClient
from bees_core.channels.adapters import (
    ChannelError,
    DiscordAdapter,
    Identity,
    TelegramAdapter,
    split_text,
)
from bees_core.channels.service import (
    ChannelService,
    ChannelWorker,
    CreateChannel,
    RevisionInput,
    UpdateChannel,
    normalize_code,
)
from bees_core.providers.contracts import ProviderConfig
from bees_core.tools import ToolService

TOKEN = "123456:ABCDEFGHIJKLMNOPQRSTUVWXYZ"


class Resolver:
    def resolve(self, ref):
        assert ref == "vault:fixture"
        return SecretStr(TOKEN)


class Telegram:
    def __init__(self):
        self.updates = []
        self.sent = []
        self.status = 200
        self.next_id = 100

    def message(self, text, chat=42, name="Ana", kind="private"):
        self.next_id += 1
        self.updates.append(
            {
                "update_id": self.next_id,
                "message": {
                    "message_id": self.next_id,
                    "chat": {"id": chat, "type": kind},
                    "from": {"id": chat, "first_name": name},
                    "text": text,
                },
            }
        )

    def __call__(self, request: httpx.Request):
        assert request.url.host == "api.telegram.org"
        assert request.url.path.startswith(f"/bot{TOKEN}/")
        if self.status != 200:
            return httpx.Response(self.status, json={"ok": False})
        method = request.url.path.rsplit("/", 1)[-1]
        body = json.loads(request.content or b"{}")
        if method == "getMe":
            return httpx.Response(
                200,
                json={"ok": True, "result": {"id": 9, "first_name": "Bee", "username": "bee_bot"}},
            )
        if method == "getUpdates":
            pending = [u for u in self.updates if u["update_id"] >= body["offset"]]
            return httpx.Response(200, json={"ok": True, "result": pending})
        if method == "sendMessage":
            self.sent.append((body["chat_id"], body["text"]))
            return httpx.Response(200, json={"ok": True, "result": {}})
        return httpx.Response(404)


@pytest.fixture
def setup(tmp_path):
    config = ProviderConfig(
        kind="openai_compatible",
        endpoint="https://fixture.test/v1",
        model="fixture-agent",
        capabilities={"text": True, "tool_calls": True},
    )
    database, store, agent, chat, _ = fixture_state(tmp_path, config=config)
    telegram = Telegram()
    service = ChannelService(database)
    link, code = service.create(
        agent.id,
        CreateChannel(client_request_id=uuid4(), kind="telegram", token=SecretStr(TOKEN)),
        secret_ref="vault:fixture",
        identity=Identity(id="9", name="Bee", username="bee_bot"),
    )
    return database, store, agent, chat, telegram, service, link, code


def worker(database, telegram, script=None):
    return ChannelWorker(
        database,
        Resolver(),
        adapters_by_kind={"telegram": TelegramAdapter(httpx.MockTransport(telegram))},
        provider_transport=httpx.MockTransport(lambda request: script.pop(0))
        if script is not None
        else None,
    )


def run(coroutine):
    return asyncio.run(coroutine)


def test_codes_are_normalized():
    assert normalize_code("/start abcd-2345") == "ABCD2345"
    assert normalize_code("ABCD2345") == "ABCD2345"
    assert normalize_code("/start@bee_bot ABCD2345") == "ABCD2345"
    assert normalize_code("olá ABCD2345") is None
    assert normalize_code("ABCD1234") is None  # 1 fica fora do alfabeto sem ambiguidade.


def test_pairing_requires_code_and_ignores_strangers(setup):
    database, _, _, _, telegram, service, link, code = setup
    work = worker(database, telegram)
    telegram.message("oi", chat=7)
    telegram.message("/start ZZZZZZZZ", chat=7)
    run(work.poll_once(link["id"]))
    assert telegram.sent == [] and service.get(link["id"])["status"] == "pending"
    telegram.message(f"/start {code}", chat=42)
    run(work.poll_once(link["id"]))
    paired = service.get(link["id"])
    assert (
        paired["status"] == "active" and paired["chat_id"] == "42" and paired["peer_name"] == "Ana"
    )
    assert telegram.sent[0][0] == 42 and "Conectado" in telegram.sent[0][1]
    # Código é de uso único; outro chat não assume o vínculo.
    telegram.message(f"/start {code}", chat=7)
    telegram.message("quem é você?", chat=7)
    run(work.poll_once(link["id"]))
    assert len(telegram.sent) == 1 and service.get(link["id"])["chat_id"] == "42"


def test_chat_mode_replies_once_and_forwards_routine_messages(setup):
    database, store, agent, chat, telegram, service, link, code = setup
    script = [answer("Olá! Sou sua abelha.")]
    work = worker(database, telegram, script)
    telegram.message(f"/start {code}")
    run(work.poll_once(link["id"]))
    telegram.message("Oi abelha")
    assert run(work.poll_once(link["id"])) == 1
    assert run(work.flush_once(link["id"])) == 1
    assert telegram.sent[-1] == (42, "Olá! Sou sua abelha.")
    # Reentrega da mesma atualização (cursor perdido) não gera nova resposta.
    service.set_cursor(link["id"], "0")
    assert run(work.poll_once(link["id"])) == 0
    assert run(work.flush_once(link["id"])) == 0 and script == []
    with store.transaction(write=False) as unit:
        roles = [m.role for m in unit.messages.list(conversation_id=chat.id)]
    assert roles == ["user", "assistant"]


def test_agent_mode_creates_task_and_sends_result(setup):
    database, store, agent, chat, telegram, service, link, code = setup
    tools = ToolService(database)
    for manifest in tools.catalog()[1:]:
        plugin = tools.install({"client_request_id": uuid4(), "manifest": manifest})
        tools.update_plugin(plugin.id, {"expected_revision": 1, "enabled": True})
        for tool in manifest["tools"]:
            tools.set_grant(
                agent.id, plugin.id, tool["tool_name"], {"expected_revision": 0, "enabled": True}
            )
    work = worker(database, telegram)
    telegram.message(f"/start {code}")
    telegram.message("/modo agente")
    run(work.poll_once(link["id"]))
    assert service.get(link["id"])["mode"] == "agent"
    telegram.message("Resuma https://example.org/a")
    run(work.poll_once(link["id"]))
    assert "Trabalhando" in telegram.sent[-1][1]
    script = [
        tool_calls(("f1", "web_fetch", {"url": "https://example.org/a"})),
        answer("Abelhas polinizam 75% das culturas. Fonte: https://example.org/a"),
    ]
    agent_worker = AgentTaskWorker(
        database,
        transport=httpx.MockTransport(lambda request: script.pop(0)),
        web=WebClient(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200, text=PAGE, headers={"content-type": "text/html"}
                )
            ),
            resolver=public,
        ),
    )
    for _ in range(20):
        if not asyncio.run(agent_worker.run_once()):
            break
    assert run(work.flush_once(link["id"])) >= 1
    assert any("75%" in text for _, text in telegram.sent)


def test_invalid_token_fails_link_and_pause_stops_polling(setup):
    database, _, agent, _, telegram, service, link, code = setup
    work = worker(database, telegram)
    paused = service.update(agent.id, link["id"], UpdateChannel(expected_revision=1, paused=True))
    assert paused["status"] == "paused" and paused["id"] not in {
        row["id"] for row in service.runnable()
    }
    resumed = service.update(agent.id, link["id"], UpdateChannel(expected_revision=2, paused=False))
    assert resumed["status"] == "pending"
    telegram.status = 401
    run(work._inbound_loop(link["id"]))
    failed = service.get(link["id"])
    assert failed["status"] == "failed" and failed["reason_code"] == "channel_token_invalid"
    revoked = service.revoke(
        agent.id, link["id"], RevisionInput(expected_revision=failed["revision"])
    )
    assert revoked["status"] == "revoked"


def test_approval_notice_is_sent_once(setup, monkeypatch):
    database, _, _, _, telegram, service, link, code = setup
    work = worker(database, telegram)
    telegram.message(f"/start {code}")
    run(work.poll_once(link["id"]))
    from bees_core.agent_tools import service as agent_service

    monkeypatch.setattr(
        agent_service.AgentToolService,
        "list",
        lambda self, *args, **kwargs: [
            {
                "id": "a1",
                "summary": "Clicar em Comprar",
                "tool_name": "computer.browser.click",
                "valid": True,
            }
        ],
    )
    assert run(work.flush_once(link["id"])) == 1
    assert "Clicar em Comprar" in telegram.sent[-1][1]
    assert run(work.flush_once(link["id"])) == 0


def test_discord_adapter_cursor_and_split():
    messages = [
        {"id": "120", "author": {"id": "u1", "username": "ana"}, "content": "olá"},
        {"id": "121", "author": {"id": "b1", "username": "bee", "bot": True}, "content": "eco"},
    ]
    posted = []

    def discord(request: httpx.Request):
        assert request.headers["authorization"] == f"Bot {TOKEN}"
        if request.method == "GET" and request.url.path.endswith("/messages"):
            if request.url.params.get("limit") == "1":
                return httpx.Response(200, json=[{"id": "119"}])
            assert request.url.params["after"] == "119"
            return httpx.Response(200, json=list(reversed(messages)))
        if request.method == "POST":
            posted.append(json.loads(request.content))
            return httpx.Response(200, json={"id": "200"})
        return httpx.Response(404)

    adapter = DiscordAdapter(httpx.MockTransport(discord))
    found, cursor = run(adapter.poll(TOKEN, "555", "0"))
    assert found == [] and cursor == "119"
    found, cursor = run(adapter.poll(TOKEN, "555", cursor))
    assert [item.text for item in found] == ["olá"] and cursor == "121"
    run(adapter.send(TOKEN, "555", "u1", "x" * 2500))
    assert len(posted) == 2 and all(len(p["content"]) <= 1900 for p in posted)
    assert posted[0]["allowed_mentions"] == {"parse": []}
    with pytest.raises(ChannelError, match="channel_target_invalid"):
        run(adapter.poll(TOKEN, None, "0"))


def test_split_text_prefers_paragraphs():
    parts = split_text("a" * 30 + "\n\n" + "b" * 30, 40)
    assert parts == ["a" * 30, "b" * 30]
