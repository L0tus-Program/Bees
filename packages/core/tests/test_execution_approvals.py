"""Consentimento humano no despacho real, inclusive falhas depois do efeito."""

import asyncio
import subprocess
import sys
from datetime import timedelta
from uuid import uuid4

import httpx
import pytest
from test_execution_policies import answer, rule, state, task

from bees_core.approvals import ApprovalDecisionInput, ApprovalRevokeInput, ApprovalService
from bees_core.execution import TaskWorker
from bees_core.models import utc_now
from bees_core.policies import PolicyUpdate
from bees_core.storage.database import Database
from bees_core.tasks import TaskCommandInput


def approve(service, agent, pending, decision="allow_once"):
    return service.decide(
        agent.id,
        pending.id,
        ApprovalDecisionInput(
            client_request_id=uuid4(),
            expected_revision=pending.revision,
            decision=decision,
        ),
    )


def waiting(tmp_path, *, kind="openai_compatible"):
    database, store, agent, conversation, tasks, policies = state(tmp_path, kind=kind)
    policy = rule(policies, agent, "ask")
    item = task(tasks, agent)
    assert asyncio.run(TaskWorker(database).run_once())
    service = ApprovalService(database)
    pending = service.list(agent.id, task_id=item.id)[-1]
    assert pending.status == "pending"
    assert tasks.detail(agent.id, item.id).task.calls_started == 0
    return database, store, agent, tasks, policies, policy, item, service, pending


def test_one_time_survives_restart_dispatches_once_and_does_not_change_rule(tmp_path):
    database, store, agent, tasks, policies, policy, item, service, pending = waiting(tmp_path)
    detail = tasks.detail(agent.id, item.id)
    run_id = detail.runs[-1].id
    before_messages = detail.messages
    approved = approve(service, agent, pending)
    assert approved.status == "approved"
    assert tasks.detail(agent.id, item.id).runs[-1].id == run_id
    effects = []

    def transport(request):
        effects.append(request)
        return answer(request)

    worker = TaskWorker(Database(database.path), transport=httpx.MockTransport(transport))
    assert asyncio.run(worker.run_once())
    assert not asyncio.run(worker.run_once())
    final = tasks.detail(agent.id, item.id)
    assert final.task.status == "completed" and final.task.calls_started == 1
    assert len(final.messages) == len(before_messages) + 1
    assert policies.get(agent.id, policy.id).effect == "ask"
    assert service.view(agent.id, service.get(agent.id, pending.id))["consumed_at"]
    with store.transaction(write=False) as unit:
        calls = unit.model_calls.list(run_id=run_id)
        assert calls[-1].metadata["approval_id"] == str(pending.id)
        assert unit.actions.get(pending.action_id).status == "confirmed"
    another = task(tasks, agent)
    assert asyncio.run(worker.run_once())
    assert tasks.detail(agent.id, another.id).task.status == "waiting_approval"
    assert len(effects) == 1


@pytest.mark.parametrize("during_preflight", [False, True])
def test_revoking_grant_prevents_effect_and_creates_new_question(tmp_path, during_preflight):
    values = waiting(tmp_path, kind="ollama")
    database, _, agent, tasks, _, _, item, service, pending = values
    approved = approve(service, agent, pending)
    paths = []

    def revoke():
        service.revoke(
            agent.id,
            approved.id,
            ApprovalRevokeInput(
                expected_revision=approved.revision,
                status="revoked",
            ),
        )

    if not during_preflight:
        revoke()

    def transport(request):
        paths.append(request.url.path)
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "fixture-text"}]})
        if request.url.path == "/api/show":
            revoke()
            return httpx.Response(200, json={"capabilities": ["completion"]})
        pytest.fail("Consentimento revogado não pode gerar")

    worked = asyncio.run(TaskWorker(database, transport=httpx.MockTransport(transport)).run_once())
    assert worked is during_preflight
    detail = tasks.detail(agent.id, item.id)
    assert detail.task.calls_started == 0
    assert detail.task.status in ("paused", "waiting_approval")
    assert "/api/chat" not in paths


def test_rules_changed_during_preflight_invalidate_one_time_consent(tmp_path):
    values = waiting(tmp_path, kind="ollama")
    database, _, agent, tasks, policies, policy, item, service, pending = values
    approve(service, agent, pending)

    def transport(request):
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "fixture-text"}]})
        if request.url.path == "/api/show":
            policies.update(
                agent.id,
                policy.id,
                PolicyUpdate(
                    expected_revision=policy.revision,
                    reason="Nova revisão humana",
                ),
            )
            return httpx.Response(200, json={"capabilities": ["completion"]})
        pytest.fail("Revisão alterada exige novo consentimento")

    assert asyncio.run(TaskWorker(database, transport=httpx.MockTransport(transport)).run_once())
    assert tasks.detail(agent.id, item.id).task.status == "waiting_approval"
    assert tasks.detail(agent.id, item.id).task.calls_started == 0
    assert not service.view(agent.id, service.get(agent.id, pending.id))["valid"]


@pytest.mark.parametrize("control,status", [("pause", "paused"), ("redirect", "queued")])
def test_control_during_preflight_does_not_consume_approved_action(tmp_path, control, status):
    values = waiting(tmp_path, kind="ollama")
    database, _, agent, tasks, _, _, item, service, pending = values
    approve(service, agent, pending)

    def transport(request):
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "fixture-text"}]})
        if request.url.path == "/api/show":
            current = tasks.detail(agent.id, item.id).task
            tasks.command(
                agent.id,
                item.id,
                TaskCommandInput(
                    client_request_id=uuid4(),
                    expected_revision=current.revision,
                    kind=control,
                    instruction="Novo escopo" if control == "redirect" else None,
                ),
            )
            return httpx.Response(200, json={"capabilities": ["completion"]})
        pytest.fail("Controle alterado impede efeito aprovado anteriormente")

    assert asyncio.run(TaskWorker(database, transport=httpx.MockTransport(transport)).run_once())
    assert tasks.detail(agent.id, item.id).task.status == status
    assert tasks.detail(agent.id, item.id).task.calls_started == 0
    assert service.view(agent.id, service.get(agent.id, pending.id))["consumed_at"] is None


def test_unknown_effect_consumes_grant_and_never_reuses_it(tmp_path):
    database, store, agent, tasks, _, _, item, service, pending = waiting(tmp_path)
    approve(service, agent, pending)
    effects = []

    def lost_response(request):
        effects.append(request)
        raise httpx.ReadError("Resposta perdida depois de aceitar", request=request)

    worker = TaskWorker(database, transport=httpx.MockTransport(lost_response))
    assert asyncio.run(worker.run_once())
    current = tasks.detail(agent.id, item.id).task
    assert current.status == "paused" and current.calls_started == 1
    with store.transaction(write=False) as unit:
        assert unit.actions.get(pending.action_id).status == "outcome_unknown"
    assert not asyncio.run(worker.run_once())
    tasks.command(
        agent.id,
        item.id,
        TaskCommandInput(
            client_request_id=uuid4(),
            expected_revision=current.revision,
            kind="resume",
            acknowledge_unknown=True,
        ),
    )
    assert asyncio.run(worker.run_once())
    assert tasks.detail(agent.id, item.id).task.status == "waiting_approval"
    assert len(effects) == 1


def test_process_death_after_approved_dispatch_recovers_both_journals(tmp_path):
    database, store, agent, tasks, _, _, item, service, pending = waiting(tmp_path)
    approve(service, agent, pending)
    marker = tmp_path / "accepted.txt"
    script = """
import asyncio, os, sys
from pathlib import Path
import httpx
from bees_core.execution import TaskWorker
from bees_core.storage.database import Database
def accepted(request):
    Path(sys.argv[2]).write_text('accepted', encoding='utf-8')
    os._exit(19)
asyncio.run(TaskWorker(Database(sys.argv[1]), lease_seconds=5,
                      transport=httpx.MockTransport(accepted)).run_once())
"""
    crashed = subprocess.run(
        [sys.executable, "-c", script, str(database.path), str(marker)],
        check=False,
    )
    assert crashed.returncode == 19 and marker.exists()
    with store.transaction() as unit:
        assert unit.execution.recover_expired(now=utc_now() + timedelta(seconds=6)) == 1
        assert unit.actions.get(pending.action_id).status == "outcome_unknown"
    assert not asyncio.run(TaskWorker(Database(database.path)).run_once())
    assert tasks.detail(agent.id, item.id).task.calls_started == 1
    assert service.view(agent.id, service.get(agent.id, pending.id))["consumed_at"]


def test_persistent_exact_rule_avoids_next_question_until_revoked(tmp_path):
    database, _, agent, tasks, policies, _, item, service, pending = waiting(tmp_path)
    approved = approve(service, agent, pending, "allow_rule")
    worker = TaskWorker(database, transport=httpx.MockTransport(answer))
    assert asyncio.run(worker.run_once())
    assert tasks.detail(agent.id, item.id).task.status == "completed"
    another = task(tasks, agent)
    assert asyncio.run(worker.run_once())
    assert tasks.detail(agent.id, another.id).task.status == "completed"
    saved = policies.get(agent.id, approved.policy_id)
    assert "request_hash" not in saved.scope["parameters"]
    policies.update(
        agent.id,
        saved.id,
        PolicyUpdate(
            expected_revision=saved.revision,
            status="revoked",
        ),
    )
    next_item = task(tasks, agent)
    assert asyncio.run(worker.run_once())
    assert tasks.detail(agent.id, next_item.id).task.status == "waiting_approval"


@pytest.mark.parametrize("decision", ["ask", "deny"])
def test_restrictive_decision_does_not_dispatch(tmp_path, decision):
    database, _, agent, tasks, policies, _, item, service, pending = waiting(tmp_path)
    changed = approve(service, agent, pending, decision)
    assert changed.decision == decision
    assert not asyncio.run(TaskWorker(database).run_once())
    assert tasks.detail(agent.id, item.id).task.calls_started == 0
    assert any(p.effect == decision for p in policies.list(agent.id))
