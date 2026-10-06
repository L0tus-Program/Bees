"""Consentimento não é transferível: escopo, revisão, validade e consumo."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from uuid import uuid4

import pytest

from bees_core.approvals import (
    ApprovalDecisionInput,
    ApprovalError,
    ApprovalRevokeInput,
    ApprovalService,
)
from bees_core.models import Agent, ModelCall, utc_now
from bees_core.policies import PolicyInput, PolicyService, PolicyUpdate
from bees_core.providers.contracts import ProviderConfig
from bees_core.providers.service import ProviderService
from bees_core.storage.database import Database
from bees_core.storage.store import NotFoundError, RevisionConflict, StateStore
from bees_core.tasks import TaskCommandInput, TaskInput, TaskService


@pytest.fixture
def pending(tmp_path):
    db = Database(tmp_path / "approvals.sqlite3")
    db.initialize()
    store = StateStore(db)
    config = ProviderConfig(
        kind="openai_compatible",
        endpoint="https://fixture.test/v1",
        model="fixture-text",
        capabilities={"text": True, "tool_calls": False},
    )
    agent = Agent(name="Aprovações", provider_config=config.model_dump(mode="json"))
    with store.transaction() as unit:
        unit.agents.create(agent)
    tasks = TaskService(db)
    task = tasks.create(
        agent.id,
        TaskInput(
            client_request_id=uuid4(),
            title="Teste",
            objective="Analise somente os dados fornecidos.",
        ),
    )
    policies = PolicyService(db)
    ask = policies.create(agent.id, PolicyInput(name="Perguntar sempre", effect="ask"))
    now = [utc_now()]
    approvals = ApprovalService(db, clock=lambda: now[0], ttl_seconds=60)
    providers = ProviderService(db)
    with store.transaction() as unit:
        run = unit.runs.list(task_id=task.id)[-1]
        prepared = providers.prepare_chat(
            unit, agent.id, task.conversation_id, "Execute a tarefa.", task_id=task.id
        )
        decision = providers.model_policy(unit, prepared)
        approval = approvals.request(unit, task, run, prepared, decision)
        unit.tasks.update(
            task.model_copy(update={"status": "waiting_approval", "desired_state": "paused"}),
            task.revision,
        )
        run = unit.runs.get(run.id)
        unit.runs.update(run.model_copy(update={"status": "waiting_approval"}), run.revision)
    return db, store, agent, task, approvals, approval, prepared, policies, ask, now


def decide(state, decision="allow_once", **extra):
    _, _, agent, _, service, approval, _, _, _, _ = state
    return service.decide(
        agent.id,
        approval.id,
        ApprovalDecisionInput(
            client_request_id=uuid4(),
            expected_revision=approval.revision,
            decision=decision,
            **extra,
        ),
    )


def check(state):
    db, store, _, task, service, _, prepared, _, _, _ = state
    with store.transaction(write=False) as unit:
        current = unit.tasks.get(task.id)
        run = unit.runs.list(task_id=task.id)[-1]
        policy = ProviderService(db).model_policy(unit, prepared)
        return service.check(unit, current, run, prepared, policy), policy


def call_in(unit, task, run, prepared):
    config = prepared.config.model_dump(mode="json")
    request = prepared.request.model_dump(mode="json")
    snapshot = {"prepared": prepared.model_dump(mode="json"), "directive_revision": 0}
    return unit.model_calls.create(
        ModelCall(
            run_id=run.id,
            ordinal=1,
            task_control_revision=task.control_revision,
            agent_revision=prepared.agent_revision,
            lease_generation=1,
            provider_config=config,
            request=request,
            snapshot=snapshot,
            request_hash=unit.model_calls.request_digest(config, request, snapshot),
        )
    )


def test_pending_is_durable_and_public_view_excludes_private_snapshot(pending):
    db, store, agent, task, service, approval, prepared, _, _, _ = pending
    restarted = ApprovalService(db)
    assert restarted.get(agent.id, approval.id) == approval
    assert restarted.list(agent.id, task_id=task.id) == [approval]
    view = restarted.view(agent.id, approval)
    assert view["valid"] and view["parameters"] == {"model": "fixture-text"}
    assert (
        "prepared" not in view
        and "metadata" not in view
        and "request_hash" not in view["parameters"]
    )
    with store.transaction(write=False) as unit:
        run = unit.runs.list(task_id=task.id)[-1]
        assert restarted.prepared_for_run(unit, unit.tasks.get(task.id), run) == prepared
    assert check(pending)[0] is None


def test_same_question_reuses_pending_record(pending):
    db, store, _, task, service, approval, prepared, _, _, _ = pending
    with store.transaction() as unit:
        run = unit.runs.list(task_id=task.id)[-1]
        same = service.request(
            unit,
            unit.tasks.get(task.id),
            run,
            prepared,
            ProviderService(db).model_policy(unit, prepared),
        )
    assert same.id == approval.id


def test_once_requeues_same_run_without_granting_rule(pending):
    _, store, agent, task, service, approval, prepared, policies, _, _ = pending
    before = TaskService(store.database).detail(agent.id, task.id)
    approved = decide(pending)
    after = TaskService(store.database).detail(agent.id, task.id)
    assert after.task.status == "queued" and after.task.desired_state == "running"
    assert after.task.control_revision == before.task.control_revision
    assert after.runs[-1].id == before.runs[-1].id
    assert len(policies.list(agent.id)) == 1
    assert check(pending)[0].id == approved.id
    with store.transaction(write=False) as unit:
        assert service.prepared_for_run(unit, after.task, after.runs[-1]) == prepared


def test_consent_consumed_only_with_atomic_dispatch_and_never_twice(pending):
    db, store, agent, task, service, approval, prepared, _, _, _ = pending
    decide(pending)
    with pytest.raises(RuntimeError):
        with store.transaction() as unit:
            task = unit.tasks.get(task.id)
            run = unit.runs.list(task_id=task.id)[-1]
            call = call_in(unit, task, run, prepared)
            decision = ProviderService(db).model_policy(unit, prepared)
            service.consume(unit, task, run, prepared, decision, call_id=call.id)
            raise RuntimeError("Falha antes de begin_call: rollback")
    assert check(pending)[0] is not None
    with store.transaction() as unit:
        task = unit.tasks.get(task.id)
        run = unit.runs.list(task_id=task.id)[-1]
        call = call_in(unit, task, run, prepared)
        decision = ProviderService(db).model_policy(unit, prepared)
        consumed = service.consume(unit, task, run, prepared, decision, call_id=call.id)
        assert consumed.metadata["model_call_id"] == str(call.id)
        assert service.check(unit, task, run, prepared, decision) is None
    assert (
        service.view(agent.id, service.get(agent.id, approval.id))["stale_reason"]
        == "approval_consumed"
    )


def test_approval_does_not_accept_another_prepared_request(pending):
    db, store, _, task, service, _, prepared, _, _, _ = pending
    decide(pending)
    changed = prepared.model_copy(update={"context": prepared.context | {"unexpected": True}})
    with store.transaction(write=False) as unit:
        run = unit.runs.list(task_id=task.id)[-1]
        assert (
            service.check(
                unit,
                unit.tasks.get(task.id),
                run,
                changed,
                ProviderService(db).model_policy(unit, prepared),
            )
            is None
        )


def test_expiry_prevents_consent_and_refreshes_same_prepared(pending):
    db, store, _, task, service, approval, prepared, _, _, now = pending
    decide(pending)
    now[0] += timedelta(seconds=60)
    assert check(pending)[0] is None
    with store.transaction() as unit:
        task = unit.tasks.get(task.id)
        run = unit.runs.list(task_id=task.id)[-1]
        assert service.prepared_for_run(unit, task, run) == prepared
        fresh = service.request(
            unit, task, run, prepared, ProviderService(db).model_policy(unit, prepared)
        )
    assert fresh.id != approval.id
    assert service.get(task.agent_id, approval.id).status == "revoked"


@pytest.mark.parametrize("decision", ["allow_once", "allow_rule", "ask", "deny"])
def test_expired_decision_is_rejected(pending, decision):
    pending[-1][0] += timedelta(seconds=60)
    with pytest.raises(ApprovalError, match="approval_expired"):
        decide(pending, decision)


def test_decision_replay_is_exact_even_after_revision_changes(pending):
    _, _, agent, _, service, approval, _, _, _, _ = pending
    value = ApprovalDecisionInput(
        client_request_id=uuid4(), expected_revision=1, decision="allow_once"
    )
    accepted = service.decide(agent.id, approval.id, value)
    assert service.decide(agent.id, approval.id, value) == accepted
    with pytest.raises(ApprovalError, match="idempotency_conflict"):
        service.decide(agent.id, approval.id, value.model_copy(update={"decision": "deny"}))
    with pytest.raises(RevisionConflict):
        service.decide(
            agent.id, approval.id, value.model_copy(update={"client_request_id": uuid4()})
        )


def test_concurrent_human_decisions_have_one_winner(pending):
    _, _, agent, _, service, approval, _, _, _, _ = pending

    def choose(decision):
        try:
            return service.decide(
                agent.id,
                approval.id,
                ApprovalDecisionInput(
                    client_request_id=uuid4(), expected_revision=1, decision=decision
                ),
            )
        except RevisionConflict:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(choose, ["allow_once", "deny"]))
    assert sum(result is not None for result in outcomes) == 1


@pytest.mark.parametrize("change", ["control", "directive", "profile", "memory", "policy"])
def test_revision_and_context_changes_invalidate_human_consent(pending, change):
    _, store, agent, task, service, approval, prepared, policies, ask, _ = pending
    decide(pending)
    if change == "policy":
        policies.update(
            agent.id, ask.id, PolicyUpdate(expected_revision=ask.revision, reason="Outra regra")
        )
    else:
        with store.transaction() as unit:
            if change == "control":
                item = unit.tasks.get(task.id)
                unit.tasks.update(item.model_copy(update={"control_revision": 1}), item.revision)
            elif change == "directive":
                run = unit.runs.list(task_id=task.id)[-1]
                unit.runs.update(
                    run.model_copy(
                        update={"checkpoint": run.checkpoint | {"directive_revision": 1}}
                    ),
                    run.revision,
                )
            elif change == "profile":
                unit.agents.update(
                    agent.model_copy(update={"name": "Perfil alterado"}), agent.revision
                )
            else:
                conversation = unit.conversations.get(prepared.conversation_id)
                unit.conversations.update(
                    conversation.model_copy(update={"title": "Contexto novo"}),
                    conversation.revision,
                )
    assert (
        service.view(agent.id, service.get(agent.id, approval.id))["stale_reason"]
        == "approval_stale"
    )


def test_new_deny_prevents_once_and_permanent_consent(pending):
    _, _, agent, _, _, _, _, policies, _, _ = pending
    decide(pending)
    policies.create(agent.id, PolicyInput(name="Bloqueio posterior", effect="deny"))
    assert check(pending)[0] is None and check(pending)[1].effect == "deny"


def test_allow_rule_is_exact_exception_not_global_override(pending):
    db, store, agent, _, service, _, prepared, policies, ask, _ = pending
    approval = decide(pending, "allow_rule")
    granted = policies.get(agent.id, approval.policy_id)
    assert granted.scope["parameters"] == {"model": "fixture-text"}
    assert granted.scope["resource"] == prepared.config.endpoint
    assert check(pending)[1].allowed
    with store.transaction(write=False) as unit:
        other = ProviderService.model_intent(
            agent.id, prepared.config.model_copy(update={"model": "other"}), prepared.request
        )
        assert policies.evaluate(unit, other).effect == "ask"
    policies.update(
        agent.id, ask.id, PolicyUpdate(expected_revision=ask.revision, reason="Revisão nova")
    )
    assert check(pending)[0] is None and check(pending)[1].effect == "ask"


def test_allow_rule_does_not_suppress_new_ask_or_new_deny(pending):
    _, _, agent, _, _, _, _, policies, _, _ = pending
    decide(pending, "allow_rule")
    policies.create(agent.id, PolicyInput(name="Nova pergunta", effect="ask"))
    assert check(pending)[1].effect == "ask"
    policies.create(agent.id, PolicyInput(name="Nunca", effect="deny"))
    assert check(pending)[0] is None and check(pending)[1].effect == "deny"


def test_renaming_granted_rule_disables_exception(pending):
    _, _, agent, _, _, _, _, policies, _, _ = pending
    approval = decide(pending, "allow_rule")
    granted = policies.get(agent.id, approval.policy_id)
    policies.update(
        agent.id, granted.id, PolicyUpdate(expected_revision=granted.revision, name="Nome alterado")
    )
    assert check(pending)[1].effect == "ask"


@pytest.mark.parametrize(
    "decision,effect,status", [("ask", "ask", "waiting_approval"), ("deny", "deny", "paused")]
)
def test_ask_and_deny_save_destination_scoped_rule(pending, decision, effect, status):
    _, store, agent, task, _, _, prepared, policies, _, _ = pending
    approval = decide(pending, decision)
    saved = policies.get(agent.id, approval.metadata["decision_policy_id"])
    assert saved.effect == effect and saved.scope["resource"] == prepared.config.endpoint
    assert saved.scope["parameters"] == {"model": "fixture-text"}
    assert TaskService(store.database).detail(agent.id, task.id).task.status == status


def test_revoke_cancels_pending_dispatch_and_reusable_rule(pending):
    _, store, agent, task, service, _, _, policies, _, _ = pending
    approved = decide(pending, "allow_rule")
    request = ApprovalRevokeInput(client_request_id=uuid4(), expected_revision=approved.revision)
    revoked = service.revoke(agent.id, approved.id, request)
    assert service.revoke(agent.id, approved.id, request) == revoked
    assert policies.get(agent.id, approved.policy_id).status == "revoked"
    assert TaskService(store.database).detail(agent.id, task.id).task.status == "paused"
    assert check(pending)[0] is None


def test_old_approval_revocation_does_not_pause_new_run(pending):
    _, store, agent, task, service, approval, _, _, _, _ = pending
    tasks = TaskService(store.database)
    item = tasks.detail(agent.id, task.id).task
    tasks.command(
        agent.id,
        task.id,
        TaskCommandInput(client_request_id=uuid4(), expected_revision=item.revision, kind="resume"),
    )
    current = tasks.detail(agent.id, task.id).task
    assert current.status == "queued"
    service.revoke(agent.id, approval.id, ApprovalRevokeInput(expected_revision=approval.revision))
    assert tasks.detail(agent.id, task.id).task.status == "queued"


def test_wrong_agent_and_task_cannot_read_or_decide(pending):
    _, _, _, _, service, approval, _, _, _, _ = pending
    with pytest.raises(NotFoundError):
        service.get(uuid4(), approval.id)
    with pytest.raises(NotFoundError):
        service.list(pending[2].id, task_id=uuid4())
    with pytest.raises(NotFoundError):
        service.decide(
            uuid4(),
            approval.id,
            ApprovalDecisionInput(
                client_request_id=uuid4(), expected_revision=1, decision="allow_once"
            ),
        )


def test_dispatch_outcome_unknown_never_becomes_reusable_once(pending):
    db, store, agent, task, service, approval, prepared, _, _, _ = pending
    decide(pending)
    with store.transaction() as unit:
        task = unit.tasks.get(task.id)
        run = unit.runs.list(task_id=task.id)[-1]
        call = call_in(unit, task, run, prepared)
        service.consume(
            unit,
            task,
            run,
            prepared,
            ProviderService(db).model_policy(unit, prepared),
            call_id=call.id,
        )
        service.finish(unit, approval.id, "outcome_unknown")
    with store.transaction(write=False) as unit:
        assert unit.actions.get(approval.action_id).status == "outcome_unknown"
        assert service.prepared_for_run(unit, task, run) is None
    assert (
        service.view(agent.id, service.get(agent.id, approval.id))["stale_reason"]
        == "approval_consumed"
    )


def test_ask_then_once_keeps_human_audit_and_exact_replay(pending):
    _, _, agent, _, service, original, _, _, _, _ = pending
    asked = decide(pending, "ask", reason="Quero confirmar a cada tarefa.")
    once = service.decide(
        agent.id,
        original.id,
        ApprovalDecisionInput(
            client_request_id=uuid4(),
            expected_revision=asked.revision,
            decision="allow_once",
            reason="Pode enviar esta solicitação.",
        ),
    )
    history = once.metadata["human_decisions"]
    assert [entry["decision"] for entry in history] == ["ask", "allow_once"]
    assert history[0]["reason"] == "Quero confirmar a cada tarefa."
    assert history[0]["policy_id"] is not None and history[1]["policy_id"] is None
    assert all(entry["actor"] == "user" for entry in history)


def test_corrupt_or_foreign_checkpoint_cannot_transfer_permission(pending):
    db, store, agent, task, service, approval, prepared, _, _, _ = pending
    decide(pending)
    with store.transaction() as unit:
        run = unit.runs.list(task_id=task.id)[-1]
        run = unit.runs.update(
            run.model_copy(
                update={
                    "checkpoint": run.checkpoint
                    | {
                        "approval_id": "invalid-pointer",
                    }
                }
            ),
            run.revision,
        )
        assert service.prepared_for_run(unit, unit.tasks.get(task.id), run) is None
        assert (
            service.check(
                unit,
                unit.tasks.get(task.id),
                run,
                prepared,
                ProviderService(db).model_policy(unit, prepared),
            )
            is None
        )
    revoked = service.revoke(
        agent.id,
        approval.id,
        ApprovalRevokeInput(expected_revision=service.get(agent.id, approval.id).revision),
    )
    assert revoked.status == "revoked"


def test_human_decision_rechecks_policy_even_if_uuid_and_revision_match(pending):
    _, _, agent, _, _, _, _, policies, ask, _ = pending
    policies.update(
        agent.id, ask.id, PolicyUpdate(expected_revision=ask.revision, reason="Reavalie a regra.")
    )
    with pytest.raises(ApprovalError, match="approval_stale"):
        decide(pending)


def test_expired_once_cannot_be_decided_after_restart(pending):
    db, _, agent, _, _, approval, _, _, _, now = pending
    now[0] += timedelta(seconds=60)
    restarted = ApprovalService(db, clock=lambda: now[0])
    with pytest.raises(ApprovalError, match="approval_expired"):
        restarted.decide(
            agent.id,
            approval.id,
            ApprovalDecisionInput(
                client_request_id=uuid4(),
                expected_revision=approval.revision,
                decision="allow_once",
            ),
        )


def test_consumption_rejects_call_with_other_payload(pending):
    db, store, _, task, service, _, prepared, _, _, _ = pending
    decide(pending)
    with store.transaction() as unit:
        task = unit.tasks.get(task.id)
        run = unit.runs.list(task_id=task.id)[-1]
        other = prepared.model_copy(update={"context": prepared.context | {"other": True}})
        call = call_in(unit, task, run, other)
        with pytest.raises(ApprovalError, match="approval_stale"):
            service.consume(
                unit,
                task,
                run,
                prepared,
                ProviderService(db).model_policy(unit, prepared),
                call_id=call.id,
            )
        assert (
            service.check(
                unit, task, run, prepared, ProviderService(db).model_policy(unit, prepared)
            )
            is not None
        )


def test_allow_rule_exception_cannot_be_forged_by_mutable_policy_metadata(pending):
    db, store, agent, _, _, _, prepared, policies, ask, _ = pending
    approved = decide(pending, "allow_rule")
    second = policies.create(agent.id, PolicyInput(name="Outra pergunta", effect="ask"))
    with store.transaction() as unit:
        granted = unit.policies.get(approved.policy_id)
        grant = granted.metadata["approval_grant"] | {
            "ask_rules": [
                {"id": str(ask.id), "revision": ask.revision},
                {"id": str(second.id), "revision": second.revision},
            ],
            "policy_revision": granted.revision + 1,
        }
        unit.policies.update(
            granted.model_copy(update={"metadata": {"approval_grant": grant}}), granted.revision
        )
        decision = ProviderService(db).model_policy(unit, prepared)
        assert decision.effect == "ask"
