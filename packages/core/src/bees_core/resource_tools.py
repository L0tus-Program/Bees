"""Intenção autorizável separada do despacho imutável do recurso de texto próprio.

Somente composição confiável prepara intenções ou despacha. O canal humano decide
sobre snapshots; não recebe parâmetros de autoridade nem executa ferramentas.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID, uuid5

from pydantic import field_validator

from bees_core.approvals import (
    ApprovalDecisionInput,
    ApprovalError,
    ApprovalRevokeInput,
    ApprovalService,
)
from bees_core.executor_transport.contracts import MAX_SECONDS, ResourceIdentity, TransportError
from bees_core.models import Action, Approval, Policy, utc_now
from bees_core.policies import ActionIntent, PolicyScope, ResourceTextParameters
from bees_core.security.hosts import HostService
from bees_core.storage.database import Database
from bees_core.storage.store import NotFoundError, RevisionConflict, StateStore
from bees_core.tools import TextInput, ToolError, ToolInvocationInput, ToolService, digest

KIND = "resource_tool"
TASK_KIND = "resource_tool_task"
MAX_EFFECTS = 16


class ResourceDecision(ApprovalDecisionInput):
    @field_validator("client_request_id")
    @classmethod
    def non_nil(cls, value):
        if not value.int:
            raise ValueError("Pedido humano exige UUID não nulo.")
        return value


class ResourceRevoke(ApprovalRevokeInput):
    client_request_id: UUID

    @field_validator("client_request_id")
    @classmethod
    def non_nil(cls, value):
        if not value.int:
            raise ValueError("Pedido humano exige UUID não nulo.")
        return value


@dataclass(frozen=True)
class ResourcePreparation:
    intent: Action
    approval: Approval | None
    effect: Action | None


class ResourceToolService:
    def __init__(
        self,
        database: Database,
        target: ResourceIdentity | None = None,
        *,
        clock: Callable = utc_now,
        ttl_seconds: int = 900,
    ):
        if type(ttl_seconds) is not int or not 1 <= ttl_seconds <= 3600:
            raise ValueError("Validade fora do limite.")
        self.store = StateStore(database)
        self.tools = ToolService(database, clock=clock)
        self.policies = self.tools.policies
        self.target, self.clock, self.ttl_seconds = target, clock, ttl_seconds

    @staticmethod
    def _effect_id(intent_id):
        return uuid5(UUID(str(intent_id)), "bees.resource-tool.effect/v1")

    @staticmethod
    def _scope(intent):
        return PolicyScope.model_validate(intent.model_dump(exclude={"agent_id"})).model_dump(
            mode="json"
        )

    @staticmethod
    def _target(value):
        target = ResourceIdentity(value["installation_id"], value["resource_id"], value["agent_id"])
        if target.fields() != value:
            raise ApprovalError("approval_stale")
        return target

    def _authority(self, unit, agent_id, run_id, plugin_id, arguments, target, *, review=False):
        if (
            HostService._installation(unit.agents._context.connection) != target.installation_id
            or str(agent_id) != target.agent_id
        ):
            raise ApprovalError("approval_stale")
        intent, authority = self.tools._authority(
            unit,
            agent_id,
            run_id,
            UUID(str(plugin_id)),
            "text.normalize",
            arguments,
            approval_review=review,
        )
        run = unit.runs.get(run_id)
        task = unit.tasks.get(run.task_id)
        if task.metadata.get("execution_kind") != TASK_KIND:
            raise ToolError("unsupported_task_kind", "Ferramenta exige modo explícito de tarefa.")
        parameters = ResourceTextParameters(
            **intent.parameters,
            installation_id=target.installation_id,
            plugin_id=str(plugin_id),
            plugin_revision=authority["plugin_revision"],
            manifest_hash=authority["manifest_hash"],
            grant_id=authority["grant_id"],
            grant_revision=authority["grant_revision"],
            agent_revision=authority["agent_revision"],
            tool_version="1",
        ).model_dump(mode="json")
        result = ActionIntent(
            agent_id=agent_id,
            tool_name="text.normalize",
            action="transform_resource",
            environment_id=target.resource_id,
            resource=f"resource:{target.resource_id}",
            identity="bees_user",
            parameters=parameters,
        )
        return result, authority | {
            "contract_version": 2,
            "intent_hash": digest(result.model_dump(mode="json")),
        }

    def _owned_intent(self, unit, agent_id, intent_id):
        root = unit.actions.get(intent_id)
        run = unit.runs.get(root.run_id) if root else None
        task = unit.tasks.get(run.task_id) if run else None
        if (
            task is None
            or task.agent_id != UUID(str(agent_id))
            or root.metadata.get("approval_kind") != KIND
            or root.tool_name != "tool.intent"
        ):
            raise NotFoundError("Intenção de ferramenta não encontrada nesta abelha.")
        return root, task, run

    def _owned(self, unit, agent_id, approval_id):
        approval = unit.approvals.get(approval_id)
        if approval is None:
            raise NotFoundError("Decisão de ferramenta não encontrada.")
        root, task, run = self._owned_intent(unit, agent_id, approval.action_id)
        if (
            root.metadata.get("approval_id") != str(approval.id)
            or approval.actor != "user"
            or approval.scope != self._scope(ActionIntent.model_validate(root.parameters))
        ):
            raise NotFoundError("Decisão não pertence à intenção.")
        return approval, root, task, run

    def _snapshot(self, unit, root, agent_id, *, consumed=False):
        snapshot = root.metadata["snapshot"]
        if (
            set(snapshot) != {"arguments", "target", "intent", "authority", "safety_generation"}
            or digest(snapshot) != root.metadata["snapshot_hash"]
            or root.parameters != snapshot["intent"]
        ):
            raise ApprovalError("approval_stale")
        arguments = TextInput.model_validate(snapshot["arguments"])
        target = self._target(snapshot["target"])
        intent, authority = self._authority(
            unit,
            UUID(str(agent_id)),
            root.run_id,
            snapshot["authority"]["plugin_id"],
            arguments,
            target,
            review=True,
        )
        if (
            intent.model_dump(mode="json") != snapshot["intent"]
            or authority != snapshot["authority"]
        ):
            raise ApprovalError("approval_stale")
        safety = unit.safety.state()
        if safety.status != "running":
            raise ApprovalError("global_stop")
        if safety.generation != snapshot["safety_generation"]:
            raise ApprovalError("approval_stale")
        if not consumed and self.clock() >= datetime.fromisoformat(root.metadata["expires_at"]):
            raise ApprovalError("approval_expired")
        decision = self.policies.evaluate(unit, intent)
        if decision.effect == "deny":
            raise ApprovalError("policy_denied")
        if decision.rules_hash != root.metadata["rules_hash"]:
            raise ApprovalError("approval_stale")
        return arguments, target, intent, authority, decision

    def validity(self, unit, approval):
        try:
            root = unit.actions.get(approval.action_id)
            if root is None or root.metadata.get("approval_kind") != KIND:
                return "approval_stale"
            if approval.actor != "user" or approval.scope != self._scope(
                ActionIntent.model_validate(root.parameters)
            ):
                return "approval_stale"
            if approval.status == "revoked":
                return "approval_revoked"
            if approval.status == "denied":
                return "approval_denied"
            if approval.metadata.get("consumed_at"):
                return "approval_consumed"
            self._snapshot(unit, root, root.parameters["agent_id"])
            return None
        except ApprovalError as error:
            return error.code
        except KeyError, ValueError, TypeError, ToolError, NotFoundError, TransportError:
            return "approval_stale"

    @staticmethod
    def _ask_rules(unit, decision):
        return [
            {"id": str(policy.id), "revision": policy.revision}
            for identity in decision.deciding_policy_ids
            if (policy := unit.policies.get(identity)) is not None and policy.effect == "ask"
        ]

    @staticmethod
    def _budget(task):
        count = task.metadata.get("resource_tool_actions_started", 0)
        reserved = task.metadata.get("resource_tool_reserved_milliseconds", 0)
        if type(count) is not int or type(reserved) is not int or count < 0 or reserved < 0:
            raise ToolError("task_limit_reached", "Orçamento de ferramenta inválido.")
        remaining = task.max_active_seconds * 1000 - task.active_milliseconds - reserved
        if count >= MAX_EFFECTS or remaining <= 0:
            raise ToolError("task_limit_reached", "Limite de ferramenta esgotado.")
        return count, reserved, remaining

    def _preparation(self, unit, root):
        approval_id = root.metadata.get("approval_id")
        return ResourcePreparation(
            root,
            unit.approvals.get(approval_id) if approval_id else None,
            unit.actions.get(self._effect_id(root.id)),
        )

    def prepare(self, agent_id, run_id, value, *, claim):
        if self.target is None:
            raise ToolError(
                "executor_binding_required", "Recurso deve vir da composição confiável."
            )
        raw = value.model_dump() if isinstance(value, ToolInvocationInput) else value
        value = ToolInvocationInput.model_validate(raw)
        if not value.client_request_id.int:
            raise ValueError("Intenção exige UUID não nulo.")
        agent_id, run_id = UUID(str(agent_id)), UUID(str(run_id))
        request_hash = digest(
            {
                "agent_id": str(agent_id),
                "run_id": str(run_id),
                "input": value.model_dump(mode="json"),
                "target": self.target.fields(),
            }
        )
        with self.store.transaction(actor="executor", source="resource_tool_prepare") as unit:
            prior = unit.actions.get(value.client_request_id)
            if prior is not None:
                root, _, _ = self._owned_intent(unit, agent_id, prior.id)
                if root.metadata.get("request_hash") != request_hash:
                    raise ApprovalError("idempotency_conflict")
                return self._preparation(unit, root)
            self.tools._binding(unit, claim, agent_id, run_id, now=self.clock())
            self.tools._available(unit, claim)
            task = unit.tasks.get(claim.task.id)
            self._budget(task)
            intent, authority = self._authority(
                unit, agent_id, run_id, value.plugin_id, value.arguments, self.target
            )
            decision = self.policies.evaluate(unit, intent)
            if decision.effect == "deny":
                raise ApprovalError("policy_denied")
            safety = unit.safety.state()
            if safety.status != "running":
                raise ToolError("global_stop", "Nenhuma intenção nova com Bees parado.")
            snapshot = {
                "arguments": value.arguments.model_dump(mode="json"),
                "target": self.target.fields(),
                "intent": intent.model_dump(mode="json"),
                "authority": authority,
                "safety_generation": safety.generation,
            }
            root = unit.actions.create(
                Action(
                    id=value.client_request_id,
                    run_id=run_id,
                    tool_name="tool.intent",
                    parameters=intent.model_dump(mode="json"),
                    metadata={
                        "approval_kind": KIND,
                        "snapshot": snapshot,
                        "snapshot_hash": digest(snapshot),
                        "request_hash": request_hash,
                        "rules_hash": decision.rules_hash,
                        "ask_rules": self._ask_rules(unit, decision),
                        "expires_at": (
                            self.clock() + timedelta(seconds=self.ttl_seconds)
                        ).isoformat(),
                    },
                )
            )
            if decision.effect == "ask":
                approval = unit.approvals.create(
                    Approval(action_id=root.id, scope=self._scope(intent))
                )
                root = unit.actions.update(
                    root.model_copy(
                        update={
                            "status": "awaiting_approval",
                            "metadata": root.metadata | {"approval_id": str(approval.id)},
                        }
                    ),
                    root.revision,
                )
                self._task_state(
                    unit, task, unit.runs.get(run_id), "waiting_approval", "tool_approval_required"
                )
                unit.execution.release(claim)
            else:
                root = unit.actions.update(
                    root.model_copy(update={"status": "ready"}), root.revision
                )
                self._prepare_effect(unit, root, claim)
            return self._preparation(unit, root)

    @staticmethod
    def _task_state(unit, task, run, status, error):
        unit.tasks.update(
            task.model_copy(
                update={
                    "status": status,
                    "desired_state": "running" if status in ("queued", "running") else "paused",
                }
            ),
            task.revision,
        )
        unit.runs.update(
            run.model_copy(
                update={
                    "status": status,
                    "error": error,
                    "checkpoint": run.checkpoint
                    | {"progress": status, "attention_required": status == "waiting_approval"},
                }
            ),
            run.revision,
        )

    def _prepare_effect(self, unit, root, claim):
        prior = unit.actions.get(self._effect_id(root.id))
        if prior is not None:
            return prior
        if root.status != "ready":
            raise ApprovalError("invalid_approval_state")
        arguments, target, intent, authority, decision = self._snapshot(
            unit, root, claim.task.agent_id
        )
        if self.target != target:
            raise ApprovalError("approval_stale")
        binding = self.tools._binding(
            unit, claim, claim.task.agent_id, root.run_id, now=self.clock()
        )
        self.tools._available(unit, claim)
        self._budget(unit.tasks.get(claim.task.id))
        approval_id = root.metadata.get("approval_id")
        if approval_id:
            approval = unit.approvals.get(approval_id)
            if (
                approval is None
                or approval.status != "approved"
                or approval.actor != "user"
                or approval.metadata.get("consumed_at")
                or self.validity(unit, approval)
            ):
                raise ApprovalError("approval_stale")
        elif not decision.allowed:
            raise ApprovalError("approval_required")
        effect = unit.actions.create(
            Action(
                id=self._effect_id(root.id),
                run_id=root.run_id,
                tool_name="text.normalize",
                parameters=intent.parameters,
                idempotency_key=f"resource:{root.id}",
                execution_binding=binding,
                lease_generation=claim.generation,
                metadata={
                    **authority,
                    "intent": intent.model_dump(mode="json"),
                    "resource_tool_intent_id": str(root.id),
                    "rules_hash": decision.rules_hash,
                },
            )
        )
        return unit.actions.update(effect.model_copy(update={"status": "ready"}), effect.revision)

    def prepare_dispatch(self, agent_id, intent_id, *, claim):
        with self.store.transaction(actor="executor", source="resource_tool_dispatch") as unit:
            root, _, _ = self._owned_intent(unit, agent_id, intent_id)
            return self._prepare_effect(unit, root, claim)

    def online(self, unit, agent_id, effect, arguments, target, claim, *, available):
        root, task, _ = self._owned_intent(
            unit, agent_id, effect.metadata["resource_tool_intent_id"]
        )
        if effect.id != self._effect_id(root.id) or effect.run_id != root.run_id:
            raise ApprovalError("approval_stale")
        observed, expected_target, intent, authority, decision = self._snapshot(
            unit, root, agent_id, consumed=effect.status == "dispatch_started"
        )
        if (
            target != expected_target
            or observed.model_dump(mode="json") != arguments
            or any(effect.metadata.get(key) != value for key, value in authority.items())
            or effect.metadata.get("intent") != intent.model_dump(mode="json")
            or effect.parameters != intent.parameters
        ):
            raise ApprovalError("approval_stale")
        self.tools._bound_claim(unit, effect, claim, UUID(str(agent_id)), now=self.clock())
        if available:
            run = unit.runs.get(root.run_id)
            if (
                root.status != "ready"
                or task.status != "running"
                or task.desired_state != "running"
                or run.status != "running"
            ):
                raise ApprovalError("approval_stale")
            self.tools._available(unit, claim)
            self._budget(task)
        elif effect.status == "dispatch_started" and root.status != "confirmed":
            raise ApprovalError("approval_stale")
        approval_id = root.metadata.get("approval_id")
        if approval_id:
            approval = unit.approvals.get(approval_id)
            if (
                approval is None
                or approval.actor != "user"
                or approval.status != "approved"
                or approval.action_id != root.id
                or approval.decision not in ("allow_once", "allow_rule")
            ):
                raise ApprovalError("approval_stale")
            if effect.status == "dispatch_started":
                if not approval.metadata.get("consumed_at") or approval.metadata.get(
                    "effect_action_id"
                ) != str(effect.id):
                    raise ApprovalError("approval_stale")
            elif approval.metadata.get("consumed_at"):
                raise ApprovalError("approval_consumed")
        elif not decision.allowed:
            raise ApprovalError("approval_required")
        return root, task, decision

    def consume(self, unit, agent_id, effect, arguments, target, claim, request):
        root, task, decision = self.online(
            unit, agent_id, effect, arguments, target, claim, available=True
        )
        count, reserved, remaining = self._budget(task)
        allowance = request["deadline_ms"] - int(self.clock().timestamp() * 1000)
        if not 0 < allowance <= min(int(MAX_SECONDS * 1000), remaining):
            raise ToolError("task_limit_reached", "Prazo excede orçamento restante.")
        stamp = self.clock().isoformat()
        approval_id = root.metadata.get("approval_id")
        if approval_id:
            approval = unit.approvals.get(approval_id)
            unit.approvals.update(
                approval.model_copy(
                    update={
                        "metadata": approval.metadata
                        | {"consumed_at": stamp, "effect_action_id": str(effect.id)}
                    }
                ),
                approval.revision,
            )
        root = unit.actions.update(
            root.model_copy(
                update={
                    "status": "dispatch_started",
                    "metadata": root.metadata | {"effect_action_id": str(effect.id)},
                }
            ),
            root.revision,
        )
        unit.actions.update(root.model_copy(update={"status": "confirmed"}), root.revision)
        unit.tasks.update(
            task.model_copy(
                update={
                    "metadata": task.metadata
                    | {
                        "resource_tool_actions_started": count + 1,
                        "resource_tool_reserved_milliseconds": reserved + allowance,
                    }
                }
            ),
            task.revision,
        )
        return decision

    def _human_replay(self, unit, approval, value, operation):
        path = '$.human_requests."' + str(value.client_request_id) + '"'
        prior = unit.agents._context.connection.execute(
            "SELECT id FROM approvals WHERE json_type(metadata_json,?) IS NOT NULL LIMIT 1", (path,)
        ).get
        if prior is not None and prior != str(approval.id):
            raise ApprovalError("idempotency_conflict")
        return ApprovalService._replay(approval, value, operation)

    def decide(self, agent_id, approval_id, value):
        value = ResourceDecision.model_validate(
            value.model_dump() if isinstance(value, ResourceDecision) else value
        )
        with self.store.transaction(actor="user", source="resource_tool_decision") as unit:
            approval, root, task, run = self._owned(unit, agent_id, approval_id)
            if self._human_replay(unit, approval, value, "decide"):
                return approval
            if approval.revision != value.expected_revision:
                raise RevisionConflict("Decisão mudou; confira a revisão atual.")
            if approval.status != "pending" or task.status != "waiting_approval":
                raise ApprovalError("invalid_approval_state")
            invalid = self.validity(unit, approval)
            if invalid:
                raise ApprovalError(invalid)
            intent = ActionIntent.model_validate(root.parameters)
            metadata = ApprovalService._receipt(approval, value, "decide")
            policy_id = None
            if value.decision != "allow_once":
                scope = self._scope(intent)
                self.policies._checked_scope(scope)
                rule = unit.policies.create(
                    Policy(
                        agent_id=task.agent_id,
                        name=value.rule_name or "Decisão humana de ferramenta",
                        effect="allow" if value.decision == "allow_rule" else value.decision,
                        scope=scope,
                        source="user",
                        reason=value.reason,
                        metadata={
                            "approval_grant": {
                                "approval_id": str(approval.id),
                                "policy_revision": 1,
                                "scope_hash": digest(scope),
                                "ask_rules": root.metadata["ask_rules"],
                            }
                        }
                        if value.decision == "allow_rule"
                        else {},
                    )
                )
                policy_id = rule.id if value.decision == "allow_rule" else None
                if policy_id:
                    metadata |= {
                        "authorized_scope": scope,
                        "authorized_ask_rules": root.metadata["ask_rules"],
                    }
            approved = value.decision.startswith("allow")
            approval = unit.approvals.update(
                approval.model_copy(
                    update={
                        "status": "approved"
                        if approved
                        else "denied"
                        if value.decision == "deny"
                        else "pending",
                        "decision": value.decision,
                        "reason": value.reason,
                        "decided_at": self.clock(),
                        "policy_id": policy_id,
                        "metadata": metadata,
                    }
                ),
                approval.revision,
            )
            current = self.policies.evaluate(unit, intent)
            if approved and current.effect == "deny":
                raise ApprovalError("policy_denied")
            root = unit.actions.update(
                root.model_copy(
                    update={
                        "status": "ready"
                        if approved
                        else "cancelled"
                        if value.decision == "deny"
                        else "awaiting_approval",
                        "metadata": root.metadata
                        | {
                            "rules_hash": current.rules_hash,
                            "ask_rules": root.metadata["ask_rules"]
                            if approved
                            else self._ask_rules(unit, current),
                        },
                    }
                ),
                root.revision,
            )
            self._task_state(
                unit,
                task,
                run,
                "queued"
                if approved
                else "paused"
                if value.decision == "deny"
                else "waiting_approval",
                None
                if approved
                else "approval_denied"
                if value.decision == "deny"
                else "tool_approval_required",
            )
            return approval

    def get(self, agent_id, approval_id):
        with self.store.transaction(write=False) as unit:
            return self._owned(unit, agent_id, approval_id)[0]

    def list(self, agent_id, *, task_id=None, limit=50, offset=0):
        if (
            type(limit) is not int
            or not 1 <= limit <= 101
            or type(offset) is not int
            or not 0 <= offset <= 2**31 - 1
        ):
            raise ValueError("Página de decisões inválida.")
        agent_id = UUID(str(agent_id))
        with self.store.transaction(write=False) as unit:
            if unit.agents.get(agent_id) is None:
                raise NotFoundError("Abelha não encontrada.")
            if task_id is not None:
                task = unit.tasks.get(task_id)
                if task is None or task.agent_id != agent_id:
                    raise NotFoundError("Tarefa não encontrada nesta abelha.")
            ids = unit.agents._context.connection.execute(
                "SELECT p.id FROM approvals p JOIN actions a ON a.id=p.action_id "
                "JOIN runs r ON r.id=a.run_id JOIN tasks t ON t.id=r.task_id "
                "WHERE t.agent_id=? AND json_extract(a.metadata_json,'$.approval_kind')=? "
                "AND (? IS NULL OR t.id=?) ORDER BY p.created_at DESC,p.id DESC LIMIT ? OFFSET ?",
                (
                    str(agent_id),
                    KIND,
                    str(task_id) if task_id else None,
                    str(task_id) if task_id else None,
                    limit,
                    offset,
                ),
            ).fetchall()
            return [self._owned(unit, agent_id, row[0])[0] for row in ids]

    def view(self, agent_id, approval_id, *, detail=False):
        with self.store.transaction(write=False) as unit:
            approval, root, task, run = self._owned(unit, agent_id, approval_id)
            snapshot = root.metadata["snapshot"]
            effect_id = self._effect_id(root.id)
            effect = unit.actions.get(effect_id)
            arguments = TextInput.model_validate(snapshot["arguments"])
            invalid = self.validity(unit, approval)
            return {
                "kind": KIND,
                "id": str(approval.id),
                "agent_id": str(task.agent_id),
                "task_id": str(task.id),
                "run_id": str(run.id),
                "intent_action_id": str(root.id),
                "effect_action_id": str(effect_id),
                "effect_status": effect.status if effect else None,
                "revision": approval.revision,
                "status": approval.status,
                "decision": approval.decision,
                "created_at": approval.created_at.isoformat(),
                "expires_at": root.metadata["expires_at"],
                "consumed_at": approval.metadata.get("consumed_at"),
                "valid": invalid is None,
                "stale_reason": invalid,
                "scope": approval.scope,
                "target": snapshot["target"],
                "tool_name": "text.normalize",
                "tool_version": "1",
                "operation": arguments.operation,
                "input_hash": root.parameters["parameters"]["input_hash"],
                "input_bytes": len(arguments.text.encode("utf-8")),
                "input_preview": arguments.text
                if detail and digest(snapshot) == root.metadata["snapshot_hash"]
                else None,
                "task_title": task.title,
            }

    def revoke(self, agent_id, approval_id, value):
        value = ResourceRevoke.model_validate(
            value.model_dump() if isinstance(value, ResourceRevoke) else value
        )
        with self.store.transaction(actor="user", source="resource_tool_revoke") as unit:
            approval, root, task, run = self._owned(unit, agent_id, approval_id)
            if self._human_replay(unit, approval, value, "revoke"):
                return approval
            if approval.revision != value.expected_revision:
                raise RevisionConflict("Decisão mudou; confira a revisão atual.")
            if approval.status not in ("pending", "approved"):
                raise ApprovalError("invalid_approval_state")
            approval = unit.approvals.update(
                approval.model_copy(
                    update={
                        "status": "revoked",
                        "metadata": ApprovalService._receipt(approval, value, "revoke"),
                    }
                ),
                approval.revision,
            )
            if approval.policy_id:
                policy = unit.policies.get(approval.policy_id)
                if policy and policy.status == "active":
                    unit.policies.update(
                        policy.model_copy(update={"status": "revoked"}), policy.revision
                    )
            if not approval.metadata.get("consumed_at"):
                if root.status in ("prepared", "ready", "awaiting_approval"):
                    unit.actions.update(
                        root.model_copy(update={"status": "cancelled"}), root.revision
                    )
                latest = unit.runs.latest(task.id)
                if (
                    task.status not in ("completed", "cancelled", "failed")
                    and latest is not None
                    and latest.id == run.id
                    and task.control_revision
                    == root.metadata["snapshot"]["authority"]["task_control_revision"]
                ):
                    self._task_state(unit, task, run, "paused", "approval_revoked")
            return approval
