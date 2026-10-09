"""Consumo e limite de tokens com SQLite e HTTP de teste; nenhum modelo pago."""

import asyncio
import subprocess
import sys
import threading
from datetime import timedelta
from importlib import resources
from uuid import uuid4

import httpx
import pytest

from bees_core.budgets import BudgetService, LimitInput, estimate_input, reserve
from bees_core.execution import TaskWorker
from bees_core.models import Agent, Conversation, UsageEntry, utc_now
from bees_core.providers.contracts import ProviderCapabilities, ProviderConfig
from bees_core.providers.errors import ProviderError
from bees_core.providers.service import ProviderService
from bees_core.storage.database import Database
from bees_core.storage.store import InvalidTransition, RevisionConflict, StateStore
from bees_core.tasks import TaskInput, TaskService

USAGE = {"prompt_tokens": 120, "completion_tokens": 30, "total_tokens": 150}


def answer(content="Resposta própria de teste", usage=USAGE):
    body = {
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ]
    }
    if usage is not None:
        body["usage"] = usage
    return httpx.Response(200, json=body)


class Setup:
    def __init__(self, tmp_path):
        self.database = Database(tmp_path / "state.sqlite3")
        self.database.initialize()
        self.store = StateStore(self.database)
        config = ProviderConfig(
            kind="openai_compatible",
            endpoint="https://fixture.test/v1",
            model="fixture-text",
            capabilities=ProviderCapabilities(),
        )
        self.agent = Agent(name="Abelha de teste", provider_config=config.model_dump(mode="json"))
        self.chat = Conversation(agent_id=self.agent.id)
        with self.store.transaction() as uow:
            uow.agents.create(self.agent)
            uow.conversations.create(self.chat)
        self.budgets = BudgetService(self.database)
        self.requests = []

    def provider(self, handler=None):
        def record(request):
            self.requests.append(request)
            return (handler or (lambda request: answer()))(request)

        return ProviderService(self.database, transport=httpx.MockTransport(record))

    def send(self, text="Uma pergunta própria de teste", handler=None):
        return asyncio.run(self.provider(handler).chat(self.agent.id, self.chat.id, text))

    def entries(self):
        with self.store.transaction(write=False) as uow:
            return uow.usage_entries.since(self.agent.id, utc_now() - timedelta(days=1))

    def limit(self, tokens, **values):
        current = self.budgets.summary(self.agent.id)["limit"]
        return self.budgets.set_limit(
            self.agent.id,
            LimitInput(token_limit=tokens, **values),
            expected_revision=current.revision if current else None,
        )


@pytest.fixture
def setup(tmp_path):
    return Setup(tmp_path)


def test_chat_reserves_before_network_and_confirms_reported_usage(setup):
    setup.send()
    [entry] = setup.entries()
    assert entry.status == "confirmed" and entry.source == "chat"
    assert entry.usage_kind == "reported" and entry.total_tokens == 150
    assert entry.reserved_tokens > 4096 and entry.estimate_method == "chars_div_3_v1"
    assert entry.counted_tokens() == 150
    summary = setup.budgets.summary(setup.agent.id)
    assert (summary["counted_tokens"], summary["reported_tokens"]) == (150, 150)
    assert summary["remaining_tokens"] is None and summary["limit"] is None


def test_missing_usage_is_never_zero(setup):
    setup.send(handler=lambda request: answer(usage=None))
    [entry] = setup.entries()
    assert entry.status == "confirmed" and entry.usage_kind == "unknown"
    assert entry.counted_tokens() == entry.reserved_tokens > 0


def test_limit_blocks_next_generation_before_any_request(setup):
    setup.limit(10_000, output_allowance=1000)
    setup.send()
    before = len(setup.requests)
    setup.limit(10_000, output_allowance=9_900)
    with pytest.raises(ProviderError) as caught:
        setup.send("Outra pergunta")
    assert caught.value.code == "budget_exhausted"
    assert len(setup.requests) == before
    assert len(setup.entries()) == 1
    # Desativar o limite é escolha humana explícita; histórico de consumo continua.
    setup.limit(10_000, status="disabled")
    setup.send("Mais uma")
    assert len(setup.entries()) == 2


@pytest.mark.parametrize(
    ("response", "status", "counted"),
    [
        (httpx.Response(401, json={"error": "chave"}), "released", 0),
        (httpx.Response(429, json={"error": "limite"}), "released", 0),
        (httpx.Response(503, json={"error": "fora"}), "unknown", None),
        (httpx.TimeoutException("perdido"), "unknown", None),
        (httpx.Response(200, json={"choices": []}), "unknown", None),
    ],
)
def test_failed_generation_releases_only_explicit_rejection(setup, response, status, counted):
    def handler(request):
        if isinstance(response, Exception):
            raise response
        return response

    with pytest.raises(ProviderError):
        setup.send(handler=handler)
    [entry] = setup.entries()
    assert entry.status == status
    assert entry.counted_tokens() == (entry.reserved_tokens if counted is None else counted)


def test_unknown_reservation_counts_against_limit(setup):
    setup.limit(12_000, output_allowance=8_000)
    with pytest.raises(ProviderError):
        setup.send(handler=lambda request: (_ for _ in ()).throw(httpx.ReadTimeout("x")))
    [unknown] = setup.entries()
    assert unknown.status == "unknown"
    with pytest.raises(ProviderError) as caught:
        setup.send("Depois do timeout")
    assert caught.value.code == "budget_exhausted"
    assert len(setup.requests) == 1


def test_discarded_response_still_records_consumption(setup):
    def handler(request):
        # Outra sessão escreve na conversa durante a geração: resposta descartada.
        with setup.store.transaction() as uow:
            conversation = uow.conversations.get(setup.chat.id)
            uow.conversations.update(conversation, expected_revision=conversation.revision)
        return answer()

    with pytest.raises(ProviderError) as caught:
        setup.send(handler=handler)
    assert caught.value.code == "state_conflict"
    [entry] = setup.entries()
    assert entry.status == "confirmed" and entry.total_tokens == 150


def test_concurrent_reservations_never_share_free_budget(setup):
    # Pedidos preparados antes: as threads disputam somente a reserva.
    prepared = [_prepared(setup), _prepared(setup)]
    size = max(estimate_input(item.request) for item in prepared)
    setup.limit(size + 1000 + size, output_allowance=1000)
    barrier = threading.Barrier(2)
    results = []

    def attempt(item):
        barrier.wait()
        try:
            with StateStore(Database(setup.database.path)).transaction() as uow:
                results.append(reserve(uow, item, source="chat").id)
        except ProviderError as error:
            results.append(error.code)

    threads = [threading.Thread(target=attempt, args=(item,)) for item in prepared]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(type(item).__name__ for item in results) == ["UUID", "str"]
    assert "budget_exhausted" in results and len(setup.entries()) == 1


def _prepared(setup):
    with setup.store.transaction() as uow:
        return ProviderService(setup.database).prepare_chat(
            uow, setup.agent.id, setup.chat.id, "Pergunta para reserva"
        )


def test_settlement_is_final_and_rows_append_only(setup):
    setup.send()
    [entry] = setup.entries()
    with setup.store.transaction() as uow:
        with pytest.raises(InvalidTransition):
            uow.usage_entries.update(
                entry.model_copy(update={"metadata": {"revisado": True}}), entry.revision
            )
    for sql in (
        "UPDATE usage_entries SET status='released' WHERE id=?",
        "UPDATE usage_entries SET reserved_tokens=1 WHERE id=?",
        "DELETE FROM usage_entries WHERE id=?",
    ):
        with pytest.raises(Exception, match="immutable|final|append-only"):
            with setup.database.transaction() as connection:
                connection.execute(sql, (str(entry.id),))
    assert setup.entries() == [entry]


def test_limit_edits_are_cas_and_validated(setup):
    created = setup.budgets.set_limit(
        setup.agent.id, LimitInput(token_limit=5000), expected_revision=None
    )
    with pytest.raises(RevisionConflict):
        setup.budgets.set_limit(
            setup.agent.id, LimitInput(token_limit=6000), expected_revision=None
        )
    with pytest.raises(RevisionConflict):
        setup.budgets.set_limit(
            setup.agent.id, LimitInput(token_limit=6000), expected_revision=created.revision + 1
        )
    updated = setup.budgets.set_limit(
        setup.agent.id, LimitInput(token_limit=7000), expected_revision=created.revision
    )
    assert (updated.token_limit, updated.revision) == (7000, 2)
    with pytest.raises(ValueError):
        LimitInput(token_limit=0)
    with pytest.raises(ValueError):
        LimitInput(token_limit=10, window_seconds=60)


def task(setup):
    return TaskService(setup.database).create(
        setup.agent.id,
        TaskInput(
            client_request_id=uuid4(),
            title="Relatório",
            objective="Explique filas duráveis.",
            expected_result="Três recomendações.",
        ),
    )


def test_worker_links_usage_to_journal_call(setup):
    created = task(setup)
    worker = TaskWorker(setup.database, transport=httpx.MockTransport(lambda r: answer()))
    assert asyncio.run(worker.run_once())
    detail = TaskService(setup.database).detail(setup.agent.id, created.id)
    [entry] = setup.entries()
    assert entry.source == "task" and entry.model_call_id == detail.calls[0].id
    assert entry.status == "confirmed" and entry.total_tokens == 150


def test_worker_budget_exhaustion_pauses_without_dispatch_or_charge(setup):
    setup.limit(100, output_allowance=50)
    created = task(setup)
    requests = []
    worker = TaskWorker(
        setup.database,
        transport=httpx.MockTransport(lambda r: requests.append(r) or answer()),
    )
    assert asyncio.run(worker.run_once())
    detail = TaskService(setup.database).detail(setup.agent.id, created.id)
    assert not requests and not setup.entries()
    assert detail.task.status == "paused" and detail.task.calls_started == 0
    assert detail.runs[-1].error == "budget_exhausted"
    assert detail.calls[0].status != "dispatch_started"


def test_worker_lost_after_acceptance_keeps_reservation_unknown(setup, tmp_path):
    created = task(setup)
    marker = tmp_path / "accepted"
    script = """
import asyncio, os, sys
from pathlib import Path
import httpx
from bees_core.execution import TaskWorker
from bees_core.storage.database import Database
worker = TaskWorker(Database(sys.argv[1]), lease_seconds=5)
def accepted(request):
    Path(sys.argv[2]).write_text('aceito', encoding='utf-8')
    os._exit(19)
worker.providers.transport = httpx.MockTransport(accepted)
asyncio.run(worker.run_once())
"""
    crashed = subprocess.run(
        [sys.executable, "-c", script, str(setup.database.path), str(marker)], check=False
    )
    assert crashed.returncode == 19 and marker.exists()
    [entry] = setup.entries()
    assert entry.status == "reserved"
    with setup.store.transaction() as uow:
        assert uow.execution.recover_expired(now=utc_now() + timedelta(seconds=6)) == 1
    [entry] = setup.entries()
    assert entry.status == "unknown" and entry.counted_tokens() == entry.reserved_tokens
    detail = TaskService(setup.database).detail(setup.agent.id, created.id)
    assert detail.calls[0].status == "outcome_unknown"


def test_upgrade_nine_to_ten_adds_ledger_and_preserves_state(tmp_path, monkeypatch):
    from bees_core.storage import database as database_module

    original = resources.files("bees_core.storage.migrations")
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    for file in original.iterdir():
        if file.name.endswith(".sql") and int(file.name[:4]) < 10:
            (migrations / file.name).write_bytes(file.read_bytes())
    monkeypatch.setattr(database_module.resources, "files", lambda _: migrations)
    database = Database(tmp_path / "legacy.sqlite3")
    database.initialize()
    now = utc_now().isoformat()
    agent_id = uuid4()
    with database.transaction() as connection:
        connection.execute(
            "INSERT INTO agents(id,name,created_at,updated_at) VALUES(?,?,?,?)",
            (str(agent_id), "Legado", now, now),
        )
        before = connection.execute("SELECT * FROM agents").fetchall()
    (migrations / "0010_usage_budgets.sql").write_bytes(
        (original / "0010_usage_budgets.sql").read_bytes()
    )
    database.initialize()
    assert database.schema_version() == 10
    assert len(list(tmp_path.glob("legacy.sqlite3.backup-*.sqlite3"))) == 1
    with database.transaction(write=False) as connection:
        assert connection.execute("SELECT * FROM agents").fetchall() == before
        assert connection.execute("SELECT count(*) FROM usage_entries").get == 0
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    assert BudgetService(database).summary(agent_id)["counted_tokens"] == 0


def test_limit_must_fit_one_generation():
    with pytest.raises(ValueError):
        LimitInput(token_limit=100, output_allowance=100)
    assert LimitInput(token_limit=101, output_allowance=100).token_limit == 101


@pytest.mark.parametrize(
    "usage",
    [
        {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        {"prompt_tokens": 10, "completion_tokens": 10**30},
        {"total_tokens": 10**30},
    ],
)
def test_zero_or_absurd_usage_counts_reservation_and_keeps_paid_response(setup, usage):
    response = setup.send(handler=lambda request: answer("Resposta paga", usage=usage))
    assert response.message.content == "Resposta paga"
    [entry] = setup.entries()
    assert entry.status == "confirmed" and entry.usage_kind == "unknown"
    assert entry.counted_tokens() == entry.reserved_tokens
    with setup.store.transaction(write=False) as uow:
        assert uow.messages.list(conversation_id=setup.chat.id)[-1].content == "Resposta paga"


def test_absurd_usage_in_worker_completes_task(setup):
    created = task(setup)
    worker = TaskWorker(
        setup.database,
        transport=httpx.MockTransport(lambda r: answer(usage={"total_tokens": 10**30})),
    )
    assert asyncio.run(worker.run_once())
    assert TaskService(setup.database).detail(setup.agent.id, created.id).task.status == (
        "completed"
    )
    [entry] = setup.entries()
    assert entry.status == "confirmed" and entry.usage_kind == "unknown"


def test_blocked_chat_leaves_no_orphan_message(setup):
    setup.limit(5000, output_allowance=4999)
    with setup.store.transaction(write=False) as uow:
        before = uow.messages.list(conversation_id=setup.chat.id)
    for _ in range(2):
        with pytest.raises(ProviderError) as caught:
            setup.send("Bloqueada")
        assert caught.value.code == "budget_exhausted"
    with setup.store.transaction(write=False) as uow:
        assert uow.messages.list(conversation_id=setup.chat.id) == before
    assert not setup.requests and not setup.entries()


@pytest.mark.parametrize(
    ("failure", "status"),
    [
        (httpx.Response(400, json={}), "released"),
        (httpx.Response(413, json={}), "released"),
        (httpx.Response(422, json={}), "released"),
        (httpx.Response(408, json={}), "unknown"),
        (httpx.Response(409, json={}), "unknown"),
        (httpx.Response(500, json={}), "unknown"),
        (httpx.ConnectError("recusada"), "released"),
        (httpx.ConnectTimeout("sem conexão"), "released"),
        (httpx.ReadError("cortada"), "unknown"),
    ],
)
def test_release_requires_proof_of_no_generation(setup, failure, status):
    def handler(request):
        if isinstance(failure, Exception):
            raise failure
        return failure

    with pytest.raises(ProviderError):
        setup.send(handler=handler)
    [entry] = setup.entries()
    assert entry.status == status


def test_concurrent_write_during_authorization_is_handled_conflict(setup, monkeypatch):
    import apsw

    from bees_core.providers import service as service_module

    def busy(*args, **kwargs):
        raise apsw.BusyError("database is locked")

    monkeypatch.setattr(service_module, "reserve", busy)
    with pytest.raises(ProviderError) as caught:
        setup.send()
    assert caught.value.code == "state_conflict"
    assert not setup.requests and not setup.entries()


def test_sql_window_matches_entry_rule_and_partial_is_not_reported(setup):
    now = utc_now()
    shapes = [
        {"status": "reserved"},
        {"status": "released"},
        {"status": "unknown"},
        {"status": "confirmed", "usage_kind": "reported", "total_tokens": 150},
        {"status": "confirmed", "usage_kind": "reported", "input_tokens": 7, "output_tokens": 3},
        {"status": "confirmed", "usage_kind": "reported", "output_tokens": 30},
        {"status": "confirmed", "usage_kind": "reported", "total_tokens": 0},
        {"status": "confirmed", "usage_kind": "estimated", "total_tokens": 40},
        {"status": "confirmed", "usage_kind": "unknown"},
    ]
    with setup.store.transaction() as uow:
        for shape in shapes:
            uow.usage_entries.create(
                UsageEntry(
                    agent_id=setup.agent.id,
                    source="chat",
                    conversation_id=setup.chat.id,
                    provider_kind="openai_compatible",
                    model="fixture-text",
                    reserved_tokens=1000,
                    estimate_method="chars_div_3_v1",
                    settled_at=None if shape["status"] == "reserved" else now,
                    **shape,
                )
            )
    entries = setup.entries()
    with setup.store.transaction(write=False) as uow:
        window = uow.usage_entries.window(setup.agent.id, now - timedelta(days=1))
    assert window["counted_tokens"] == sum(entry.counted_tokens() for entry in entries)
    assert window["counted_tokens"] == 1000 + 0 + 1000 + 150 + 10 + 1000 + 1000 + 40 + 1000
    assert window["reported_tokens"] == 160
    assert window["open_reservations"] == 1 and window["entries"] == len(shapes)
    # Incerto, parcial, zerado e confirmado sem uso.
    assert window["unknown_entries"] == 4


def test_worker_checks_budget_before_asking_for_decision(setup):
    from bees_core.policies import PolicyInput, PolicyService

    PolicyService(setup.database).create(
        setup.agent.id, PolicyInput(name="Perguntar sempre", effect="ask")
    )
    setup.limit(100, output_allowance=50)
    created = task(setup)
    worker = TaskWorker(setup.database, transport=httpx.MockTransport(lambda r: answer()))
    assert asyncio.run(worker.run_once())
    detail = TaskService(setup.database).detail(setup.agent.id, created.id)
    assert detail.task.status == "paused" and detail.runs[-1].error == "budget_exhausted"
    with setup.store.transaction(write=False) as uow:
        assert uow.approvals.list(limit=100) == []
