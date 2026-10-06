"""Decisões humanas concretas, duráveis e consumidas no despacho transacional.

Action conserva o snapshot já preparado sem valores de credenciais. Approval
vincula a decisão à execução, ao controle, ao contexto e às regras observadas.
O transporte deve projetar campos públicos; nunca expor Action.metadata.
"""

import hashlib
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from bees_core.models import Action, Approval, Policy, Run, Task, utc_now
from bees_core.policies import PolicyDecision, PolicyScope, PolicyService, _canonical
from bees_core.providers.contracts import SecretResolver
from bees_core.providers.errors import ProviderError
from bees_core.providers.service import PreparedChat, ProviderService
from bees_core.storage.database import Database
from bees_core.storage.store import (
    NotFoundError,
    RevisionConflict,
    StateStore,
    UnitOfWork,
    _pagination,
)


class ApprovalError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class ApprovalDecisionInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    client_request_id: UUID
    expected_revision: int = Field(ge=1, strict=True)
    decision: Literal["allow_once", "allow_rule", "ask", "deny"]
    reason: str = Field(default="", max_length=2000)
    rule_name: str | None = Field(default=None, min_length=1, max_length=200)

    @field_validator("reason", "rule_name")
    @classmethod
    def safe_text(cls, value: str | None) -> str | None:
        if value is not None and "\x00" in value:
            raise ValueError("Texto inválido.")
        if value is not None and not value.strip() and value != "":
            raise ValueError("Nome exige texto útil.")
        return value


class ApprovalRevokeInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    client_request_id: UUID | None = None
    expected_revision: int = Field(ge=1, strict=True)
    status: Literal["revoked"] = "revoked"


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode()).hexdigest()


class ApprovalService:
    """Somente decide/revoke são expostos ao canal humano autenticado.

    request/check/consume/prepared_for_run/finish pertencem ao executor. Consumo
    deve ocorrer na mesma UnitOfWork que begin_call, antes de qualquer rede.
    O serviço não mantém transações abertas durante integrações externas.
    """

    def __init__(
        self,
        database: Database,
        resolver: SecretResolver | None = None,
        *,
        ttl_seconds: int = 900,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        if type(ttl_seconds) is not int or not 1 <= ttl_seconds <= 3600:
            raise ValueError("Validade deve estar entre 1 e 3600 segundos.")
        self.store = StateStore(database)
        self.providers = ProviderService(database, resolver)
        self.policies = PolicyService(database)
        self.ttl_seconds = ttl_seconds
        self.clock = clock

    @staticmethod
    def _owned(unit: UnitOfWork, agent_id: UUID | str, approval_id: UUID | str):
        approval = unit.approvals.get(approval_id)
        action = unit.actions.get(approval.action_id) if approval else None
        run = unit.runs.get(action.run_id) if action else None
        task = unit.tasks.get(run.task_id) if run else None
        if task is None or task.agent_id != UUID(str(agent_id)):
            raise NotFoundError("Aprovação não encontrada nesta abelha.")
        return approval, action, task, run

    @staticmethod
    def _directive(run: Run) -> int:
        return int(run.checkpoint.get("directive_revision", 0))

    def _current(self, unit: UnitOfWork, task: Task, run: Run) -> Approval | None:
        approval_id = run.checkpoint.get("approval_id")
        if not approval_id:
            return None
        try:
            approval, action, owner, _ = self._owned(unit, task.agent_id, str(approval_id))
        except NotFoundError, ValueError:
            return None
        if action.run_id != run.id or owner.id != task.id:
            return None
        return approval

    def validity(self, unit: UnitOfWork, approval: Approval) -> str | None:
        """Motivo público de obsolescência; não revela snapshot nem segredos."""
        try:
            action = unit.actions.get(approval.action_id)
            if action is None or action.metadata.get("approval_kind") != "model_generate":
                return "approval_stale"
            run = unit.runs.get(action.run_id)
            task = unit.tasks.get(run.task_id) if run else None
            if task is None or run is None or approval.actor != "user":
                return "approval_stale"
            if approval.status == "revoked":
                return "approval_revoked"
            if approval.status == "denied":
                return "approval_denied"
            if approval.metadata.get("consumed_at"):
                return "approval_consumed"
            expiry = datetime.fromisoformat(str(action.metadata["expires_at"]))
            if self.clock() >= expiry:
                return "approval_expired"
            if (
                task.status in ("completed", "cancelled", "failed")
                or task.desired_state == "cancelled"
                or task.control_revision != action.metadata["control_revision"]
                or self._directive(run) != action.metadata["directive_revision"]
            ):
                return "approval_stale"
            runs = unit.runs.list(task_id=task.id, limit=1000)
            if not runs or runs[-1].id != run.id:
                return "approval_stale"
            if any(
                call.status in ("dispatch_started", "outcome_unknown")
                for call in unit.model_calls.list(run_id=run.id, limit=1000)
            ):
                return "approval_stale"
            prepared = PreparedChat.model_validate(action.metadata["prepared"])
            if _digest(prepared.model_dump(mode="json")) != action.metadata["snapshot_hash"]:
                return "approval_stale"
            if prepared.agent_id != task.agent_id or prepared.task_id != task.id:
                return "approval_stale"
            current = self.providers.model_policy(unit, prepared)
            if current.effect == "deny":
                return "policy_denied"
            if current.rules_hash != action.metadata["rules_hash"]:
                return "approval_stale"
            return None
        except KeyError, ValueError, TypeError, ProviderError, NotFoundError:
            return "approval_stale"

    def request(
        self,
        unit: UnitOfWork,
        task: Task,
        run: Run,
        prepared: PreparedChat,
        decision: PolicyDecision,
    ) -> Approval:
        current = self.providers.model_policy(unit, prepared)
        if decision.effect != "ask" or current != decision:
            raise ApprovalError("approval_stale")
        if (
            run.task_id != task.id
            or prepared.task_id != task.id
            or prepared.agent_id != task.agent_id
            or task.status in ("completed", "failed", "cancelled")
            or task.desired_state == "cancelled"
        ):
            raise ApprovalError("approval_stale")
        snapshot = prepared.model_dump(mode="json")
        previous = self._current(unit, task, run)
        if previous is not None and self.validity(unit, previous) is None:
            action = unit.actions.get(previous.action_id)
            if action.metadata["snapshot_hash"] == _digest(snapshot):
                return previous
        if previous is not None and previous.status in ("pending", "approved"):
            if not previous.metadata.get("consumed_at"):
                unit.approvals.update(
                    previous.model_copy(update={"status": "revoked"}), previous.revision
                )
                old = unit.actions.get(previous.action_id)
                if old.status in ("prepared", "awaiting_approval", "ready"):
                    unit.actions.update(
                        old.model_copy(update={"status": "cancelled"}), old.revision
                    )
        intent = self.providers.model_intent(prepared.agent_id, prepared.config, prepared.request)
        stamp = self.clock()
        action = unit.actions.create(
            Action(
                run_id=run.id,
                tool_name="model",
                parameters=intent.model_dump(mode="json"),
                status="awaiting_approval",
                metadata={
                    "approval_kind": "model_generate",
                    "prepared": snapshot,
                    "snapshot_hash": _digest(snapshot),
                    "control_revision": task.control_revision,
                    "directive_revision": self._directive(run),
                    "rules_hash": decision.rules_hash,
                    "expires_at": (stamp + timedelta(seconds=self.ttl_seconds)).isoformat(),
                    "ask_rules": [
                        {"id": str(policy.id), "revision": policy.revision}
                        for policy_id in decision.deciding_policy_ids
                        if (policy := unit.policies.get(policy_id)) is not None
                    ],
                },
            )
        )
        approval = unit.approvals.create(
            Approval(action_id=action.id, scope=intent.model_dump(mode="json"))
        )
        unit.runs.update(
            run.model_copy(
                update={
                    "checkpoint": run.checkpoint
                    | {
                        "approval_id": str(approval.id),
                        "action_id": str(action.id),
                    }
                }
            ),
            run.revision,
        )
        return approval

    def prepared_for_run(self, unit: UnitOfWork, task: Task, run: Run) -> PreparedChat | None:
        approval = self._current(unit, task, run)
        if approval is None:
            return None
        invalid = self.validity(unit, approval)
        if invalid:
            if invalid in ("approval_consumed", "approval_denied", "approval_revoked"):
                return None
            action = unit.actions.get(approval.action_id)
            prepared = PreparedChat.model_validate(action.metadata["prepared"])
            self.providers.validate_snapshot(unit, prepared)
            if (
                task.control_revision != action.metadata["control_revision"]
                or self._directive(run) != action.metadata["directive_revision"]
            ):
                return None
            return prepared
        action = unit.actions.get(approval.action_id)
        return PreparedChat.model_validate(action.metadata["prepared"])

    def check(
        self,
        unit: UnitOfWork,
        task: Task,
        run: Run,
        prepared: PreparedChat,
        decision: PolicyDecision,
    ) -> Approval | None:
        if decision.effect == "deny":
            return None
        approval = self._current(unit, task, run)
        if approval is None or approval.status != "approved":
            return None
        invalid = self.validity(unit, approval)
        if invalid:
            return None
        action = unit.actions.get(approval.action_id)
        if (
            action.metadata["snapshot_hash"] != _digest(prepared.model_dump(mode="json"))
            or action.metadata["rules_hash"] != decision.rules_hash
            or approval.decision not in ("allow_once", "allow_rule")
        ):
            return None
        return approval

    def consume(
        self,
        unit: UnitOfWork,
        task: Task,
        run: Run,
        prepared: PreparedChat,
        decision: PolicyDecision,
        *,
        call_id: UUID,
    ) -> Approval | None:
        approval = self.check(unit, task, run, prepared, decision)
        if approval is None:
            return None
        call = unit.model_calls.get(call_id)
        if (
            call is None
            or call.run_id != run.id
            or call.status != "prepared"
            or _digest(call.snapshot.get("prepared")) != _digest(prepared.model_dump(mode="json"))
            or call.task_control_revision != task.control_revision
        ):
            raise ApprovalError("approval_stale")
        stamp = self.clock()
        approval = unit.approvals.update(
            approval.model_copy(
                update={
                    "metadata": approval.metadata
                    | {
                        "consumed_at": stamp.isoformat(),
                        "model_call_id": str(call_id),
                    }
                }
            ),
            approval.revision,
        )
        action = unit.actions.get(approval.action_id)
        unit.actions.update(
            action.model_copy(
                update={
                    "status": "dispatch_started",
                    "metadata": action.metadata | {"model_call_id": str(call_id)},
                }
            ),
            action.revision,
        )
        return approval

    @staticmethod
    def _replay(approval: Approval, value: BaseModel, operation: str) -> bool:
        if value.client_request_id is None:
            return False
        previous = approval.metadata.get("human_requests", {}).get(str(value.client_request_id))
        if previous is None:
            return False
        if previous != _digest({"operation": operation, "input": value.model_dump(mode="json")}):
            raise ApprovalError("idempotency_conflict")
        return True

    @staticmethod
    def _receipt(approval: Approval, value: BaseModel, operation: str) -> dict:
        if value.client_request_id is None:
            return approval.metadata
        return approval.metadata | {
            "human_requests": {
                **approval.metadata.get("human_requests", {}),
                str(value.client_request_id): _digest(
                    {"operation": operation, "input": value.model_dump(mode="json")}
                ),
            }
        }

    def decide(
        self,
        agent_id: UUID | str,
        approval_id: UUID | str,
        value: ApprovalDecisionInput | dict,
    ) -> Approval:
        value = ApprovalDecisionInput.model_validate(value)
        with self.store.transaction(actor="user", source="approval_user") as unit:
            approval, action, task, run = self._owned(unit, agent_id, approval_id)
            if self._replay(approval, value, "decide"):
                return approval
            if approval.revision != value.expected_revision:
                raise RevisionConflict("A aprovação mudou; releia antes de decidir.")
            if approval.status != "pending" or task.status != "waiting_approval":
                raise ApprovalError("invalid_approval_state")
            invalid = self.validity(unit, approval)
            if invalid:
                raise ApprovalError(invalid)
            stamp = self.clock()
            updated = approval.model_copy(
                update={
                    "decision": value.decision,
                    "reason": value.reason,
                    "decided_at": stamp,
                    "status": "approved"
                    if value.decision.startswith("allow")
                    else "denied"
                    if value.decision == "deny"
                    else "pending",
                    "metadata": self._receipt(approval, value, "decide"),
                }
            )
            if value.decision in ("allow_rule", "ask", "deny"):
                intent = self.providers.model_intent(
                    task.agent_id,
                    (prepared := PreparedChat.model_validate(action.metadata["prepared"])).config,
                    prepared.request,
                )
                scope = PolicyScope(
                    tool_name=intent.tool_name,
                    action=intent.action,
                    environment_id=intent.environment_id,
                    resource=intent.resource,
                    identity=intent.identity,
                    parameters={"model": intent.parameters["model"]},
                ).model_dump(mode="json")
                rule = Policy(
                    agent_id=task.agent_id,
                    name=value.rule_name or "Autorização humana de geração",
                    effect="allow" if value.decision == "allow_rule" else value.decision,
                    scope=scope,
                    source="user",
                    reason=value.reason,
                    metadata={
                        "approval_grant": {
                            "approval_id": str(approval.id),
                            "policy_revision": 1,
                            "scope_hash": _digest(scope),
                            "ask_rules": action.metadata["ask_rules"],
                        }
                    }
                    if value.decision == "allow_rule"
                    else {},
                )
                self.policies._checked_scope(scope)
                unit.policies.create(rule)
                updated = updated.model_copy(
                    update={
                        "policy_id": rule.id if value.decision == "allow_rule" else None,
                        "metadata": updated.metadata
                        | {
                            "decision_policy_id": str(rule.id),
                            **(
                                {
                                    "authorized_ask_rules": action.metadata["ask_rules"],
                                    "authorized_scope": scope,
                                }
                                if value.decision == "allow_rule"
                                else {}
                            ),
                        },
                    }
                )
            updated = updated.model_copy(
                update={
                    "metadata": updated.metadata
                    | {
                        "human_decisions": [
                            *approval.metadata.get("human_decisions", []),
                            {
                                "client_request_id": str(value.client_request_id),
                                "actor": "user",
                                "decision": value.decision,
                                "reason": value.reason,
                                "decided_at": stamp.isoformat(),
                                "policy_id": (
                                    updated.metadata.get("decision_policy_id")
                                    if value.decision != "allow_once"
                                    else None
                                ),
                            },
                        ],
                    }
                }
            )
            approval = unit.approvals.update(updated, approval.revision)
            if value.decision in ("allow_rule", "ask", "deny"):
                current = self.providers.model_policy(unit, prepared)
                if value.decision == "allow_rule" and not current.allowed:
                    raise ApprovalError("approval_stale")
                action = unit.actions.update(
                    action.model_copy(
                        update={
                            "metadata": action.metadata
                            | {
                                "rules_hash": current.rules_hash,
                                "ask_rules": [
                                    {"id": str(policy.id), "revision": policy.revision}
                                    for policy_id in current.deciding_policy_ids
                                    if (policy := unit.policies.get(policy_id)) is not None
                                ],
                            }
                        }
                    ),
                    action.revision,
                )
            next_status = (
                "queued"
                if approval.status == "approved"
                else ("paused" if approval.status == "denied" else "waiting_approval")
            )
            next_action = (
                "ready"
                if approval.status == "approved"
                else ("cancelled" if approval.status == "denied" else "awaiting_approval")
            )
            unit.actions.update(action.model_copy(update={"status": next_action}), action.revision)
            unit.tasks.update(
                task.model_copy(
                    update={
                        "status": next_status,
                        "desired_state": "running" if next_status == "queued" else "paused",
                    }
                ),
                task.revision,
            )
            unit.runs.update(
                run.model_copy(
                    update={
                        "status": next_status,
                        "error": "approval_denied" if next_status == "paused" else None,
                        "checkpoint": run.checkpoint
                        | {"progress": next_status, "attention_required": next_status != "queued"},
                    }
                ),
                run.revision,
            )
            return approval

    def revoke(
        self,
        agent_id: UUID | str,
        approval_id: UUID | str,
        value: ApprovalRevokeInput | dict,
    ) -> Approval:
        value = ApprovalRevokeInput.model_validate(value)
        with self.store.transaction(actor="user", source="approval_user") as unit:
            approval, action, task, run = self._owned(unit, agent_id, approval_id)
            if self._replay(approval, value, "revoke"):
                return approval
            if approval.revision != value.expected_revision:
                raise RevisionConflict("A aprovação mudou; releia antes de revogar.")
            if approval.status not in ("pending", "approved"):
                raise ApprovalError("invalid_approval_state")
            approval = unit.approvals.update(
                approval.model_copy(
                    update={
                        "status": "revoked",
                        "metadata": self._receipt(approval, value, "revoke"),
                    }
                ),
                approval.revision,
            )
            if approval.policy_id is not None:
                policy = unit.policies.get(approval.policy_id)
                if policy and policy.status == "active":
                    unit.policies.update(
                        policy.model_copy(update={"status": "revoked"}), policy.revision
                    )
            if not approval.metadata.get("consumed_at"):
                if action.status in ("prepared", "awaiting_approval", "ready"):
                    unit.actions.update(
                        action.model_copy(update={"status": "cancelled"}), action.revision
                    )
                runs = unit.runs.list(task_id=task.id, limit=1000)
                current_approval = self._current(unit, task, run)
                if (
                    task.status not in ("completed", "cancelled", "failed")
                    and runs
                    and runs[-1].id == run.id
                    and action.metadata.get("control_revision") == task.control_revision
                    and current_approval is not None
                    and current_approval.id == approval.id
                ):
                    unit.tasks.update(
                        task.model_copy(update={"status": "paused", "desired_state": "paused"}),
                        task.revision,
                    )
                    unit.runs.update(
                        run.model_copy(update={"status": "paused", "error": "approval_revoked"}),
                        run.revision,
                    )
            return approval

    def get(self, agent_id: UUID | str, approval_id: UUID | str) -> Approval:
        with self.store.transaction(write=False) as unit:
            return self._owned(unit, agent_id, approval_id)[0]

    def view(self, agent_id: UUID | str, approval: Approval) -> dict:
        """Projeção pública mínima, relida para evitar decisões com estado antigo."""
        with self.store.transaction(write=False) as unit:
            approval, action, task, run = self._owned(unit, agent_id, approval.id)
            intent = action.parameters
            parameters = {"model": intent.get("parameters", {}).get("model")}
            scope = {
                field: intent.get(field)
                for field in ("tool_name", "action", "environment_id", "resource", "identity")
            }
            scope["parameters"] = parameters
            stale = self.validity(unit, approval)
            return {
                "id": str(approval.id),
                "agent_id": str(task.agent_id),
                "task_id": str(task.id),
                "task_title": task.title,
                "task_objective": task.objective,
                "expected_result": task.expected_result,
                "conversation_id": str(task.conversation_id) if task.conversation_id else None,
                "status": approval.status,
                "decision": approval.decision,
                "revision": approval.revision,
                "created_at": approval.created_at.isoformat(),
                "decided_at": approval.decided_at.isoformat() if approval.decided_at else None,
                "expires_at": action.metadata.get("expires_at"),
                "consumed_at": approval.metadata.get("consumed_at"),
                "valid": stale is None,
                "stale_reason": stale,
                **{
                    field: intent.get(field)
                    for field in ("tool_name", "action", "environment_id", "resource", "identity")
                },
                "parameters": parameters,
                "scope": scope,
                "consequence": (
                    "Enviar o contexto desta tarefa ao modelo selecionado. "
                    "O provedor pode cobrar pelo uso. Salvar uma regra cria uma exceção "
                    "às regras Perguntar observadas somente neste destino, modelo, "
                    "ambiente e identidade; bloqueios prevalecem."
                ),
            }

    def list(
        self,
        agent_id: UUID | str,
        *,
        task_id: UUID | str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Approval]:
        _pagination(limit, offset)
        with self.store.transaction(write=False) as unit:
            ProviderService._agent(unit, agent_id)
            if task_id is not None:
                task = unit.tasks.get(task_id)
                if task is None or task.agent_id != UUID(str(agent_id)):
                    raise NotFoundError("Tarefa não encontrada nesta abelha.")
            relevant = []
            page_offset = 0
            while True:
                page = unit.approvals.list(limit=1000, offset=page_offset)
                for approval in page:
                    try:
                        _, _, task, _ = self._owned(unit, agent_id, approval.id)
                    except NotFoundError:
                        continue
                    if task_id is None or task.id == UUID(str(task_id)):
                        relevant.append(approval)
                if len(page) < 1000:
                    break
                page_offset += len(page)
            return sorted(
                relevant, key=lambda entry: (entry.created_at, str(entry.id)), reverse=True
            )[offset : offset + limit]

    def finish(self, unit: UnitOfWork, approval_id: UUID | str, status: str) -> None:
        approval = unit.approvals.get(approval_id)
        if approval is None or not approval.metadata.get("consumed_at"):
            return
        if status not in ("confirmed", "failed_no_effect", "outcome_unknown"):
            raise ValueError("Resultado de ação desconhecido.")
        action = unit.actions.get(approval.action_id)
        if action.status == status:
            return
        unit.actions.update(action.model_copy(update={"status": status}), action.revision)
