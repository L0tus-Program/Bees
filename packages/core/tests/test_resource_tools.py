"""Aprovação distinta, espera sem lease e despacho real sem repetir o efeito."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import timedelta
from uuid import uuid4

import apsw
import pytest
from pydantic import ValidationError
from test_executor_transport import builtin_setup as _builtin_setup
from test_executor_transport import system as _transport_system
from test_tools import invocation

from bees_core.approvals import ApprovalError, ApprovalService
from bees_core.executor_transport.contracts import TransportError
from bees_core.executor_transport.journal import Journal
from bees_core.executor_transport.resource_authority import ResourceToolAuthority
from bees_core.models import Action, Approval, Run, Task, utc_now
from bees_core.policies import PolicyService
from bees_core.resource_tools import TASK_KIND, ResourceToolService
from bees_core.storage.database import Database
from bees_core.storage.store import IntegrityError, NotFoundError, RevisionConflict, StateStore
from bees_core.tools import ToolError

builtin_setup = _builtin_setup
transport_system = _transport_system


@dataclass
class ResourceCase:
    transport: object
    resources: ResourceToolService
    value: dict

    @property
    def agent(self):
        return self.transport.builtin[3]

    @property
    def store(self):
        return StateStore(self.transport.builtin[0])

    def prepare(self, claim=None):
        return self.resources.prepare(
            self.agent.id,
            self.transport.builtin[6].id,
            self.value,
            claim=claim or self.transport.builtin[2].test_claim,
        )

    def decide(self, approval, decision="allow_once", **changes):
        return self.resources.decide(
            self.agent.id,
            approval.id,
            {
                "client_request_id": uuid4(),
                "expected_revision": approval.revision,
                "decision": decision,
                **changes,
            },
        )

    def claim(self):
        with self.store.transaction() as unit:
            return unit.execution.claim_next(uuid4(), now=utc_now(), ttl_seconds=300)

    def dispatch(self, preparation, claim=None):
        claim = claim or self.claim()
        effect = self.resources.prepare_dispatch(self.agent.id, preparation.intent.id, claim=claim)
        authority = ResourceToolAuthority(
            self.resources, self.agent.id, effect.id, expected_revision=effect.revision, claim=claim
        )
        return self.transport.transport.execute(authority)


@pytest.fixture
def state(transport_system):
    db, store, _, _, _, task, *_ = transport_system.builtin
    with store.transaction() as unit:
        current = unit.tasks.get(task.id)
        unit.tasks.update(
            current.model_copy(update={"metadata": {"execution_kind": TASK_KIND}}), current.revision
        )
    resources = ResourceToolService(db, transport_system.resource.identity)
    return ResourceCase(transport_system, resources, invocation(transport_system.builtin))


def test_human_wait_releases_leases_then_real_process_consumes_once(state):
    prepared = state.prepare()
    assert prepared.approval.status == "pending" and prepared.effect is None
    with state.store.transaction(write=False) as unit:
        task = unit.tasks.get(state.transport.builtin[5].id)
        assert task.status == "waiting_approval"
        assert (
            unit.agents._context.connection.execute(
                "SELECT count(*) FROM execution_leases WHERE owner_id IS NOT NULL"
            ).get
            == 0
        )
    approved = state.decide(prepared.approval)
    assert approved.status == "approved"
    claim = state.claim()
    assert claim.owner_id != state.transport.builtin[2].test_claim.owner_id
    result = state.dispatch(prepared, claim)
    assert result.status == "confirmed" and result.result == {"text": "Olá Bees"}
    repeated = state.prepare()
    assert repeated.effect == result and repeated.intent.status == "confirmed"
    assert repeated.approval.metadata["effect_action_id"] == str(result.id)
    assert repeated.approval.metadata["consumed_at"]
    with state.store.transaction(write=False) as unit:
        task = unit.tasks.get(claim.task.id)
        assert task.metadata["resource_tool_actions_started"] == 1
        assert 0 < task.metadata["resource_tool_reserved_milliseconds"] <= 5000
        assert unit.model_calls.list(run_id=claim.run.id) == []


def test_model_approval_channel_cannot_decide_or_return_tool_approval(state):
    prepared = state.prepare()
    old = ApprovalService(state.transport.builtin[0])
    with pytest.raises(NotFoundError):
        old.get(state.agent.id, prepared.approval.id)
    assert old.list(state.agent.id) == []
    with pytest.raises(ValidationError):
        state.resources.decide(
            state.agent.id,
            prepared.approval.id,
            {
                "client_request_id": uuid4(),
                "expected_revision": 1,
                "decision": "allow_once",
                "authority": "model.generate",
            },
        )


def authority(state, prepared):
    claim = state.claim()
    effect = state.resources.prepare_dispatch(state.agent.id, prepared.intent.id, claim=claim)
    return ResourceToolAuthority(
        state.resources, state.agent.id, effect.id, expected_revision=effect.revision, claim=claim
    )


def effect_events(state):
    journal = Journal(state.transport.resource.directory, state.transport.resource.identity)
    try:
        return [
            row[0]
            for row in journal.connection.execute("SELECT status FROM events ORDER BY sequence")
        ]
    finally:
        journal.close()


def test_saved_rule_is_exact_and_new_input_still_requires_own_approval(state):
    prepared = state.prepare()
    approved = state.decide(prepared.approval, "allow_rule")
    first = authority(state, prepared)
    assert state.transport.transport.execute(first).status == "confirmed"
    rule = None
    with state.store.transaction(write=False) as unit:
        rule = unit.policies.get(approved.policy_id)
    assert rule.scope["resource"] == f"resource:{state.transport.resource.identity.resource_id}"
    assert rule.scope["parameters"]["grant_revision"] == 1
    assert "text" not in rule.scope["parameters"]
    state.value = state.value | {"client_request_id": uuid4()}
    same = state.prepare(first.claim)
    assert same.approval is None and same.effect.status == "ready"
    follow = ResourceToolAuthority(
        state.resources,
        state.agent.id,
        same.effect.id,
        expected_revision=same.effect.revision,
        claim=first.claim,
    )
    assert state.transport.transport.execute(follow).status == "confirmed"
    state.value = state.value | {
        "client_request_id": uuid4(),
        "arguments": {"text": "Outra entrada", "operation": "trim"},
    }
    changed = state.prepare(first.claim)
    assert changed.approval is not None and changed.approval.status == "pending"
    assert effect_events(state).count("started") == 2


@pytest.mark.parametrize("change", ["grant", "plugin", "agent", "control", "policy"])
def test_changed_authority_before_effect_invalidates_decision_without_consumption(state, change):
    prepared = state.prepare()
    state.decide(prepared.approval)
    db, _, tools, agent, _, task, _, plugin, grant = state.transport.builtin
    if change == "grant":
        tools.set_grant(
            agent.id,
            plugin.id,
            "text.normalize",
            {"enabled": False, "expected_revision": grant.revision},
        )
    elif change == "plugin":
        tools.update_plugin(plugin.id, {"enabled": False, "expected_revision": plugin.revision})
    elif change == "agent":
        with state.store.transaction() as unit:
            current = unit.agents.get(agent.id)
            unit.agents.update(
                current.model_copy(update={"instructions": "Novo perfil"}), current.revision
            )
    elif change == "control":
        with state.store.transaction() as unit:
            current = unit.tasks.get(task.id)
            unit.tasks.update(
                current.model_copy(update={"control_revision": current.control_revision + 1}),
                current.revision,
            )
    else:
        PolicyService(db).create(
            agent.id,
            {
                "name": "Nova pergunta",
                "effect": "ask",
                "scope": {"tool_name": "text.normalize", "action": "transform_resource"},
            },
        )
    with pytest.raises((ApprovalError, ToolError, RevisionConflict)):
        authority(state, prepared)
    with state.store.transaction(write=False) as unit:
        approval = unit.approvals.get(prepared.approval.id)
        assert not approval.metadata.get("consumed_at")
    assert effect_events(state) == []


def test_expiry_denial_and_revocation_never_dispatch(state):
    prepared = state.prepare()
    future = ResourceToolService(
        state.transport.builtin[0], clock=lambda: utc_now() + timedelta(hours=1)
    )
    assert future.view(state.agent.id, prepared.approval.id)["stale_reason"] == "approval_expired"
    with pytest.raises(ApprovalError, match="approval_expired"):
        future.decide(
            state.agent.id,
            prepared.approval.id,
            {"client_request_id": uuid4(), "expected_revision": 1, "decision": "allow_once"},
        )
    denied = state.decide(prepared.approval, "deny")
    assert denied.status == "denied"
    assert state.resources.view(state.agent.id, denied.id)["stale_reason"] == "approval_denied"
    with pytest.raises(ApprovalError):
        state.resources.prepare_dispatch(
            state.agent.id, prepared.intent.id, claim=state.transport.builtin[2].test_claim
        )
    assert effect_events(state) == []


def test_revoke_after_preparing_effect_blocks_go_and_preserves_unconsumed_decision(state):
    prepared = state.prepare()
    approved = state.decide(prepared.approval)
    executor = authority(state, prepared)
    state.resources.revoke(
        state.agent.id,
        approved.id,
        {"client_request_id": uuid4(), "expected_revision": approved.revision, "status": "revoked"},
    )
    with pytest.raises((ApprovalError, ToolError)):
        state.transport.transport.execute(executor)
    assert effect_events(state) == []
    assert state.resources.get(state.agent.id, approved.id).metadata.get("consumed_at") is None


def test_concurrent_cas_and_exact_replay_record_one_human_decision(state):
    prepared = state.prepare()
    request = {"client_request_id": uuid4(), "expected_revision": 1, "decision": "allow_once"}

    def decide(_):
        return ResourceToolService(Database(state.transport.builtin[0].path)).decide(
            state.agent.id, prepared.approval.id, request
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        left, right = pool.map(decide, range(2))
    assert left == right and left.revision == 2
    with pytest.raises(RevisionConflict):
        state.decide(prepared.approval)
    with pytest.raises(ApprovalError, match="idempotency_conflict"):
        state.resources.decide(state.agent.id, prepared.approval.id, request | {"decision": "deny"})
    assert effect_events(state) == []


def test_human_uuid_cannot_approve_another_intent(state):
    first = state.prepare()
    with state.store.transaction() as unit:
        task = unit.tasks.create(
            Task(
                agent_id=state.agent.id,
                title="Segunda",
                objective="Texto",
                metadata={"execution_kind": TASK_KIND},
            )
        )
        run = unit.runs.create(Run(task_id=task.id))
        claim = unit.execution.claim_next(uuid4(), now=utc_now(), ttl_seconds=300)
    second = state.resources.prepare(
        state.agent.id, run.id, state.value | {"client_request_id": uuid4()}, claim=claim
    )
    request_id = uuid4()
    state.decide(first.approval, client_request_id=request_id)
    with pytest.raises(ApprovalError, match="idempotency_conflict"):
        state.decide(second.approval, client_request_id=request_id)
    assert state.resources.get(state.agent.id, second.approval.id).status == "pending"


def test_begin_rollback_restores_approval_counter_and_authorization_intent(state):
    prepared = state.prepare()
    approved = state.decide(prepared.approval)
    executor = authority(state, prepared)
    db = state.transport.builtin[0]
    with db.transaction() as connection:
        connection.execute(
            "CREATE TRIGGER test_fail_effect_begin BEFORE UPDATE ON actions "
            "WHEN NEW.tool_name='text.normalize' AND NEW.status='dispatch_started' "
            "BEGIN SELECT RAISE(ABORT,'controlled begin failure'); END;"
        )
    with pytest.raises(IntegrityError) as failure:
        state.transport.transport.execute(executor)
    assert isinstance(failure.value.__cause__, apsw.ConstraintError)
    current = state.resources.get(state.agent.id, approved.id)
    assert current.status == "approved" and not current.metadata.get("consumed_at")
    with state.store.transaction(write=False) as unit:
        assert unit.actions.get(prepared.intent.id).status == "ready"
        assert (
            unit.tasks.get(executor.claim.task.id).metadata.get("resource_tool_actions_started", 0)
            == 0
        )
    assert "started" not in effect_events(state)


def test_lost_response_keeps_consumed_decision_and_unknown_effect_without_retry(state, monkeypatch):
    prepared = state.prepare()
    state.decide(prepared.approval)
    executor = authority(state, prepared)

    def lost(*_):
        raise TransportError("executor_unavailable")

    monkeypatch.setattr(executor, "complete", lost)
    result = state.transport.transport.execute(executor)
    assert result.status == "outcome_unknown"
    assert state.resources.get(state.agent.id, prepared.approval.id).metadata["consumed_at"]
    assert state.transport.transport.execute(executor) == result
    assert (
        state.transport.transport.inspect(result.metadata["executor_transport"])["status"]
        == "receipt"
    )
    assert effect_events(state).count("started") == 1
    with state.store.transaction() as unit:
        assert unit.execution.claim_next(uuid4(), now=utc_now(), ttl_seconds=300) is None


def test_text_model_approval_and_builtin_dispatch_cannot_authorize_resource_effect(state):
    db, _, tools, agent, _, _, run, *_ = state.transport.builtin
    with state.store.transaction() as unit:
        text = unit.actions.create(
            Action(run_id=run.id, tool_name="model", metadata={"approval_kind": "model_generate"})
        )
        approval = unit.approvals.create(
            Approval(action_id=text.id, status="approved", decision="allow_once")
        )
    with pytest.raises(NotFoundError):
        state.resources.get(agent.id, approval.id)
    prepared = state.prepare()
    state.decide(prepared.approval)
    executor = authority(state, prepared)
    with pytest.raises(ToolError):
        tools.execute(
            agent.id,
            executor.action_id,
            executor.arguments,
            expected_revision=executor.expected_revision,
            claim=executor.claim,
        )
    assert effect_events(state) == []


def test_input_only_appears_in_explicit_human_detail_not_lists_or_scope(state):
    marker = "PRIVATE-INPUT-FOR-HUMAN-REVIEW"
    state.value["arguments"]["text"] = marker
    prepared = state.prepare()
    compact = state.resources.view(state.agent.id, prepared.approval.id)
    assert marker not in str(compact) and marker not in str(compact["scope"])
    assert (
        state.resources.view(state.agent.id, prepared.approval.id, detail=True)["input_preview"]
        == marker
    )
    assert effect_events(state) == []


@pytest.mark.parametrize("change", ["count", "time", "invalid_counter"])
def test_durable_task_budget_blocks_go_without_consuming_approval(state, change):
    prepared = state.prepare()
    state.decide(prepared.approval)
    executor = authority(state, prepared)
    with state.store.transaction() as unit:
        task = unit.tasks.get(executor.claim.task.id)
        metadata = task.metadata | {
            "resource_tool_actions_started": -1 if change == "invalid_counter" else 16
        }
        if change == "time":
            metadata = task.metadata | {
                "resource_tool_reserved_milliseconds": task.max_active_seconds * 1000
            }
        unit.tasks.update(task.model_copy(update={"metadata": metadata}), task.revision)
    with pytest.raises(ToolError, match="Limite|Orçamento"):
        state.transport.transport.execute(executor)
    assert effect_events(state) == []
    assert not state.resources.get(state.agent.id, prepared.approval.id).metadata.get("consumed_at")


@pytest.mark.parametrize("change", ["snapshot", "safety_generation", "target"])
def test_changed_snapshot_generation_or_trusted_target_never_dispatches(state, change):
    from bees_core.executor_transport.contracts import ResourceIdentity
    from bees_core.safety import SafetyCommand, SafetyService

    prepared = state.prepare()
    approved = state.decide(prepared.approval)
    if change == "snapshot":
        with state.store.transaction() as unit:
            root = unit.actions.get(prepared.intent.id)
            snapshot = root.metadata["snapshot"] | {
                "arguments": {"text": "Different", "operation": "upper"}
            }
            unit.actions.update(
                root.model_copy(update={"metadata": root.metadata | {"snapshot": snapshot}}),
                root.revision,
            )
    elif change == "safety_generation":
        safety = SafetyService(state.transport.builtin[0])
        safety.command(SafetyCommand(client_request_id=uuid4(), kind="stop", expected_revision=1))
        safety.command(SafetyCommand(client_request_id=uuid4(), kind="resume", expected_revision=2))
    else:
        identity = state.resources.target
        state.resources.target = ResourceIdentity(
            identity.installation_id, str(uuid4()), identity.agent_id
        )
    with pytest.raises((ApprovalError, ToolError, RevisionConflict)):
        authority(state, prepared)
    assert effect_events(state) == []
    assert not state.resources.get(state.agent.id, approved.id).metadata.get("consumed_at")


def test_reopened_service_keeps_used_authorization_and_revoke_does_not_undo_effect(state):
    prepared = state.prepare()
    state.decide(prepared.approval, "allow_rule")
    result = state.dispatch(prepared)
    reopened = ResourceToolService(Database(state.transport.builtin[0].path))
    current = reopened.get(state.agent.id, prepared.approval.id)
    assert reopened.view(state.agent.id, current.id)["effect_status"] == "confirmed"
    assert reopened.view(state.agent.id, current.id)["stale_reason"] == "approval_consumed"
    revoked = reopened.revoke(
        state.agent.id,
        current.id,
        {"client_request_id": uuid4(), "expected_revision": current.revision, "status": "revoked"},
    )
    assert revoked.metadata["consumed_at"] == current.metadata["consumed_at"]
    with reopened.store.transaction(write=False) as unit:
        assert unit.actions.get(result.id) == result
        assert unit.policies.get(revoked.policy_id).status == "revoked"
    assert effect_events(state).count("started") == 1


def test_revoked_grant_after_effect_keeps_unknown_and_blocks_next_action(state, monkeypatch):
    prepared = state.prepare()
    state.decide(prepared.approval)
    executor = authority(state, prepared)
    original = executor.complete

    def revoke_before_publication(request, output):
        _, _, tools, agent, _, _, _, plugin, grant = state.transport.builtin
        tools.set_grant(
            agent.id,
            plugin.id,
            "text.normalize",
            {"expected_revision": grant.revision, "enabled": False},
        )
        return original(request, output)

    monkeypatch.setattr(executor, "complete", revoke_before_publication)
    result = state.transport.transport.execute(executor)
    assert result.status == "outcome_unknown"
    assert state.resources.get(state.agent.id, prepared.approval.id).metadata["consumed_at"]
    assert state.transport.transport.execute(executor) == result
    with pytest.raises((ApprovalError, ToolError, RevisionConflict)):
        state.resources.prepare(
            state.agent.id,
            prepared.intent.run_id,
            state.value | {"client_request_id": uuid4()},
            claim=executor.claim,
        )
    assert effect_events(state).count("started") == 1


def test_new_deny_wins_over_exact_saved_allow_rule(state):
    prepared = state.prepare()
    state.decide(prepared.approval, "allow_rule")
    executor = authority(state, prepared)
    PolicyService(state.transport.builtin[0]).create(
        state.agent.id,
        {
            "name": "Bloquear transformações no recurso",
            "effect": "deny",
            "scope": {"tool_name": "text.normalize", "action": "transform_resource"},
        },
    )
    with pytest.raises((ApprovalError, ToolError)):
        state.transport.transport.execute(executor)
    assert effect_events(state) == []
    assert not state.resources.get(state.agent.id, prepared.approval.id).metadata.get("consumed_at")
