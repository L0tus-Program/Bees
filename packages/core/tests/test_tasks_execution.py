"""Execução textual real com SQLite e HTTP de teste, incluindo falhas de processo."""

import asyncio
import json
import subprocess
import sys
import time
from datetime import timedelta
from uuid import uuid4

import httpx
import pytest
from pydantic import ValidationError

from bees_core.execution import TaskWorker
from bees_core.memory import MemoryInput, MemoryService
from bees_core.models import Agent, Conversation, utc_now
from bees_core.profiles import AgentProfile
from bees_core.providers.contracts import (
    ChatMessage,
    ChatResponse,
    ProviderCapabilities,
    ProviderConfig,
)
from bees_core.providers.errors import ProviderError
from bees_core.providers.secrets import EnvSecretResolver
from bees_core.providers.service import ProviderService
from bees_core.storage.database import Database
from bees_core.storage.store import Execution, NotFoundError, RevisionConflict, StateStore
from bees_core.tasks import TaskCommandInput, TaskError, TaskInput, TaskService


def fixture_state(tmp_path, *, config=None):
    database = Database(tmp_path / "state.sqlite3")
    database.initialize()
    store = StateStore(database)
    config = config or ProviderConfig(
        kind="openai_compatible",
        endpoint="https://fixture.test/v1",
        model="fixture-text",
        capabilities=ProviderCapabilities(),
    )
    agent = Agent(name="Abelha de teste", provider_config=config.model_dump(mode="json"))
    chat = Conversation(agent_id=agent.id)
    with store.transaction() as uow:
        uow.agents.create(agent)
        uow.conversations.create(chat)
    service = TaskService(database)
    return database, store, agent, chat, service


def task_input(**updates):
    return TaskInput(
        client_request_id=uuid4(),
        title="Escrever relatório",
        objective="Explique filas duráveis.",
        expected_result="Texto com três recomendações.",
        **updates,
    )


def answer(content="Resultado útil", *, finish="stop"):
    return httpx.Response(
        200,
        json={
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": content},
                    "finish_reason": finish,
                }
            ]
        },
    )


def command(service, agent, task, kind, **updates):
    current = service.detail(agent.id, task.id).task
    return service.command(
        agent.id,
        task.id,
        TaskCommandInput(
            client_request_id=uuid4(),
            expected_revision=current.revision,
            kind=kind,
            **updates,
        ),
    )


def test_create_replay_cas_and_scope_survive_restart(tmp_path):
    database, store, agent, chat, service = fixture_state(tmp_path)
    value = task_input()
    task = service.create(agent.id, value)
    assert task.conversation_id != chat.id
    assert (task.max_calls, task.max_active_seconds) == (3, 120)
    reopened = TaskService(Database(database.path))
    assert reopened.create(agent.id, value).id == task.id
    assert len(reopened.list(agent.id)) == 1
    with pytest.raises(TaskError, match="idempotency_conflict"):
        reopened.create(agent.id, value.model_copy(update={"objective": "Outra tarefa"}))
    with pytest.raises(NotFoundError):
        reopened.detail(uuid4(), task.id)
    pause = TaskCommandInput(
        client_request_id=uuid4(), expected_revision=task.revision, kind="pause"
    )
    paused = reopened.command(agent.id, task.id, pause)
    assert reopened.command(agent.id, task.id, pause).id == paused.id
    with pytest.raises(RevisionConflict):
        reopened.command(agent.id, task.id, pause.model_copy(update={"client_request_id": uuid4()}))
    with store.transaction(write=False) as uow:
        assert len(uow.task_commands.list(task_id=task.id)) == 1


def test_one_generation_atomic_result_and_no_replay(tmp_path):
    database, _, agent, _, service = fixture_state(tmp_path)
    task = service.create(agent.id, task_input())
    requests = []

    def handler(request):
        requests.append(json.loads(request.content))
        return answer()

    worker = TaskWorker(database, transport=httpx.MockTransport(handler))
    assert asyncio.run(worker.run_once())
    detail = TaskService(Database(database.path)).detail(agent.id, task.id)
    assert detail.task.status == "completed"
    assert detail.task.calls_started == 1
    assert detail.task.active_milliseconds > 0
    assert detail.calls[0].status == "confirmed"
    assert detail.calls[0].output_message_id == detail.messages[-1].id
    assert detail.runs[-1].checkpoint["result_message_id"] == str(detail.messages[-1].id)
    assert detail.messages[-1].content == "Resultado útil"
    assert len(requests) == 1
    assert not asyncio.run(TaskWorker(Database(database.path)).run_once())
    assert not requests[0].get("tools")
    assert "request" not in detail.calls[0].model_dump()


def test_pause_keeps_inflight_response_resume_does_not_charge_again(tmp_path):
    database, _, agent, _, service = fixture_state(tmp_path)
    task = service.create(agent.id, task_input(max_calls=1))

    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        count = 0

        async def handler(request):
            nonlocal count
            count += 1
            entered.set()
            await release.wait()
            return answer("Resposta preservada")

        worker = TaskWorker(database, transport=httpx.MockTransport(handler), poll_seconds=0.01)
        running = asyncio.create_task(worker.run_once())
        await entered.wait()
        command(service, agent, task, "pause")
        release.set()
        await running
        detail = service.detail(agent.id, task.id)
        assert detail.task.status == "paused"
        assert detail.calls[0].status == "confirmed"
        assert detail.messages[-1].content == "Resposta preservada"
        completed = command(service, agent, task, "resume")
        assert completed.status == "completed"
        assert not await worker.run_once()
        assert count == 1

    asyncio.run(scenario())


def test_redirect_confirms_old_response_without_publishing_and_uses_next_call(tmp_path):
    database, _, agent, _, service = fixture_state(tmp_path)
    task = service.create(agent.id, task_input())

    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        bodies = []

        async def handler(request):
            bodies.append(json.loads(request.content))
            if len(bodies) == 1:
                entered.set()
                await release.wait()
                return answer("Versão antiga")
            return answer("Versão redirecionada")

        worker = TaskWorker(database, transport=httpx.MockTransport(handler), poll_seconds=0.01)
        running = asyncio.create_task(worker.run_once())
        await entered.wait()
        command(service, agent, task, "redirect", instruction="Priorize integridade.")
        release.set()
        await running
        detail = service.detail(agent.id, task.id)
        assert detail.task.status == "queued"
        assert detail.calls[0].obsolete
        assert detail.calls[0].status == "confirmed"
        assert detail.calls[0].output_message_id is None
        assert not any(message.content == "Versão antiga" for message in detail.messages)
        assert await worker.run_once()
        final = service.detail(agent.id, task.id)
        assert final.task.status == "completed"
        assert final.task.calls_started == 2
        assert "Priorize integridade." in bodies[1]["messages"][-1]["content"]
        assert final.commands[0].instruction == "Priorize integridade."

    asyncio.run(scenario())


def test_cancel_after_acceptance_is_unknown_and_does_not_block_other_task(tmp_path):
    database, _, agent, _, service = fixture_state(tmp_path)
    task = service.create(agent.id, task_input())

    async def scenario():
        entered, cancelled = asyncio.Event(), asyncio.Event()

        async def handler(request):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        worker = TaskWorker(database, transport=httpx.MockTransport(handler), poll_seconds=0.01)
        running = asyncio.create_task(worker.run_once())
        await entered.wait()
        command(service, agent, task, "cancel")
        await running
        assert cancelled.is_set()
        detail = service.detail(agent.id, task.id)
        assert detail.task.status == "cancelled"
        assert detail.calls[0].status == "outcome_unknown"
        assert detail.task.calls_started == 1
        assert detail.runs[-1].checkpoint["attention_required"]
        with pytest.raises(TaskError, match="terminal_task"):
            command(service, agent, task, "resume", acknowledge_unknown=True)
        second = service.create(agent.id, task_input())
        other = TaskWorker(database, transport=httpx.MockTransport(lambda request: answer()))
        assert await other.run_once()
        assert service.detail(agent.id, second.id).task.status == "completed"

    asyncio.run(scenario())


def test_unknown_requires_explicit_ack_preserves_old_journal_and_budget(tmp_path):
    database, _, agent, _, service = fixture_state(tmp_path)
    task = service.create(agent.id, task_input())

    def timeout(request):
        raise httpx.ReadTimeout("private-provider-body-secret", request=request)

    assert asyncio.run(TaskWorker(database, transport=httpx.MockTransport(timeout)).run_once())
    with pytest.raises(TaskError, match="unknown_requires_ack"):
        command(service, agent, task, "resume")
    command(service, agent, task, "resume", acknowledge_unknown=True)
    assert asyncio.run(
        TaskWorker(database, transport=httpx.MockTransport(lambda r: answer())).run_once()
    )
    detail = service.detail(agent.id, task.id)
    assert detail.task.calls_started == 2
    assert [call.status for call in detail.calls] == ["outcome_unknown", "confirmed"]
    assert detail.calls[0].acknowledged
    assert "private-provider-body-secret" not in detail.model_dump_json()


def test_call_limit_cannot_be_reset_by_unknown_ack(tmp_path):
    database, _, agent, _, service = fixture_state(tmp_path)
    task = service.create(agent.id, task_input(max_calls=1))
    assert asyncio.run(
        TaskWorker(
            database,
            transport=httpx.MockTransport(
                lambda request: httpx.Response(503, json={"private": "secret"})
            ),
        ).run_once()
    )
    with pytest.raises(TaskError, match="task_limit_reached"):
        command(service, agent, task, "resume", acknowledge_unknown=True)
    detail = service.detail(agent.id, task.id)
    assert detail.task.calls_started == 1
    assert not detail.calls[0].acknowledged
    assert len(detail.commands) == 0


def test_chat_remains_concurrent_in_separate_conversation(tmp_path):
    database, _, agent, chat, service = fixture_state(tmp_path)
    task = service.create(agent.id, task_input())

    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()

        async def handler(request):
            if "Objetivo:" in json.loads(request.content)["messages"][-1]["content"]:
                entered.set()
                await release.wait()
                return answer("Resultado da tarefa")
            return answer("Conversa livre")

        transport = httpx.MockTransport(handler)
        running = asyncio.create_task(TaskWorker(database, transport=transport).run_once())
        await entered.wait()
        result = await ProviderService(database, transport=transport).chat(agent.id, chat.id, "Oi")
        assert result.message.content == "Conversa livre"
        release.set()
        await running
        assert service.detail(agent.id, task.id).task.status == "completed"

    asyncio.run(scenario())


def test_two_workers_cannot_dispatch_same_task(tmp_path):
    database, _, agent, _, service = fixture_state(tmp_path)
    service.create(agent.id, task_input())

    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        count = 0

        async def handler(request):
            nonlocal count
            count += 1
            entered.set()
            await release.wait()
            return answer()

        transport = httpx.MockTransport(handler)
        first = asyncio.create_task(TaskWorker(database, transport=transport).run_once())
        await entered.wait()
        assert not await TaskWorker(Database(database.path), transport=transport).run_once()
        release.set()
        await first
        assert count == 1

    asyncio.run(scenario())


@pytest.mark.parametrize("change", ["memory", "config"])
def test_context_changes_confirm_response_as_obsolete_without_stale_result(tmp_path, change):
    database, store, agent, _, service = fixture_state(tmp_path)
    task = service.create(agent.id, task_input())

    def handler(request):
        if change == "memory":
            MemoryService(database).create(
                MemoryInput(scope="user", content="Prefiro filas com revisão otimista.")
            )
        else:
            with store.transaction() as uow:
                current = uow.agents.get(agent.id)
                uow.agents.update(
                    current.model_copy(update={"instructions": "Nova instrução"}), current.revision
                )
        return answer("Resposta de contexto antigo")

    assert asyncio.run(TaskWorker(database, transport=httpx.MockTransport(handler)).run_once())
    detail = service.detail(agent.id, task.id)
    assert detail.task.status == "paused"
    assert detail.calls[0].status == "confirmed"
    assert detail.calls[0].obsolete
    assert detail.calls[0].output_message_id is None
    assert detail.runs[-1].error == "context_changed"


def test_missing_secret_pauses_before_input_or_dispatch(tmp_path):
    config = ProviderConfig(
        kind="openai_compatible",
        endpoint="https://fixture.test/v1",
        model="fixture-text",
        secret_ref="env:BEES_MISSING_TEST_KEY",
        capabilities=ProviderCapabilities(),
    )
    database, _, agent, _, service = fixture_state(tmp_path, config=config)
    task = service.create(agent.id, task_input())

    def forbidden(request):
        pytest.fail("Credencial ausente não pode fazer rede")

    worker = TaskWorker(database, EnvSecretResolver({}), transport=httpx.MockTransport(forbidden))
    assert asyncio.run(worker.run_once())
    detail = service.detail(agent.id, task.id)
    assert detail.task.status == "paused"
    assert detail.task.calls_started == 0
    assert not detail.calls and not detail.messages
    assert detail.runs[-1].error == "secret_unavailable"


def test_shutdown_cancellation_records_unknown(tmp_path):
    database, _, agent, _, service = fixture_state(tmp_path)
    task = service.create(agent.id, task_input())

    async def scenario():
        entered = asyncio.Event()

        async def handler(request):
            entered.set()
            await asyncio.Event().wait()

        running = asyncio.create_task(
            TaskWorker(database, transport=httpx.MockTransport(handler)).run_once()
        )
        await entered.wait()
        running.cancel()
        with pytest.raises(asyncio.CancelledError):
            await running
        detail = service.detail(agent.id, task.id)
        assert detail.calls[0].status == "outcome_unknown"
        assert detail.calls[0].error_code == "worker_shutdown"

    asyncio.run(scenario())


@pytest.mark.parametrize("phase", ["prepared", "dispatched"])
def test_abrupt_process_death_before_or_after_dispatch(tmp_path, phase):
    database, store, agent, _, service = fixture_state(tmp_path)
    task = service.create(agent.id, task_input())
    marker = tmp_path / "accepted.txt"
    script = """
import asyncio, os, sys
from pathlib import Path
import httpx
from bees_core.execution import TaskWorker
from bees_core.models import utc_now
from bees_core.storage.database import Database
database=Database(sys.argv[1])
worker=TaskWorker(database, lease_seconds=5)
if sys.argv[3]=='prepared':
    with worker.store.transaction() as uow:
        claim=uow.execution.claim_next(worker.owner_id,now=utc_now(),ttl_seconds=5)
    worker._prepare(claim)
    os._exit(19)
def accepted(request):
    Path(sys.argv[2]).write_text('one accepted request',encoding='utf-8')
    os._exit(19)
worker.providers.transport=httpx.MockTransport(accepted)
asyncio.run(worker.run_once())
"""
    crashed = subprocess.run(
        [sys.executable, "-c", script, str(database.path), str(marker), phase], check=False
    )
    assert crashed.returncode == 19
    with store.transaction() as uow:
        assert uow.execution.recover_expired(now=utc_now() + timedelta(seconds=6)) == 1
    requests = []
    worker = TaskWorker(
        Database(database.path),
        transport=httpx.MockTransport(
            lambda request: requests.append(request) or answer("Após reinício")
        ),
    )
    if phase == "dispatched":
        assert marker.exists()
        assert not asyncio.run(worker.run_once())
        detail = service.detail(agent.id, task.id)
        assert detail.calls[0].status == "outcome_unknown"
        assert detail.task.status == "paused"
        assert not requests
    else:
        assert not marker.exists()
        assert asyncio.run(worker.run_once())
        detail = service.detail(agent.id, task.id)
        assert detail.task.status == "completed"
        assert detail.task.calls_started == 1
        assert len(detail.messages) == 2
        assert len(requests) == 1


def test_stale_owner_cannot_commit_after_lease_expiration(tmp_path):
    database, store, agent, _, service = fixture_state(tmp_path)
    task = service.create(agent.id, task_input())
    worker = TaskWorker(database)
    with store.transaction() as uow:
        claim = uow.execution.claim_next(worker.owner_id, now=utc_now(), ttl_seconds=5)
    call, prepared = worker._prepare(claim)
    with store.transaction() as uow:
        call = uow.execution.begin_call(claim, call, now=utc_now())
    with store.transaction() as uow:
        uow.execution.recover_expired(now=utc_now() + timedelta(seconds=6))
    response = ChatResponse(
        message=ChatMessage(role="assistant", content="Resposta velha"), finish_reason="stop"
    )
    with pytest.raises(RevisionConflict):
        worker._finish(claim, call, prepared, elapsed_ms=1, response=response)
    assert not any(
        record.role == "assistant" for record in service.detail(agent.id, task.id).messages
    )


def test_copied_invalid_input_is_revalidated_before_persistence(tmp_path):
    _, _, agent, _, service = fixture_state(tmp_path)
    with pytest.raises(ValidationError):
        service.create(agent.id, task_input().model_copy(update={"max_calls": 1000}))
    assert not service.list(agent.id)


@pytest.mark.parametrize("change", ["profile_model", "memory"])
def test_resume_ready_result_rechecks_current_context_and_preserves_model_provenance(
    tmp_path, change
):
    database, store, agent, _, service = fixture_state(tmp_path)
    task = service.create(agent.id, task_input())

    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        bodies = []

        async def handler(request):
            bodies.append(json.loads(request.content))
            if len(bodies) == 1:
                entered.set()
                await release.wait()
                return answer("Resposta antiga")
            return answer("Resposta atual")

        transport = httpx.MockTransport(handler)
        worker = TaskWorker(database, transport=transport, poll_seconds=0.01)
        running = asyncio.create_task(worker.run_once())
        await entered.wait()
        command(service, agent, task, "pause")
        release.set()
        await running
        first = service.detail(agent.id, task.id)
        assert first.runs[-1].checkpoint["result_ready"]
        if change == "profile_model":
            with store.transaction(write=False) as uow:
                current = uow.agents.get(agent.id)
            config = ProviderConfig.model_validate(current.provider_config).model_copy(
                update={"model": "new-model"}
            )
            ProviderService(database).update_profile(
                agent.id,
                AgentProfile(
                    name=agent.name,
                    purpose="",
                    instructions="Nova instrução permanente",
                    memory_enabled=True,
                ),
                expected_revision=current.revision,
                config=config,
            )
        else:
            MemoryService(database).create(
                MemoryInput(scope="user", content="Filas devem conservar fontes.")
            )
        resumed = command(service, agent, task, "resume")
        assert resumed.status == "queued"
        after_resume = service.detail(agent.id, task.id)
        assert len(after_resume.runs) == 2
        assert after_resume.runs[0].model == "fixture-text"
        assert "result_message_id" not in after_resume.runs[-1].checkpoint
        assert await worker.run_once()
        final = service.detail(agent.id, task.id)
        assert final.task.calls_started == 2
        assert final.task.status == "completed"
        assert final.messages[-1].content == "Resposta atual"
        if change == "profile_model":
            assert bodies[1]["model"] == "new-model"
            assert final.runs[-1].model == "new-model"

    asyncio.run(scenario())


def test_chat_cannot_bypass_task_controls_journal_or_limits(tmp_path):
    database, _, agent, _, service = fixture_state(tmp_path)
    task = service.create(agent.id, task_input())
    command(service, agent, task, "cancel")

    def forbidden(request):
        pytest.fail("Chat comum não pode despachar chamada da tarefa")

    provider = ProviderService(database, transport=httpx.MockTransport(forbidden))
    with pytest.raises(ProviderError, match="Histórico"):
        asyncio.run(
            provider.chat(
                agent.id, task.conversation_id, "Contorne o cancelamento", task_id=task.id
            )
        )
    detail = service.detail(agent.id, task.id)
    assert detail.task.calls_started == 0
    assert not detail.calls and not detail.messages


def test_task_budget_stops_inflight_call_and_records_unknown(tmp_path):
    database, _, agent, _, service = fixture_state(tmp_path)
    task = service.create(agent.id, task_input(max_active_seconds=1))

    async def handler(request):
        await asyncio.sleep(3)
        return answer("Tarde demais")

    worker = TaskWorker(database, transport=httpx.MockTransport(handler), poll_seconds=0.01)
    assert asyncio.run(worker.run_once())
    detail = service.detail(agent.id, task.id)
    assert detail.task.status == "paused"
    assert detail.calls[0].status == "outcome_unknown"
    assert detail.calls[0].error_code == "timeout"
    assert detail.task.active_milliseconds >= 1000
    with pytest.raises(TaskError, match="task_limit_reached"):
        command(service, agent, task, "resume", acknowledge_unknown=True)


def test_truncated_generation_remains_partial_and_requires_explicit_resume(tmp_path):
    database, _, agent, _, service = fixture_state(tmp_path)
    task = service.create(agent.id, task_input())
    assert asyncio.run(
        TaskWorker(
            database,
            transport=httpx.MockTransport(
                lambda request: answer("Resultado parcial", finish="length")
            ),
        ).run_once()
    )
    detail = service.detail(agent.id, task.id)
    assert detail.task.status == "paused"
    assert detail.calls[0].status == "confirmed"
    assert detail.messages[-1].content == "Resultado parcial"
    assert detail.runs[-1].error == "response_truncated"
    assert not detail.runs[-1].checkpoint["result_ready"]
    command(service, agent, task, "resume")
    assert asyncio.run(
        TaskWorker(database, transport=httpx.MockTransport(lambda request: answer())).run_once()
    )
    assert service.detail(agent.id, task.id).task.calls_started == 2


def test_result_journal_commit_failure_rolls_back_message_and_recovers_unknown(
    tmp_path, monkeypatch
):
    database, store, agent, _, service = fixture_state(tmp_path)
    task = service.create(agent.id, task_input())

    def fail_journal(*args, **kwargs):
        raise RevisionConflict("Falha controlada ao confirmar journal")

    worker = TaskWorker(database, transport=httpx.MockTransport(lambda request: answer()))
    with monkeypatch.context() as patch:
        patch.setattr(Execution, "finish_call", fail_journal)
        with pytest.raises(RevisionConflict):
            asyncio.run(worker.run_once())
    detail = service.detail(agent.id, task.id)
    assert detail.calls[0].status == "dispatch_started"
    assert detail.calls[0].output_message_id is None
    assert not any(record.role == "assistant" for record in detail.messages)
    assert detail.task.status == "running"
    with store.transaction() as uow:
        uow.execution.recover_expired(now=utc_now() + timedelta(seconds=31))
    assert service.detail(agent.id, task.id).calls[0].status == "outcome_unknown"


def test_preparation_time_exhausts_budget_before_network(tmp_path, monkeypatch):
    database, _, agent, _, service = fixture_state(tmp_path)
    task = service.create(agent.id, task_input(max_active_seconds=1))

    def forbidden(request):
        pytest.fail("Preparação consumiu todo o orçamento antes de qualquer HTTP")

    worker = TaskWorker(database, transport=httpx.MockTransport(forbidden))
    original = worker.providers.prepare_chat

    def slow_prepare(*args, **kwargs):
        snapshot = original(*args, **kwargs)
        time.sleep(1.05)
        return snapshot

    monkeypatch.setattr(worker.providers, "prepare_chat", slow_prepare)
    assert asyncio.run(worker.run_once())
    detail = service.detail(agent.id, task.id)
    assert detail.task.calls_started == 0
    assert detail.task.active_milliseconds >= 1000
    assert detail.calls[0].status == "cancelled"
    assert detail.runs[-1].error == "task_limit_reached"
    with pytest.raises(TaskError, match="task_limit_reached"):
        command(service, agent, task, "resume")
