"""Controles de tarefas e parada global com SQLite e HTTP loopback reais."""

import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from uuid import uuid4

import pytest

from bees_core.budgets import BudgetService
from bees_core.execution import TaskWorker
from bees_core.models import Agent
from bees_core.providers.contracts import ProviderCapabilities, ProviderConfig
from bees_core.safety import SafetyCommand, SafetyService
from bees_core.storage.database import Database
from bees_core.storage.store import StateStore
from bees_core.tasks import TaskCommandInput, TaskError, TaskInput, TaskService

USAGE = {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}


def response(content="Resposta própria de teste"):
    return 200, {
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": USAGE,
    }


class Scenario:
    def __init__(self, tmp_path, endpoint):
        self.database = Database(tmp_path / "state.sqlite3")
        self.database.initialize()
        self.store = StateStore(self.database)
        config = ProviderConfig(
            kind="openai_compatible",
            endpoint=endpoint,
            model="fixture-text",
            capabilities=ProviderCapabilities(),
            timeout_seconds=5,
            deadline_seconds=10,
        )
        self.agent = Agent(
            name="Teste de controles", provider_config=config.model_dump(mode="json")
        )
        with self.store.transaction() as uow:
            uow.agents.create(self.agent)
        self.requests = []
        self.respond = lambda request: response()
        self.errors = []

    def service(self):
        return TaskService(Database(self.database.path))

    def task(self, *, max_calls=3):
        return self.service().create(
            self.agent.id,
            TaskInput(
                client_request_id=uuid4(),
                title="Relatório de teste",
                objective="Explique filas duráveis.",
                expected_result="Três recomendações.",
                max_calls=max_calls,
            ),
        )

    def detail(self, task):
        return self.service().detail(self.agent.id, task.id)

    def command(self, task, kind, **values):
        command = TaskCommandInput(
            client_request_id=uuid4(),
            expected_revision=self.detail(task).task.revision,
            kind=kind,
            **values,
        )
        result = self.service().command(self.agent.id, task.id, command)
        return command, result

    def replay(self, task, command):
        return self.service().command(self.agent.id, task.id, command)

    def run_once(self):
        return asyncio.run(TaskWorker(Database(self.database.path), poll_seconds=0.01).run_once())

    def entries(self):
        with self.store.transaction(write=False) as uow:
            return uow.usage_entries.since(self.agent.id, self.agent.created_at)

    def snapshot(self, *tables):
        # Nomes são constantes dos testes, nunca entrada externa.
        with self.database.transaction(write=False) as connection:
            return {
                table: connection.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall()
                for table in tables
            }

    def safety_command(self, kind):
        service = SafetyService(Database(self.database.path))
        command = SafetyCommand(
            client_request_id=uuid4(),
            kind=kind,
            expected_revision=service.current()["revision"],
        )
        result = service.command(command)
        assert SafetyService(Database(self.database.path)).command(command) == result
        return result


@pytest.fixture
def scenario(tmp_path):
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            try:
                assert self.path == "/v1/chat/completions"
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                state.requests.append(body)
                status, result = state.respond(body)
            except Exception as error:
                state.errors.append(error)
                status, result = 500, {"error": "Falha no servidor próprio de teste"}
            encoded = json.dumps(result).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    state = Scenario(tmp_path, f"http://127.0.0.1:{server.server_port}/v1")
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05})
    thread.start()
    try:
        yield state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        assert not thread.is_alive()
        assert not state.errors, state.errors


def test_redirect_and_replays_preserve_paid_call_and_generate_only_new_directive(scenario):
    task = scenario.task()
    redirects = []
    started_calls = []

    def respond(request):
        detail = scenario.detail(task)
        [reserved] = [entry for entry in scenario.entries() if entry.status == "reserved"]
        assert reserved.model_call_id == detail.calls[-1].id
        assert reserved.source == "task" and reserved.conversation_id == task.conversation_id
        started_calls.append(reserved.model_call_id)
        if len(scenario.requests) == 1:
            before = scenario.snapshot("model_calls", "usage_entries")
            command, redirected = scenario.command(
                task, "redirect", instruction="Priorize a integridade."
            )
            redirects.append(command)
            assert scenario.replay(task, command) == redirected
            assert scenario.snapshot("model_calls", "usage_entries") == before
            assert scenario.detail(task).task.calls_started == 1
            return response("Resposta anterior paga")
        assert "Priorize a integridade." in request["messages"][-1]["content"]
        return response("Resposta redirecionada paga")

    scenario.respond = respond
    assert scenario.run_once()
    first = scenario.detail(task)
    [entry] = scenario.entries()
    assert first.task.status == "queued" and first.task.calls_started == 1
    assert first.calls[0].status == "confirmed" and first.calls[0].obsolete
    assert first.calls[0].output_message_id is None
    assert entry.model_call_id == first.calls[0].id == started_calls[0]
    assert entry.status == "confirmed" and entry.total_tokens == 15
    assert not any(message.content == "Resposta anterior paga" for message in first.messages)
    before = scenario.snapshot("tasks", "runs", "model_calls", "task_commands", "usage_entries")
    assert scenario.replay(task, redirects[0]) == first.task
    assert scenario.snapshot(*before) == before
    assert scenario.run_once()
    final = scenario.detail(task)
    assert final.task.status == "completed" and final.task.calls_started == 2
    assert len(final.commands) == 1 and len(final.calls) == len(scenario.requests) == 2
    assert [call.id for call in final.calls] == started_calls
    assert final.calls[0] == first.calls[0]
    assert final.messages[-1].content == "Resposta redirecionada paga"
    entries = scenario.entries()
    assert entries[0] == entry and {item.model_call_id for item in entries} == set(started_calls)
    assert sum(item.counted_tokens() for item in entries) == 30
    before = scenario.snapshot("tasks", "runs", "model_calls", "task_commands", "usage_entries")
    assert scenario.replay(task, redirects[0]) == final.task
    assert not scenario.run_once()
    assert scenario.snapshot(*before) == before and len(scenario.requests) == 2


def test_pause_resume_replays_keep_paid_result_without_second_generation(scenario):
    task = scenario.task(max_calls=1)
    pauses = []

    def respond(request):
        pause, paused = scenario.command(task, "pause")
        pauses.append(pause)
        assert scenario.replay(task, pause) == paused
        return response("Resposta paga preservada")

    scenario.respond = respond
    assert scenario.run_once()
    detail = scenario.detail(task)
    assert detail.task.status == "paused" and detail.task.calls_started == 1
    assert detail.calls[0].status == "confirmed"
    assert detail.messages[-1].content == "Resposta paga preservada"
    [entry] = scenario.entries()
    assert entry.model_call_id == detail.calls[0].id and entry.counted_tokens() == 15
    before = scenario.snapshot("model_calls", "usage_entries", "messages")
    assert scenario.replay(task, pauses[0]) == detail.task
    resume, completed = scenario.command(task, "resume")
    assert completed.status == "completed" and completed.calls_started == 1
    assert scenario.replay(task, resume) == completed
    assert scenario.replay(task, pauses[0]) == completed
    assert not scenario.run_once()
    assert scenario.snapshot(*before) == before and len(scenario.requests) == 1
    assert len(scenario.detail(task).commands) == 2


def test_global_stop_resume_preserves_paused_unknown_journal_and_consumption(scenario):
    paid_paused = scenario.task(max_calls=1)

    def pause_while_paid(request):
        scenario.command(paid_paused, "pause")
        return response("Entrega pausada já paga")

    scenario.respond = pause_while_paid
    assert scenario.run_once()
    unknown = scenario.task()
    scenario.respond = lambda request: (503, {"error": "Resposta perdida após aceite"})
    assert scenario.run_once()
    queued_paused = scenario.task()
    scenario.command(queued_paused, "pause")
    cancelled = scenario.task()
    scenario.command(cancelled, "cancel")
    paid_detail, unknown_detail = scenario.detail(paid_paused), scenario.detail(unknown)
    assert paid_detail.task.status == unknown_detail.task.status == "paused"
    assert unknown_detail.unknown_model_calls == 1 and not unknown_detail.calls[0].acknowledged
    assert unknown_detail.calls[0].status == "outcome_unknown"
    assert unknown_detail.task.calls_started == 1
    entries = scenario.entries()
    assert len(entries) == 2 and {entry.status for entry in entries} == {"confirmed", "unknown"}
    uncertain = next(entry for entry in entries if entry.status == "unknown")
    assert uncertain.model_call_id == unknown_detail.calls[0].id
    assert uncertain.counted_tokens() == uncertain.reserved_tokens > 0
    before = scenario.snapshot(
        "tasks",
        "runs",
        "model_calls",
        "task_commands",
        "messages",
        "usage_entries",
        "execution_leases",
    )
    summary = BudgetService(Database(scenario.database.path)).summary(scenario.agent.id)
    assert summary["counted_tokens"] == 15 + uncertain.reserved_tokens
    assert summary["unknown_entries"] == 1 and summary["entries"] == 2
    stopped = scenario.safety_command("stop")
    assert stopped["status"] == "stopped"
    assert not scenario.run_once()
    assert scenario.snapshot(*before) == before
    resumed = scenario.safety_command("resume")
    assert resumed["status"] == "running" and resumed["generation"] == stopped["generation"]
    for _ in range(2):
        assert not scenario.run_once()
    assert scenario.snapshot(*before) == before and len(scenario.requests) == 2
    assert BudgetService(Database(scenario.database.path)).summary(scenario.agent.id) == summary
    assert scenario.detail(queued_paused).task.status == "paused"
    assert scenario.detail(cancelled).task.status == "cancelled"
    assert scenario.detail(paid_paused) == paid_detail
    assert scenario.detail(unknown) == unknown_detail
    with pytest.raises(TaskError, match="unknown_requires_ack"):
        scenario.command(unknown, "resume")
    assert scenario.snapshot(*before) == before
    assert scenario.entries() == entries
