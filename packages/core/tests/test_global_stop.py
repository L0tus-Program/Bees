"""Parada global com SQLite e HTTP de teste: zero efeitos novos e retomada explícita."""

import asyncio
import json
from importlib import resources
from uuid import uuid4

import httpx
import pytest

from bees_core import execution as execution_module
from bees_core.execution import TaskWorker
from bees_core.models import Agent, Conversation
from bees_core.providers import service as service_module
from bees_core.providers.contracts import ProviderCapabilities, ProviderConfig
from bees_core.providers.errors import ProviderError
from bees_core.providers.service import ProviderService
from bees_core.safety import SafetyCommand, SafetyService
from bees_core.storage.database import Database
from bees_core.storage.store import (
    IntegrityError,
    InvalidTransition,
    RevisionConflict,
    StateStore,
)
from bees_core.tasks import TaskInput, TaskService


def answer(content="Resposta própria de teste"):
    return httpx.Response(
        200,
        json={
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": content},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        },
    )


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
        self.safety = SafetyService(self.database)
        self.requests = []

    def command(self, kind, **values):
        state = self.safety.current()
        return self.safety.command(
            SafetyCommand(
                client_request_id=values.pop("client_request_id", uuid4()),
                kind=kind,
                expected_revision=values.pop("expected_revision", state["revision"]),
                **values,
            )
        )

    def provider(self):
        def record(request):
            self.requests.append(request)
            return answer()

        return ProviderService(self.database, transport=httpx.MockTransport(record))

    def send(self, text="Uma pergunta própria de teste"):
        return asyncio.run(self.provider().chat(self.agent.id, self.chat.id, text))

    def messages(self):
        with self.store.transaction(write=False) as uow:
            return uow.messages.list(conversation_id=self.chat.id)

    def task(self):
        return TaskService(self.database).create(
            self.agent.id,
            TaskInput(
                client_request_id=uuid4(),
                title="Relatório",
                objective="Explique filas duráveis.",
                expected_result="Três recomendações.",
            ),
        )

    def worker(self):
        return TaskWorker(
            self.database,
            transport=httpx.MockTransport(
                lambda request: self.requests.append(request) or answer()
            ),
        )

    def detail(self, task):
        return TaskService(self.database).detail(self.agent.id, task.id)


@pytest.fixture
def setup(tmp_path):
    return Setup(tmp_path)


def test_stop_and_resume_are_durable_cas_and_idempotent(setup):
    initial = setup.safety.current()
    assert (initial["status"], initial["generation"], initial["revision"]) == ("running", 0, 1)
    request_id = uuid4()
    stopped = setup.command("stop", client_request_id=request_id, reason="Revisar custos")
    assert (stopped["status"], stopped["generation"], stopped["revision"]) == ("stopped", 1, 2)
    assert stopped["reason"] == "Revisar custos"
    # Mesmo UUID e conteúdo: replay sem reaplicar; outro conteúdo: conflito.
    replay = setup.command(
        "stop", client_request_id=request_id, expected_revision=1, reason="Revisar custos"
    )
    assert replay["revision"] == 2 and replay["generation"] == 1
    with pytest.raises(IntegrityError):
        setup.command("stop", client_request_id=request_id, expected_revision=1)
    with pytest.raises(InvalidTransition):
        setup.command("stop")
    with pytest.raises(RevisionConflict):
        setup.command("resume", expected_revision=1)
    resumed = setup.command("resume")
    assert (resumed["status"], resumed["generation"], resumed["revision"]) == ("running", 1, 3)
    restarted = SafetyService(Database(setup.database.path)).current()
    assert restarted == resumed


def test_sql_rejects_invalid_transitions_and_command_edits(setup):
    setup.command("stop")
    for sql in (
        "UPDATE safety_control SET status='running',revision=revision+1,generation=0",
        "UPDATE safety_control SET status='running',revision=revision+5",
        "UPDATE safety_control SET reason='x'",
        "DELETE FROM safety_control",
        "UPDATE safety_commands SET kind='resume'",
        "DELETE FROM safety_commands",
    ):
        with pytest.raises(Exception, match="invalid|cannot|immutable|append-only"):
            with setup.database.transaction() as connection:
                connection.execute(sql)
    assert setup.safety.current()["status"] == "stopped"


def test_stopped_chat_sends_nothing_and_leaves_no_message(setup):
    setup.command("stop")
    before = setup.messages()
    with pytest.raises(ProviderError) as caught:
        setup.send()
    assert caught.value.code == "global_stop"
    assert not setup.requests and setup.messages() == before
    setup.command("resume")
    setup.send()
    assert len(setup.requests) == 1


def test_stop_and_resume_during_chat_preflight_still_blocks_old_generation(setup, monkeypatch):
    original = service_module.create_adapter

    def adapter(kind, resolver, *, transport=None, before_generation=None):
        real = original(kind, resolver, transport=transport, before_generation=before_generation)

        class Interleaved:
            async def complete(self, config, request):
                # Parada e retomada entre a pré-checagem e a autorização.
                setup.command("stop")
                setup.command("resume")
                return await real.complete(config, request)

        return Interleaved()

    monkeypatch.setattr(service_module, "create_adapter", adapter)
    with pytest.raises(ProviderError) as caught:
        setup.send()
    assert caught.value.code == "global_stop" and not setup.requests


def test_stopped_worker_claims_nothing_and_resumes_automatically(setup):
    created = setup.task()
    setup.command("stop")
    assert not asyncio.run(setup.worker().run_once())
    assert setup.detail(created).task.status == "queued" and not setup.requests
    setup.command("resume")
    assert asyncio.run(setup.worker().run_once())
    assert setup.detail(created).task.status == "completed" and len(setup.requests) == 1


def test_stop_between_claim_and_dispatch_requeues_without_effect(setup, monkeypatch):
    created = setup.task()
    original = TaskWorker._prepare

    def prepare(self, claim):
        result = original(self, claim)
        # Parada e retomada logo após o preparo: a geração antiga não pode seguir.
        setup.command("stop")
        setup.command("resume")
        return result

    monkeypatch.setattr(TaskWorker, "_prepare", prepare)
    assert asyncio.run(setup.worker().run_once())
    detail = setup.detail(created)
    assert detail.task.status == "queued" and detail.task.calls_started == 0
    assert not setup.requests
    assert all(call.status != "dispatch_started" for call in detail.calls)
    monkeypatch.undo()
    assert asyncio.run(setup.worker().run_once())
    assert setup.detail(created).task.status == "completed"


def test_stop_after_preflight_blocks_dispatch_and_requeues(setup, monkeypatch):
    created = setup.task()
    original = execution_module.create_adapter

    def adapter(kind, resolver, *, transport=None, before_generation=None):
        real = original(kind, resolver, transport=transport, before_generation=before_generation)

        class Stopped:
            async def complete(self, config, request):
                setup.command("stop")
                return await real.complete(config, request)

        return Stopped()

    monkeypatch.setattr(execution_module, "create_adapter", adapter)
    assert asyncio.run(setup.worker().run_once())
    detail = setup.detail(created)
    assert not setup.requests and detail.task.calls_started == 0
    assert detail.task.status == "queued"
    with setup.store.transaction(write=False) as uow:
        assert not uow.usage_entries.since(setup.agent.id, detail.task.created_at)
    monkeypatch.undo()
    assert not asyncio.run(setup.worker().run_once())
    setup.command("resume")
    assert asyncio.run(setup.worker().run_once())
    assert setup.detail(created).task.status == "completed" and len(setup.requests) == 1


def test_stop_during_task_dispatch_keeps_result_and_blocks_next_claim(setup):
    idle = {"model_calls": 0, "tool_actions": 0, "chat_reservations": 0, "provisioning_effects": 0}
    assert setup.safety.current()["in_flight"] == idle
    seen = []

    def respond(request):
        # Parada com a chamada já enviada: não cancela, o resultado é registrado.
        seen.append(setup.safety.current()["in_flight"])
        setup.command("stop")
        setup.requests.append(request)
        return answer()

    first, second = setup.task(), setup.task()
    worker = TaskWorker(setup.database, transport=httpx.MockTransport(respond))
    assert asyncio.run(worker.run_once())
    assert seen == [idle | {"model_calls": 1}]
    assert setup.detail(first).task.status == "completed"
    assert not asyncio.run(worker.run_once())
    assert setup.detail(second).task.status == "queued" and len(setup.requests) == 1
    assert setup.safety.current()["in_flight"] == idle


def test_stop_during_chat_request_keeps_answer_and_counts_reservation(setup):
    seen = []

    def respond(request):
        seen.append(setup.safety.current()["in_flight"]["chat_reservations"])
        setup.command("stop")
        return answer("Resposta paga preservada")

    service = ProviderService(setup.database, transport=httpx.MockTransport(respond))
    asyncio.run(service.chat(setup.agent.id, setup.chat.id, "Pergunta própria de teste"))
    assert seen == [1]
    assert [message.content for message in setup.messages()][-1] == "Resposta paga preservada"
    assert setup.safety.current()["in_flight"]["chat_reservations"] == 0
    with pytest.raises(ProviderError):
        setup.send()


def test_commands_leave_audit_events_without_reason_text(setup):
    setup.command("stop", reason="Motivo privado de teste")
    setup.command("resume")
    with setup.database.transaction(write=False) as connection:
        rows = connection.execute(
            "SELECT event_type,payload_json,actor FROM domain_events "
            "WHERE entity_type='safety_control' ORDER BY seq"
        ).fetchall()
    assert [(row[0], row[2]) for row in rows] == [("stopped", "user"), ("running", "user")]
    assert json.loads(rows[0][1]) == {
        "revision": 2,
        "previous_revision": 1,
        "generation": 1,
        "previous_status": "running",
    }
    assert all("Motivo" not in row[1] for row in rows)


def test_legacy_provisioning_claim_without_generation_counts_as_zero(setup):
    from bees_core.provisioning import ProvisioningError, ProvisioningService

    legacy = {"safety_generation": None}
    with setup.database.transaction(write=False) as connection:
        ProvisioningService._running(connection, legacy)
    setup.command("stop")
    setup.command("resume")
    with setup.database.transaction(write=False) as connection:
        # Claim anterior à migração é da geração 0 e fica cercado após a primeira parada.
        with pytest.raises(ProvisioningError, match="provisioning_global_stop"):
            ProvisioningService._running(connection, legacy)
        ProvisioningService._running(connection, {"safety_generation": 1})


def test_upgrade_ten_to_eleven_starts_running(tmp_path, monkeypatch):
    from bees_core.storage import database as database_module

    original = resources.files("bees_core.storage.migrations")
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    for file in original.iterdir():
        if file.name.endswith(".sql") and int(file.name[:4]) < 11:
            (migrations / file.name).write_bytes(file.read_bytes())
    monkeypatch.setattr(database_module.resources, "files", lambda _: migrations)
    database = Database(tmp_path / "legacy.sqlite3")
    database.initialize()
    (migrations / "0011_global_stop.sql").write_bytes(
        (original / "0011_global_stop.sql").read_bytes()
    )
    database.initialize()
    assert database.schema_version() == 11
    assert len(list(tmp_path.glob("legacy.sqlite3.backup-*.sqlite3"))) == 1
    state = SafetyService(database).current()
    assert (state["status"], state["generation"], state["revision"]) == ("running", 0, 1)
