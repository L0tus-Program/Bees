"""Guarda online do recurso com aprovação própria e consumo junto do begin."""

from bees_core.approvals import ApprovalError
from bees_core.tools import ToolError

from .authority import CoreToolAuthority
from .contracts import MAX_SECONDS, TransportError


class ResourceToolAuthority(CoreToolAuthority):
    def __init__(self, resources, agent_id, action_id, *, expected_revision, claim):
        if resources.target is None:
            raise TransportError("executor_binding_mismatch")
        self.resources = resources
        with resources.store.transaction(write=False) as unit:
            action = resources.tools._owned_action(unit, agent_id, action_id)
            root, _, _ = resources._owned_intent(
                unit, agent_id, action.metadata["resource_tool_intent_id"]
            )
            arguments = root.metadata["snapshot"]["arguments"]
        super().__init__(
            resources.tools,
            resources.target,
            agent_id,
            action_id,
            arguments,
            expected_revision=expected_revision,
            claim=claim,
        )

    def _online(self, unit, action, *, available):
        self._installation(unit)
        try:
            _, _, decision = self.resources.online(
                unit,
                self.agent_id,
                action,
                self.arguments,
                self.identity,
                self.claim,
                available=available,
            )
        except ApprovalError as error:
            raise ToolError(error.code, "Decisão de ferramenta ausente ou obsoleta.") from None
        return unit.safety.state(), decision

    def _maximum_seconds(self, unit, action):
        task = unit.tasks.get(self.claim.task.id)
        return min(MAX_SECONDS, self.resources._budget(task)[2] / 1000)

    def begin(self, request):
        with self.service.store.transaction(actor="executor", source="resource_tool_begin") as unit:
            action = self._owned(unit)
            self._request(action, request)
            if action.status != "ready":
                raise ToolError("invocation_unavailable", "Intenção iniciada; não repetir.")
            safety, _ = self._online(unit, action, available=True)
            if safety.generation != request["safety_generation"]:
                raise ToolError("global_stop", "Geração mudou desde o preparo.")
            try:
                decision = self.resources.consume(
                    unit, self.agent_id, action, self.arguments, self.identity, self.claim, request
                )
            except ApprovalError as error:
                raise ToolError(error.code, "Decisão de ferramenta ausente ou obsoleta.") from None
            return unit.actions.update(
                action.model_copy(
                    update={
                        "status": "dispatch_started",
                        "metadata": action.metadata | {"dispatch_rules_hash": decision.rules_hash},
                    }
                ),
                action.revision,
            )
