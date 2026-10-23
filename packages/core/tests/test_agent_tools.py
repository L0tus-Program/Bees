"""Ciclo do agente com modelo HTTP controlado, web por transporte próprio e decisões."""

import asyncio
import json
from uuid import uuid4

import httpx
import pytest
from test_tasks_execution import answer, command, fixture_state

from bees_core.agent_tools.catalog import by_function
from bees_core.agent_tools.cycle import AgentTaskInput, AgentTaskService, AgentTaskWorker
from bees_core.agent_tools.service import AgentToolService
from bees_core.agent_tools.web import WebClient, WebError, extract_html, parse_duckduckgo
from bees_core.artifacts import ArtifactStore
from bees_core.memory import MemoryService
from bees_core.models import Conversation
from bees_core.providers.contracts import ProviderConfig
from bees_core.safety import SafetyCommand, SafetyService
from bees_core.tools import ToolService

PAGE = """<html><head><title>Abelhas e polinização</title>
<meta name="description" content="Resumo sobre abelhas"></head><body>
<nav>menu</nav><script>alert('x')</script>
<h1>Abelhas</h1><p>As abelhas polinizam <b>cerca de 75%</b> das culturas.</p>
<a href="/fonte">Fonte primária</a></body></html>"""

DDG = """<div class="result"><a class="result__a"
href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.org%2Fabelhas&rut=x">Abelhas — Exemplo</a>
<a class="result__snippet" href="#">Polinização e <b>abelhas</b>.</a></div>"""


def tool_calls(*calls):
    return httpx.Response(
        200,
        json={
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": identity,
                                "type": "function",
                                "function": {"name": name, "arguments": json.dumps(arguments)},
                            }
                            for identity, name, arguments in calls
                        ],
                    },
                    "finish_reason": "tool_calls",
                }
            ]
        },
    )


def web_handler(request):
    if request.url.host == "html.duckduckgo.com":
        return httpx.Response(200, text=DDG, headers={"content-type": "text/html"})
    if request.url.path == "/redirect":
        return httpx.Response(302, headers={"location": "https://example.org/abelhas"})
    if request.url.path == "/interno":
        return httpx.Response(302, headers={"location": "http://10.0.0.5/admin"})
    return httpx.Response(200, text=PAGE, headers={"content-type": "text/html; charset=utf-8"})


async def public(host, port):
    return ["10.0.0.5"] if host.startswith("10.") else ["93.184.216.34"]


class Case:
    def __init__(self, tmp_path, script, *, extra_tools=()):
        config = ProviderConfig(
            kind="openai_compatible",
            endpoint="https://fixture.test/v1",
            model="fixture-agent",
            capabilities={"text": True, "tool_calls": True},
        )
        self.database, self.store, self.agent, self.chat, _ = fixture_state(tmp_path, config=config)
        self.tools = ToolService(self.database)
        self.plugins = {}
        for manifest in self.tools.catalog()[1:]:
            plugin = self.tools.install({"client_request_id": uuid4(), "manifest": manifest})
            plugin = self.tools.update_plugin(plugin.id, {"expected_revision": 1, "enabled": True})
            self.plugins[manifest["plugin_key"]] = plugin
            for tool in manifest["tools"]:
                self.tools.set_grant(
                    self.agent.id,
                    plugin.id,
                    tool["tool_name"],
                    {"expected_revision": 0, "enabled": True},
                )
        self.requests = []
        self.script = list(script)

        def respond(request):
            self.requests.append(json.loads(request.content))
            return self.script.pop(0)

        self.web = WebClient(transport=httpx.MockTransport(web_handler), resolver=public)
        self.artifacts = ArtifactStore(self.database, tmp_path / "artifacts", actor="worker")
        self.worker = AgentTaskWorker(
            self.database,
            transport=httpx.MockTransport(respond),
            web=self.web,
            artifacts=self.artifacts,
        )
        self.tasks = AgentTaskService(self.database)
        self.service = AgentToolService(self.database)
        self.task = self.tasks.create(
            self.agent.id,
            AgentTaskInput(
                client_request_id=uuid4(),
                title="Pesquisar abelhas",
                objective="Pesquise sobre polinização por abelhas e publique um resumo.",
            ),
        )

    def work(self, times=1):
        result = None
        for _ in range(times):
            result = asyncio.run(self.worker.run_once())
        return result

    def drain(self, limit=40):
        for _ in range(limit):
            if not self.work():
                return
        raise AssertionError("fila não esvaziou")

    def detail(self):
        return self.tasks.detail(self.agent.id, self.task.id)

    def results(self):
        return [
            json.loads(message.content)
            for message in self.detail().messages
            if message.role == "tool"
        ]


def test_search_fetch_publish_and_final_answer(tmp_path):
    case = Case(
        tmp_path,
        [
            tool_calls(("c1", "web_search", {"query": "polinização abelhas"})),
            tool_calls(("c2", "web_fetch", {"url": "https://example.org/redirect"})),
            tool_calls(
                (
                    "c3",
                    "publish_file",
                    {"name": "resumo", "kind": "markdown", "content": "# Abelhas\n75%"},
                )
            ),
            answer("Resumo publicado. Fonte: https://example.org/abelhas"),
        ],
    )
    case.drain()
    detail = case.detail()
    assert detail.task.status == "completed"
    offered = {tool["function"]["name"] for tool in case.requests[0]["tools"]}
    assert offered == {"web_search", "web_fetch", "memory_search", "memory_save", "publish_file"}
    search, fetch, publish = case.results()
    assert search["status"] == "confirmed"
    assert search["result"]["results"][0]["url"] == "https://example.org/abelhas"
    assert fetch["result"]["final_url"] == "https://example.org/abelhas"
    assert fetch["result"]["title"] == "Abelhas e polinização"
    assert "75%" in fetch["result"]["text"] and "alert" not in fetch["result"]["text"]
    assert publish["result"]["name"] == "resumo.md"
    artifacts = case.artifacts.list(case.agent.id, case.task.id)
    assert [artifact.name for artifact in artifacts] == ["resumo.md"]
    confirmed = [action for action in detail.actions if action.status == "confirmed"]
    assert {action.tool_name for action in confirmed} >= {
        "web.search",
        "web.fetch",
        "deliverable.publish",
    }
    # Contexto do modelo recebeu resultados correlacionados antes da próxima geração.
    last = case.requests[-1]["messages"]
    assert [m["role"] for m in last if m["role"] == "tool"].count("tool") == 3


def test_memory_save_asks_then_allow_once_runs_once(tmp_path):
    case = Case(
        tmp_path,
        [
            tool_calls(("m1", "memory_save", {"content": "Prefere relatórios curtos."})),
            answer("Preferência registrada."),
        ],
    )
    assert case.work()  # geração com proposta
    assert case.work()  # intenção pede decisão
    assert case.detail().task.status == "waiting_approval"
    assert not case.work()
    pending = case.service.list(case.agent.id, task_id=case.task.id, status="pending")
    assert len(pending) == 1 and pending[0]["tool_name"] == "memory.save"
    assert "Prefere relatórios curtos" in pending[0]["summary"]
    detail = case.service.view(case.agent.id, pending[0]["id"], detail=True)
    assert detail["arguments_preview"]["content"] == "Prefere relatórios curtos."
    case.service.decide(
        case.agent.id,
        pending[0]["id"],
        {"client_request_id": uuid4(), "expected_revision": 1, "decision": "allow_once"},
    )
    case.drain()
    assert case.detail().task.status == "completed"
    memories = MemoryService(case.database).list_for_agent(case.agent.id)
    assert [memory.content for memory in memories] == ["Prefere relatórios curtos."]
    assert case.results()[0]["status"] == "confirmed"


def test_denied_proposal_returns_rejection_and_task_continues(tmp_path):
    case = Case(
        tmp_path,
        [
            tool_calls(("m1", "memory_save", {"content": "Algo"})),
            answer("Ok, não salvei."),
        ],
    )
    case.work(2)
    pending = case.service.list(case.agent.id, task_id=case.task.id)
    case.service.decide(
        case.agent.id,
        pending[0]["id"],
        {"client_request_id": uuid4(), "expected_revision": 1, "decision": "deny"},
    )
    case.drain()
    assert case.detail().task.status == "completed"
    assert case.results() == [{"code": "denied_by_user", "status": "rejected"}]
    assert MemoryService(case.database).list_for_agent(case.agent.id) == []


def test_allow_tool_rule_skips_later_questions(tmp_path):
    case = Case(
        tmp_path,
        [
            tool_calls(("m1", "memory_save", {"content": "A"})),
            tool_calls(("m2", "memory_save", {"content": "B"})),
            answer("Pronto."),
        ],
    )
    case.work(2)
    pending = case.service.list(case.agent.id, task_id=case.task.id)
    case.service.decide(
        case.agent.id,
        pending[0]["id"],
        {"client_request_id": uuid4(), "expected_revision": 1, "decision": "allow_tool"},
    )
    case.drain()
    assert case.detail().task.status == "completed"
    assert len(MemoryService(case.database).list_for_agent(case.agent.id)) == 2
    assert len(case.service.list(case.agent.id, task_id=case.task.id)) == 1


def test_unknown_and_revoked_tools_never_reach_runner(tmp_path):
    case = Case(
        tmp_path,
        [
            tool_calls(
                ("x1", "shell_run", {"command": "rm -rf /"}),
                ("x2", "web_fetch", {"url": "https://example.org/a"}),
            ),
            answer("Fim."),
        ],
    )
    assert case.work()
    plugin = case.plugins["bees.web"]
    grant = next(
        row
        for row in case.tools.list_tools(case.agent.id)
        if row["tool_name"] == "web.fetch" and row["plugin_id"] == str(plugin.id)
    )
    case.tools.set_grant(
        case.agent.id,
        plugin.id,
        "web.fetch",
        {"expected_revision": grant["grant_revision"], "enabled": False},
    )
    case.drain()
    unknown, revoked = case.results()
    assert unknown == {"code": "tool_unavailable", "status": "rejected"}
    assert revoked == {"code": "tool_unavailable", "status": "rejected"}
    assert not [a for a in case.detail().actions if a.tool_name == "web.fetch"]


def test_private_redirect_is_blocked_before_reading(tmp_path):
    case = Case(
        tmp_path,
        [
            tool_calls(("w1", "web_fetch", {"url": "https://example.org/interno"})),
            answer("Bloqueado."),
        ],
    )
    case.drain()
    (result,) = case.results()
    assert result["status"] == "failed" and result["code"] == "web_private_address_blocked"


def test_global_stop_requeues_without_effect(tmp_path):
    case = Case(
        tmp_path,
        [tool_calls(("w1", "web_search", {"query": "x"})), answer("ok")],
    )
    assert case.work()
    safety = SafetyService(case.database)

    def toggle(kind):
        current = safety.current()
        safety.command(
            SafetyCommand(
                client_request_id=uuid4(), kind=kind, expected_revision=current["revision"]
            )
        )

    toggle("stop")
    assert not case.work()
    assert not [a for a in case.detail().actions if a.tool_name == "web.search"]
    toggle("resume")
    case.drain()
    assert case.detail().task.status == "completed"


def test_cancel_closes_pending_block(tmp_path):
    case = Case(tmp_path, [tool_calls(("m1", "memory_save", {"content": "A"}))])
    case.work(2)
    assert case.detail().task.status == "waiting_approval"
    command(case.tasks, case.agent, case.task, "cancel")
    assert case.detail().task.status == "cancelled"
    assert case.results() == [{"code": "tool_cycle_superseded", "status": "not_started"}]


def test_result_is_delivered_to_origin_chat_once(tmp_path):
    case = Case(tmp_path, [answer("Resposta final do agente.")])
    with case.store.transaction() as unit:
        task = unit.tasks.get(case.task.id)
        unit.tasks.update(
            task.model_copy(
                update={
                    "metadata": task.metadata
                    | {"agent_chat": {"conversation_id": str(case.chat.id)}}
                }
            ),
            task.revision,
        )
    case.drain()
    with case.store.transaction(write=False) as unit:
        messages = unit.messages.list(conversation_id=case.chat.id)
    contents = [m.content for m in messages if m.role == "assistant"]
    assert contents == ["Resposta final do agente."]
    case.worker._deliver_to_chat(case.task.id)
    with case.store.transaction(write=False) as unit:
        again = [
            m for m in unit.messages.list(conversation_id=case.chat.id) if m.role == "assistant"
        ]
    assert len(again) == 1


def test_html_and_search_parsers():
    page = extract_html(PAGE, "https://example.org/x")
    assert page["title"] == "Abelhas e polinização"
    assert page["description"] == "Resumo sobre abelhas"
    assert page["links"] == [{"url": "https://example.org/fonte", "text": "Fonte primária"}]
    assert "menu" in page["text"] and "alert" not in page["text"]
    results = parse_duckduckgo(DDG, 5)
    assert results == [
        {
            "title": "Abelhas — Exemplo",
            "url": "https://example.org/abelhas",
            "snippet": "Polinização e abelhas.",
        }
    ]


def test_real_resolver_blocks_loopback_and_credentials():
    client = WebClient()
    with pytest.raises(WebError, match="private_address_blocked"):
        asyncio.run(client.fetch("http://127.0.0.1:8080/"))
    with pytest.raises(WebError, match="credentials_in_url"):
        asyncio.run(client.fetch("https://user:pw@example.org/"))
    with pytest.raises(WebError, match="port_blocked"):
        asyncio.run(client.fetch("https://example.org:22/"))


def test_function_names_are_unique_and_policy_registered():
    from bees_core.policies import DEFAULT_REGISTRY

    for name in ("web_search", "web_fetch", "memory_save", "memory_search", "publish_file"):
        spec = by_function(name)
        assert DEFAULT_REGISTRY.get(spec.name, spec.action) is not None


def test_conversation_is_not_reused(tmp_path):
    case = Case(tmp_path, [answer("ok")])
    assert case.task.conversation_id != case.chat.id
    assert isinstance(case.chat, Conversation)


def test_research_mode_publishes_report_with_verified_and_unverified_links(tmp_path):
    case = Case(
        tmp_path,
        [
            tool_calls(("r1", "web_search", {"query": "abelhas polinização"})),
            tool_calls(("r2", "web_fetch", {"url": "https://example.org/abelhas"})),
            answer(
                "As abelhas polinizam cerca de 75% das culturas [1].\n\n"
                "Fontes: [1] https://example.org/abelhas ; [2] https://nao-lida.example/x"
            ),
        ],
    )
    with case.store.transaction() as unit:
        task = unit.tasks.get(case.task.id)
        unit.tasks.update(
            task.model_copy(update={"metadata": task.metadata | {"research": True}}),
            task.revision,
        )
    case.drain()
    detail = case.detail()
    assert detail.task.status == "completed"
    assert "Modo pesquisa" in case.requests[0]["messages"][0]["content"] or any(
        "Modo pesquisa" in (m.get("content") or "") for m in case.requests[0]["messages"]
    )
    check = detail.runs[-1].checkpoint["research"]
    assert check["sources_read"] == 1 and check["links_cited"] == 2
    assert check["links_verified"] == 1
    assert check["unverified_links"] == ["https://nao-lida.example/x"]
    names = {artifact.name for artifact in case.artifacts.list(case.agent.id, case.task.id)}
    assert names == {"pesquisa.md", "pesquisa-fontes.json"}
    report = next(
        a for a in case.artifacts.list(case.agent.id, case.task.id) if a.name == "pesquisa.md"
    )
    _, content = case.artifacts.read(case.agent.id, report.id)
    text = content.decode()
    assert "https://example.org/abelhas" in text and "não verificados" in text


def test_pending_agent_decision_is_actionable_attention_once(tmp_path):
    from bees_core.attention import AttentionService

    case = Case(tmp_path, [tool_calls(("m1", "memory_save", {"content": "X"})), answer("ok")])
    case.work(2)
    attention = AttentionService(case.database)
    items = [i for i in attention.list(case.agent.id).items if i.kind == "approval_required"]
    assert len(items) == 1 and items[0].requires_attention
    case.work(1)  # sem efeito enquanto aguarda: nenhum item duplicado
    items = [i for i in attention.list(case.agent.id).items if i.kind == "approval_required"]
    assert len(items) == 1
