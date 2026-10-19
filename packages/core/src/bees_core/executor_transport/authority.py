"""Autoridade online concreta para o adaptador explícito de texto próprio.

Somente o pai importa domínio/APSW. O filho recebe IDs/hash e autorização de um
único GO após o commit canônico; nunca recebe conexão/caminho do banco ou modelos.
"""

from datetime import timedelta
from uuid import UUID, uuid4

from bees_core.models import Action, ExecutionClaim
from bees_core.security.hosts import HostService
from bees_core.storage.store import RevisionConflict
from bees_core.tools import TextInput, TextOutput, ToolError, ToolService

from .contracts import MAX_SECONDS, ResourceIdentity, TransportError, digest, envelope


class CoreToolAuthority:
    def __init__(
        self,
        service: ToolService,
        identity: ResourceIdentity,
        agent_id: UUID,
        action_id: UUID,
        arguments: TextInput | dict,
        *,
        expected_revision: int,
        claim: ExecutionClaim,
    ):
        if type(expected_revision) is not int or expected_revision < 1:
            raise ValueError("Revisão deve ser inteiro positivo.")
        self.service = service
        self.identity = identity
        self.agent_id = agent_id
        self.action_id = action_id
        self.arguments = TextInput.model_validate(arguments).model_dump(mode="json")
        self.expected_revision = expected_revision
        self.claim = claim

    def _installation(self, unit):
        if (
            HostService._installation(unit.agents._context.connection)
            != self.identity.installation_id
            or str(self.agent_id) != self.identity.agent_id
        ):
            raise TransportError("executor_binding_mismatch")

    def _owned(self, unit):
        action = self.service._owned_action(unit, self.agent_id, self.action_id)
        if action.parameters.get("input_hash") != digest(self.arguments):
            raise ToolError("invocation_changed", "Parâmetros diferem da intenção preparada.")
        return action

    def _online(self, unit, action, *, available):
        self._installation(unit)
        self.service._bound_claim(unit, action, self.claim, self.agent_id, now=self.service.clock())
        if available:
            self.service._available(unit, self.claim)
        intent, snapshot = self.service._authority(
            unit,
            self.agent_id,
            action.run_id,
            UUID(action.metadata["plugin_id"]),
            action.tool_name,
            TextInput.model_validate(self.arguments),
        )
        if any(
            action.metadata.get(key) != value for key, value in snapshot.items()
        ) or action.metadata.get("intent") != intent.model_dump(mode="json"):
            raise ToolError("invocation_changed", "Autoridade mudou; prepare uma nova ação.")
        decision = self.service.policies.evaluate(unit, intent)
        if not decision.allowed:
            raise ToolError(
                "tool_policy_ask" if decision.effect == "ask" else "tool_policy_denied",
                "Política atual impede o despacho.",
            )
        safety = unit.safety.state()
        if safety.status != "running":
            raise ToolError("global_stop", "Bees parado: nenhuma ação nova é despachada.")
        return safety, decision

    def seal(self) -> tuple[Action, dict | None]:
        """Persiste UUID/alvo/prazo/fencing antes de abrir o canal do recurso.

        Metadados contêm hashes/IDs, nunca argumentos ou credencial bx_. Um alvo
        já selado é imutável por esta interface; replay não escolhe outra máquina.
        """
        with self.service.store.transaction(actor="executor", source="executor_transport") as unit:
            action = self._owned(unit)
            self._installation(unit)
            if action.status in (
                "confirmed",
                "outcome_unknown",
                "dispatch_started",
                "failed_no_effect",
                "cancelled",
            ):
                return action, None
            if action.status != "ready":
                raise ToolError("invocation_unavailable", "Ação não está pronta.")
            if action.revision != self.expected_revision:
                raise RevisionConflict("Invocação mudou; releia o registro.")
            safety, _ = self._online(unit, action, available=True)
            prior = action.metadata.get("executor_transport")
            if prior is not None:
                request = envelope(prior, self.identity)
                self._request(action, request)
                return action, request
            now = self.service.clock()
            expires = min(
                now + timedelta(seconds=self._maximum_seconds(unit, action)),
                *(t.expires_at for t in self.claim.leases),
            )
            request = {
                "protocol": 1,
                **self.identity.fields(),
                "action_id": str(action.id),
                "request_id": str(uuid4()),
                "agent_id": str(self.agent_id),
                "run_id": str(action.run_id),
                "owner_id": str(self.claim.owner_id),
                "generation": self.claim.generation,
                "control_revision": self.claim.task.control_revision,
                "safety_generation": safety.generation,
                "binding_hash": digest(action.execution_binding),
                "input_hash": action.parameters["input_hash"],
                "tool_name": "text.normalize",
                "tool_version": "1",
                "deadline_ms": int(expires.timestamp() * 1000),
            }
            envelope(request, self.identity)
            action = unit.actions.update(
                action.model_copy(
                    update={"metadata": action.metadata | {"executor_transport": request}}
                ),
                action.revision,
            )
            return action, request

    def _maximum_seconds(self, unit, action):
        return MAX_SECONDS

    def _request(self, action, request):
        envelope(request, self.identity)
        if (
            action.metadata.get("executor_transport") != request
            or request["action_id"] != str(action.id)
            or request["agent_id"] != str(self.agent_id)
            or request["run_id"] != str(action.run_id)
            or request["owner_id"] != str(self.claim.owner_id)
            or request["binding_hash"] != digest(action.execution_binding)
            or request["input_hash"] != digest(self.arguments)
            or request["generation"] != self.claim.generation
            or request["control_revision"] != self.claim.task.control_revision
        ):
            raise TransportError("executor_binding_mismatch")

    def begin(self, request):
        """Guarda online real e commit do início antes do único GO privado."""
        with self.service.store.transaction(actor="executor", source="executor_transport") as unit:
            action = self._owned(unit)
            self._request(action, request)
            if action.status != "ready":
                raise ToolError("invocation_unavailable", "Intenção já iniciada; não repetir.")
            safety, decision = self._online(unit, action, available=True)
            if safety.generation != request["safety_generation"]:
                raise ToolError("global_stop", "Geração da parada mudou desde o preparo.")
            if self.service.clock().timestamp() * 1000 >= request["deadline_ms"]:
                raise TransportError("executor_deadline_invalid")
            return unit.actions.update(
                action.model_copy(
                    update={
                        "status": "dispatch_started",
                        "metadata": action.metadata | {"dispatch_rules_hash": decision.rules_hash},
                    }
                ),
                action.revision,
            )

    def _unknown(self, unit, action, code):
        if action.status != "dispatch_started":
            return action
        updated = unit.actions.update(
            action.model_copy(
                update={
                    "status": "outcome_unknown",
                    "metadata": action.metadata | {"error_code": code},
                }
            ),
            action.revision,
        )
        unit.execution.quarantine_action(updated, now=self.service.clock())
        return updated

    def unknown(self, request):
        """Supervisor conserva o início do dono original; não publica seu resultado."""
        with self.service.store.transaction(
            actor="supervisor", source="executor_transport"
        ) as unit:
            action = self._owned(unit)
            self._request(action, request)
            return self._unknown(unit, action, "executor_result_unknown")

    def complete(self, request, output):
        observed = TextOutput.model_validate(output).model_dump(mode="json")
        with self.service.store.transaction(actor="executor", source="executor_transport") as unit:
            action = self._owned(unit)
            self._request(action, request)
            if action.status != "dispatch_started":
                return action
            try:
                safety, _ = self._online(unit, action, available=False)
                if safety.generation != request["safety_generation"]:
                    raise TransportError("executor_fenced")
            except RevisionConflict, ToolError, TransportError:
                # O recibo permanece privado no recurso. Dono/fence/autoridade antigos
                # nunca publicam confirmação canônica nem autorizam outro efeito.
                return self._unknown(unit, action, "executor_publication_fenced")
            return unit.actions.update(
                action.model_copy(
                    update={
                        "status": "confirmed",
                        "result": observed,
                        "metadata": action.metadata | {"executor_receipt_hash": digest(observed)},
                    }
                ),
                action.revision,
            )
