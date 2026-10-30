"""Intenções, decisões humanas e efeitos das ferramentas do agente.

Mesmo contrato do recurso de texto (BEES-009/010), generalizado por ``ToolSpec``:

1. ``prepare`` persiste a intenção raiz ``tool.intent`` com snapshot privado dos
   argumentos, alvo, autoridade e geração da parada. Política ``ask`` cria pedido
   humano e libera os leases; ``deny`` recusa sem efeito.
2. ``begin`` reavalia a autoridade online na transação que consome a decisão,
   contabiliza o limite e registra ``dispatch_started`` antes do executor.
3. ``complete``/``fail``/``unknown`` publicam o resultado somente pelo dono atual.

A composição confiável escolhe alvo e extensão. Argumentos do modelo nunca
selecionam ambiente, identidade, concessão ou autoridade.
"""

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal
from uuid import UUID, uuid5

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from bees_core.agent_tools.catalog import ToolSpec, digest
from bees_core.agent_tools.catalog import spec as get_spec
from bees_core.approvals import ApprovalError, ApprovalService
from bees_core.models import Action, Approval, Policy, utc_now
from bees_core.policies import ActionIntent, PolicyScope, PolicyService
from bees_core.storage.database import Database
from bees_core.storage.store import NotFoundError, RevisionConflict, StateStore
from bees_core.tasks import TaskError
from bees_core.tools import ToolError, ToolService

KIND = "agent_tool"
AGENT_TASK_KIND = "agent_task"
MAX_EFFECTS = 120
MAX_ARGUMENT_BYTES = 450_000


@dataclass(frozen=True)
class ToolTarget:
    """Alvo resolvido pela composição; nunca derivado dos argumentos do modelo."""

    kind: str
    environment_id: str
    resource: str
    identity: str
    executor_id: str | None = None

    def fields(self) -> dict:
        return {
            "kind": self.kind,
            "environment_id": self.environment_id,
            "resource": self.resource,
            "identity": self.identity,
            "executor_id": self.executor_id,
        }

    @classmethod
    def from_fields(cls, value: dict) -> ToolTarget:
        target = cls(**value)
        if target.fields() != value:
            raise ApprovalError("approval_stale")
        return target


class AgentToolDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    client_request_id: UUID
    expected_revision: int = Field(ge=1, strict=True)
    decision: Literal["allow_once", "allow_rule", "allow_tool", "deny"]
    reason: str = Field(default="", max_length=2000)
    rule_name: str | None = Field(default=None, min_length=1, max_length=200)

    @field_validator("client_request_id")
    @classmethod
    def non_nil(cls, value):
        if not value.int:
            raise ValueError("Pedido humano exige UUID não nulo.")
        return value

    @field_validator("reason", "rule_name")
    @classmethod
    def safe_text(cls, value):
        if value is not None and "\x00" in value:
            raise ValueError("Texto inválido.")
        return value


class AgentToolRevoke(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    client_request_id: UUID
    expected_revision: int = Field(ge=1, strict=True)
    status: Literal["revoked"] = "revoked"


class Invocation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    client_request_id: UUID
    plugin_id: UUID
    tool_name: str
    arguments: dict


@dataclass(frozen=True)
class Preparation:
    intent: Action
    approval: Approval | None
    effect: Action | None


def effect_id(intent_id) -> UUID:
    return uuid5(UUID(str(intent_id)), "bees.agent-tool.effect/v1")


class AgentToolService:
    def __init__(
        self,
        database: Database,
        *,
        clock: Callable = utc_now,
        ttl_seconds: int = 1800,
        targets=None,
    ):
        if type(ttl_seconds) is not int or not 1 <= ttl_seconds <= 86400:
            raise ValueError("Validade fora do limite.")
        self.store = StateStore(database)
        self.tools = ToolService(database, clock=clock)
        self.policies: PolicyService = self.tools.policies
        self.clock = clock
        self.ttl_seconds = ttl_seconds
        # Resolvedor confiável de alvos (web, plano de controle, computador, pessoal).
        from bees_core.agent_tools.targets import TargetResolver

        self.targets = targets or TargetResolver()

    # ------------------------------------------------------------ autoridade

    @staticmethod
    def _spec(name) -> ToolSpec:
        spec = get_spec(name)
        if spec is None:
            raise ToolError("unknown_tool", "Ferramenta fora do registro confiável.")
        return spec

    @staticmethod
    def arguments(spec: ToolSpec, raw: dict) -> BaseModel:
        try:
            value = spec.input_model.model_validate(raw)
        except ValidationError as error:
            # Campo e motivo (sem os valores enviados): o modelo corrige e tenta de novo.
            problems = [
                f"{'.'.join(str(part) for part in item['loc']) or 'argumentos'}: {item['msg']}"
                for item in error.errors(include_input=False, include_url=False)[:5]
            ]
            raise ToolError("invalid_tool_input", "; ".join(problems)[:500]) from None
        except ValueError, TypeError:
            raise ToolError("invalid_tool_input", "Argumentos incompatíveis.") from None
        encoded = json.dumps(value.model_dump(mode="json"), ensure_ascii=False)
        if len(encoded.encode("utf-8")) > MAX_ARGUMENT_BYTES:
            raise ToolError("invalid_tool_input", "Argumentos excedem o limite.")
        return value

    def _authority(self, unit, agent_id, run_id, plugin_id, spec, arguments, target, *, review):
        agent = unit.agents.get(agent_id)
        run = unit.runs.get(run_id)
        task = unit.tasks.get(run.task_id) if run else None
        if agent is None or run is None or task is None or task.agent_id != agent_id:
            raise NotFoundError("Execução não encontrada nesta abelha.")
        latest = unit.runs.latest(task.id)
        states = ("queued", "running", "waiting_approval") if review else ("running",)
        if (
            agent.status != "active"
            or task.desired_state == "cancelled"
            or (not review and task.desired_state != "running")
            or task.status not in states
            or run.status not in states
            or latest is None
            or latest.id != run.id
        ):
            raise ToolError("execution_unavailable", "Execução indisponível para nova ação.")
        if task.metadata.get("execution_kind") != AGENT_TASK_KIND:
            raise ToolError("unsupported_task_kind", "Ferramenta exige tarefa do agente.")
        plugin = unit.plugins.get(plugin_id)
        if plugin is None:
            raise ToolError("unknown_tool", "Extensão não instalada.")
        manifest = ToolService._manifest(plugin)
        if not plugin.enabled:
            raise ToolError("tool_disabled", "Extensão desabilitada.")
        if spec.name not in {tool.tool_name for tool in manifest.tools}:
            raise ToolError("unknown_tool", "Ferramenta não registrada nesta extensão.")
        grant = unit.tool_grants.find(agent_id, plugin.id, spec.name)
        if grant is None or not grant.enabled:
            raise ToolError("tool_not_granted", "Uso não concedido para esta abelha.")
        self.targets.check(unit, agent_id, spec, target)
        from bees_core.routine_context import routine_scope_hash, validate_routine_task
        from bees_core.skill_context import validate_skill_task

        try:
            skill = validate_skill_task(unit, task, tool_name=spec.name)
            routine = validate_routine_task(unit, task)
        except TaskError:
            raise ApprovalError("approval_stale") from None
        try:
            parameters = spec.parameters_model(
                input_hash=digest(arguments.model_dump(mode="json")),
                plugin_id=str(plugin.id),
                plugin_revision=plugin.revision,
                manifest_hash=plugin.manifest_hash,
                grant_id=str(grant.id),
                grant_revision=grant.revision,
                agent_revision=agent.revision,
                tool_version=spec.version,
                **spec.scope_fields(arguments),
                **({"skill_execution_hash": digest(skill)} if skill is not None else {}),
                **(
                    {"routine_scope_hash": routine_scope_hash(routine)}
                    if routine is not None
                    else {}
                ),
            ).model_dump(mode="json", exclude_none=True)
        except ValidationError, ValueError:
            raise ToolError("invalid_tool_input", "Escopo inválido para a política.") from None
        intent = ActionIntent(
            agent_id=agent_id,
            tool_name=spec.name,
            action=spec.action,
            environment_id=target.environment_id,
            resource=target.resource,
            identity=target.identity,
            parameters=parameters,
        )
        authority = {
            "contract_version": 3,
            "plugin_id": str(plugin.id),
            "plugin_revision": plugin.revision,
            "manifest_hash": plugin.manifest_hash,
            "grant_id": str(grant.id),
            "grant_revision": grant.revision,
            "agent_revision": agent.revision,
            "task_control_revision": task.control_revision,
            "run_id": str(run.id),
            "tool_version": spec.version,
            "execution_kind": AGENT_TASK_KIND,
            "intent_hash": digest(intent.model_dump(mode="json")),
        }
        return intent, authority

    @staticmethod
    def _scope(intent: ActionIntent) -> dict:
        return PolicyScope.model_validate(intent.model_dump(exclude={"agent_id"})).model_dump(
            mode="json"
        )

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
            set(snapshot)
            != {"tool_name", "arguments", "target", "intent", "authority", "safety_generation"}
            or digest(snapshot) != root.metadata["snapshot_hash"]
            or root.parameters != snapshot["intent"]
        ):
            raise ApprovalError("approval_stale")
        spec = self._spec(snapshot["tool_name"])
        arguments = self.arguments(spec, snapshot["arguments"])
        target = ToolTarget.from_fields(snapshot["target"])
        intent, authority = self._authority(
            unit,
            UUID(str(agent_id)),
            root.run_id,
            UUID(snapshot["authority"]["plugin_id"]),
            spec,
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
        if decision.rules_hash != root.metadata["rules_hash"] and not (
            root.metadata.get("approval_id") is None and decision.allowed
        ):
            raise ApprovalError("approval_stale")
        return spec, arguments, target, intent, authority, decision

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
        except KeyError, ValueError, TypeError, ToolError, NotFoundError:
            return "approval_stale"

    @staticmethod
    def _ask_rules(unit, decision):
        return [
            {"id": str(policy.id), "revision": policy.revision}
            for identity in decision.deciding_policy_ids
            if (policy := unit.policies.get(identity)) is not None and policy.effect == "ask"
        ]

    @staticmethod
    def budget(task):
        count = task.metadata.get("agent_tool_actions_started", 0)
        if type(count) is not int or count < 0:
            raise ToolError("task_limit_reached", "Orçamento de ferramenta inválido.")
        limit = min(MAX_EFFECTS, task.metadata.get("agent_tool_limit", MAX_EFFECTS))
        remaining = task.max_active_seconds * 1000 - task.active_milliseconds
        if count >= limit or remaining <= 0:
            raise ToolError("task_limit_reached", "Limite de ferramenta esgotado.")
        return count, remaining

    def _preparation(self, unit, root):
        approval_id = root.metadata.get("approval_id")
        return Preparation(
            root,
            unit.approvals.get(approval_id) if approval_id else None,
            unit.actions.get(effect_id(root.id)),
        )

    # ------------------------------------------------------------ executor

    def prepare(self, agent_id, run_id, value, *, claim):
        value = Invocation.model_validate(value)
        if not value.client_request_id.int:
            raise ValueError("Intenção exige UUID não nulo.")
        spec = self._spec(value.tool_name)
        arguments = self.arguments(spec, value.arguments)
        agent_id, run_id = UUID(str(agent_id)), UUID(str(run_id))
        with self.store.transaction(actor="executor", source="agent_tool_prepare") as unit:
            target = self.targets.resolve(unit, agent_id, spec)
            request_hash = digest(
                {
                    "agent_id": str(agent_id),
                    "run_id": str(run_id),
                    "plugin_id": str(value.plugin_id),
                    "tool_name": spec.name,
                    "arguments": arguments.model_dump(mode="json"),
                    "target": target.fields(),
                }
            )
            prior = unit.actions.get(value.client_request_id)
            if prior is not None:
                root, _, _ = self._owned_intent(unit, agent_id, prior.id)
                if root.metadata.get("request_hash") != request_hash:
                    raise ApprovalError("idempotency_conflict")
                return self._preparation(unit, root)
            self.tools._binding(unit, claim, agent_id, run_id, now=self.clock())
            self.tools._available(unit, claim)
            task = unit.tasks.get(claim.task.id)
            self.budget(task)
            intent, authority = self._authority(
                unit, agent_id, run_id, value.plugin_id, spec, arguments, target, review=False
            )
            decision = self.policies.evaluate(unit, intent)
            if decision.effect == "deny":
                raise ApprovalError("policy_denied")
            safety = unit.safety.state()
            if safety.status != "running":
                raise ToolError("global_stop", "Nenhuma intenção nova com Bees parado.")
            snapshot = {
                "tool_name": spec.name,
                "arguments": arguments.model_dump(mode="json"),
                "target": target.fields(),
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
                        "agent_tool": spec.name,
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
        prior = unit.actions.get(effect_id(root.id))
        if prior is not None:
            return prior
        if root.status != "ready":
            raise ApprovalError("invalid_approval_state")
        spec, _, target, intent, authority, decision = self._snapshot(
            unit, root, claim.task.agent_id
        )
        binding = self.tools._binding(
            unit, claim, claim.task.agent_id, root.run_id, now=self.clock()
        )
        self.tools._available(unit, claim)
        self.budget(unit.tasks.get(claim.task.id))
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
                id=effect_id(root.id),
                run_id=root.run_id,
                tool_name=spec.name,
                parameters=intent.parameters,
                idempotency_key=f"agent:{root.id}",
                execution_binding=binding,
                lease_generation=claim.generation,
                metadata={
                    **authority,
                    "intent": intent.model_dump(mode="json"),
                    "agent_tool_intent_id": str(root.id),
                    "target": target.fields(),
                    "rules_hash": decision.rules_hash,
                },
            )
        )
        return unit.actions.update(effect.model_copy(update={"status": "ready"}), effect.revision)

    def prepare_dispatch(self, agent_id, intent_id, *, claim):
        with self.store.transaction(actor="executor", source="agent_tool_dispatch") as unit:
            root, _, _ = self._owned_intent(unit, agent_id, intent_id)
            return self._prepare_effect(unit, root, claim)

    def begin(self, agent_id, effect_action_id, *, claim, on_begin=None):
        """Guarda online, consumo e início na mesma transação, antes do executor.

        ``on_begin(unit, effect, spec, arguments, target)`` permite ao executor
        registrar seu comando durável no mesmo commit do início.
        """
        agent_id = UUID(str(agent_id))
        with self.store.transaction(actor="executor", source="agent_tool_begin") as unit:
            effect = self.tools._owned_action(unit, agent_id, effect_action_id)
            root, task, _ = self._owned_intent(
                unit, agent_id, effect.metadata["agent_tool_intent_id"]
            )
            if effect.id != effect_id(root.id) or effect.status != "ready":
                raise ToolError("invocation_unavailable", "Efeito já iniciado; não repetir.")
            spec, arguments, target, intent, authority, decision = self._snapshot(
                unit, root, agent_id
            )
            if (
                any(effect.metadata.get(key) != value for key, value in authority.items())
                or effect.metadata.get("intent") != intent.model_dump(mode="json")
                or effect.parameters != intent.parameters
                or effect.metadata.get("target") != target.fields()
            ):
                raise ApprovalError("approval_stale")
            self.tools._bound_claim(unit, effect, claim, agent_id, now=self.clock())
            run = unit.runs.get(root.run_id)
            if (
                root.status != "ready"
                or task.status != "running"
                or task.desired_state != "running"
                or run.status != "running"
            ):
                raise ApprovalError("approval_stale")
            self.tools._available(unit, claim)
            count, _ = self.budget(task)
            approval_id = root.metadata.get("approval_id")
            stamp = self.clock().isoformat()
            if approval_id:
                approval = unit.approvals.get(approval_id)
                if (
                    approval is None
                    or approval.actor != "user"
                    or approval.status != "approved"
                    or approval.action_id != root.id
                    or approval.metadata.get("consumed_at")
                ):
                    raise ApprovalError("approval_stale")
                unit.approvals.update(
                    approval.model_copy(
                        update={
                            "metadata": approval.metadata
                            | {"consumed_at": stamp, "effect_action_id": str(effect.id)}
                        }
                    ),
                    approval.revision,
                )
            elif not decision.allowed:
                raise ApprovalError("approval_required")
            self.targets.ready(unit, agent_id, spec, target)
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
                    update={"metadata": task.metadata | {"agent_tool_actions_started": count + 1}}
                ),
                task.revision,
            )
            effect = unit.actions.update(
                effect.model_copy(
                    update={
                        "status": "dispatch_started",
                        "metadata": effect.metadata
                        | {"dispatch_rules_hash": decision.rules_hash, "started_at": stamp},
                    }
                ),
                effect.revision,
            )
            if on_begin is not None:
                on_begin(unit, effect, spec, arguments, target)
            return effect, spec, arguments, target

    def _publishable(self, unit, agent_id, action_id, claim):
        effect = self.tools._owned_action(unit, agent_id, action_id)
        if effect.status != "dispatch_started":
            return effect, False
        try:
            self.tools._bound_claim(unit, effect, claim, agent_id, now=self.clock())
        except RevisionConflict:
            return effect, False
        return effect, True

    def complete(self, agent_id, action_id, result: dict, *, claim):
        agent_id = UUID(str(agent_id))
        with self.store.transaction(actor="executor", source="agent_tool_result") as unit:
            effect, owner = self._publishable(unit, agent_id, action_id, claim)
            if effect.status != "dispatch_started":
                return effect
            if not owner:
                return self._unknown(unit, effect, "executor_publication_fenced")
            return unit.actions.update(
                effect.model_copy(
                    update={
                        "status": "confirmed",
                        "result": result,
                        "metadata": effect.metadata
                        | {
                            "executor_receipt_hash": digest(result),
                            "obsolete": ToolService._obsolete(unit, effect),
                        },
                    }
                ),
                effect.revision,
            )

    def fail(self, agent_id, action_id, code: str, *, claim, detail: str | None = None):
        """Executor comprovou ausência de efeito (validação/recusa/observação falha)."""
        agent_id = UUID(str(agent_id))
        with self.store.transaction(actor="executor", source="agent_tool_result") as unit:
            effect, owner = self._publishable(unit, agent_id, action_id, claim)
            if effect.status != "dispatch_started":
                return effect
            if not owner:
                return self._unknown(unit, effect, "executor_publication_fenced")
            return unit.actions.update(
                effect.model_copy(
                    update={
                        "status": "failed_no_effect",
                        "result": {"error": code, **({"detail": detail[:500]} if detail else {})},
                        "metadata": effect.metadata | {"error_code": code},
                    }
                ),
                effect.revision,
            )

    def _unknown(self, unit, effect, code):
        updated = unit.actions.update(
            effect.model_copy(
                update={
                    "status": "outcome_unknown",
                    "metadata": effect.metadata | {"error_code": code},
                }
            ),
            effect.revision,
        )
        unit.execution.quarantine_action(updated, now=self.clock())
        return updated

    def unknown(self, agent_id, action_id, code: str):
        agent_id = UUID(str(agent_id))
        with self.store.transaction(actor="supervisor", source="agent_tool_unknown") as unit:
            effect = self.tools._owned_action(unit, agent_id, action_id)
            if effect.status != "dispatch_started":
                return effect
            return self._unknown(unit, effect, code)

    # ------------------------------------------------------------ canal humano

    def _human_replay(self, unit, approval, value, operation):
        path = '$.human_requests."' + str(value.client_request_id) + '"'
        prior = unit.agents._context.connection.execute(
            "SELECT id FROM approvals WHERE json_type(metadata_json,?) IS NOT NULL LIMIT 1", (path,)
        ).get
        if prior is not None and prior != str(approval.id):
            raise ApprovalError("idempotency_conflict")
        return ApprovalService._replay(approval, value, operation)

    def decide(self, agent_id, approval_id, value):
        value = AgentToolDecision.model_validate(
            value.model_dump() if isinstance(value, AgentToolDecision) else value
        )
        with self.store.transaction(actor="user", source="agent_tool_decision") as unit:
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
            approved = value.decision.startswith("allow")
            if value.decision == "allow_rule":
                scope = self._scope(intent)
                self.policies._checked_scope(scope)
                rule = unit.policies.create(
                    Policy(
                        agent_id=task.agent_id,
                        name=value.rule_name or "Decisão humana de ferramenta",
                        effect="allow",
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
                        },
                    )
                )
                policy_id = rule.id
                metadata |= {
                    "authorized_scope": scope,
                    "authorized_ask_rules": root.metadata["ask_rules"],
                }
            elif value.decision == "allow_tool":
                # Regra ampla explícita: ferramenta e ambiente, sem parâmetros. Não
                # anula regras Perguntar/Bloquear já existentes (o mais restritivo vence).
                scope = PolicyScope(
                    tool_name=intent.tool_name,
                    action=intent.action,
                    environment_id=intent.environment_id,
                ).model_dump(mode="json")
                rule = unit.policies.create(
                    Policy(
                        agent_id=task.agent_id,
                        name=value.rule_name or f"Sempre permitir {intent.tool_name}",
                        effect="allow",
                        scope=scope,
                        source="user",
                        reason=value.reason,
                    )
                )
                metadata |= {"created_policy_id": str(rule.id)}
            approval = unit.approvals.update(
                approval.model_copy(
                    update={
                        "status": "approved" if approved else "denied",
                        "decision": "allow_rule"
                        if value.decision == "allow_tool"
                        else value.decision,
                        "reason": value.reason,
                        "decided_at": self.clock(),
                        "policy_id": policy_id,
                        "metadata": metadata | {"agent_decision": value.decision},
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
                        "status": "ready" if approved else "cancelled",
                        "metadata": root.metadata | {"rules_hash": current.rules_hash},
                    }
                ),
                root.revision,
            )
            # Recusa não pausa: o agente recebe o resultado e decide o próximo passo.
            self._task_state(unit, task, run, "queued", None)
            return approval

    def revoke(self, agent_id, approval_id, value):
        value = AgentToolRevoke.model_validate(
            value.model_dump() if isinstance(value, AgentToolRevoke) else value
        )
        with self.store.transaction(actor="user", source="agent_tool_revoke") as unit:
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
            # "Sempre permitir" cria regra ampla registrada só nos metadados da decisão;
            # revogar a decisão revoga também essa regra.
            created = approval.metadata.get("created_policy_id")
            for policy_id in (approval.policy_id, UUID(created) if created else None):
                if not policy_id:
                    continue
                policy = unit.policies.get(policy_id)
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
                ):
                    self._task_state(unit, task, run, "paused", "approval_revoked")
            return approval

    def get(self, agent_id, approval_id):
        with self.store.transaction(write=False) as unit:
            return self._owned(unit, agent_id, approval_id)[0]

    def list(self, agent_id, *, task_id=None, status=None, limit=50, offset=0):
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
            rows = unit.agents._context.connection.execute(
                "SELECT p.id FROM approvals p JOIN actions a ON a.id=p.action_id "
                "JOIN runs r ON r.id=a.run_id JOIN tasks t ON t.id=r.task_id "
                "WHERE t.agent_id=? AND json_extract(a.metadata_json,'$.approval_kind')=? "
                "AND (? IS NULL OR t.id=?) AND (? IS NULL OR p.status=?) "
                "ORDER BY p.created_at DESC,p.id DESC LIMIT ? OFFSET ?",
                (
                    str(agent_id),
                    KIND,
                    str(task_id) if task_id else None,
                    str(task_id) if task_id else None,
                    status,
                    status,
                    limit,
                    offset,
                ),
            ).fetchall()
            return [self.view(agent_id, row[0], unit=unit) for row in rows]

    def view(self, agent_id, approval_id, *, detail=False, unit=None):
        if unit is None:
            with self.store.transaction(write=False) as current:
                return self.view(agent_id, approval_id, detail=detail, unit=current)
        approval, root, task, run = self._owned(unit, agent_id, approval_id)
        snapshot = root.metadata["snapshot"]
        effect = unit.actions.get(effect_id(root.id))
        invalid = self.validity(unit, approval)
        spec = get_spec(snapshot["tool_name"])
        arguments = snapshot["arguments"]
        return {
            "kind": KIND,
            "id": str(approval.id),
            "agent_id": str(task.agent_id),
            "task_id": str(task.id),
            "run_id": str(run.id),
            "intent_action_id": str(root.id),
            "effect_action_id": str(effect_id(root.id)),
            "effect_status": effect.status if effect else None,
            "revision": approval.revision,
            "status": approval.status,
            "decision": approval.metadata.get("agent_decision", approval.decision),
            "created_at": approval.created_at.isoformat(),
            "expires_at": root.metadata["expires_at"],
            "consumed_at": approval.metadata.get("consumed_at"),
            "valid": invalid is None,
            "stale_reason": invalid,
            "scope": approval.scope,
            "target": snapshot["target"],
            "tool_name": snapshot["tool_name"],
            "tool_version": spec.version if spec else "1",
            "effect": spec.effect if spec else "write",
            "summary": summarize(snapshot["tool_name"], arguments),
            "arguments_preview": preview(arguments) if detail else None,
            "input_hash": root.parameters["parameters"]["input_hash"],
            "task_title": task.title,
        }


def preview(arguments: dict, limit: int = 4000) -> dict:
    """Projeção limitada dos argumentos para revisão humana explícita."""
    result = {}
    for key, value in arguments.items():
        if isinstance(value, str) and len(value) > limit:
            result[key] = value[:limit] + f"… (+{len(value) - limit} caracteres)"
        else:
            result[key] = value
    return result


def summarize(tool_name: str, arguments: dict) -> str:
    """Resumo curto e determinístico; nunca interpreta o conteúdo como instrução."""
    keys = ("command", "path", "url", "query", "name", "content", "text", "selector")
    for key in keys:
        value = arguments.get(key)
        if isinstance(value, str) and value:
            value = " ".join(value.split())
            return f"{tool_name}: {value[:160]}" + ("…" if len(value) > 160 else "")
    return tool_name
