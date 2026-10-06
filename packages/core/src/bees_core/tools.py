"""Extensões declarativas e dispatcher local de executores explicitamente confiáveis.

Instalar um manifesto não importa Python, inicia processos ou concede autoridade.
O builtin inicial apenas transforma texto em memória. Conteúdo não é persistido
na intenção, snapshot, política, evento ou erro; somente o resultado privado.
"""

import hashlib
import json
import re
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from bees_core.models import Action, ExecutionClaim, PluginInstallation, ToolGrant, utc_now
from bees_core.policies import ActionIntent, PolicyService
from bees_core.storage.database import Database
from bees_core.storage.store import NotFoundError, RevisionConflict, StateStore, UnitOfWork


def digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":")
        ).encode()
    ).hexdigest()


class ToolError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)


class ManifestTool(Contract):
    tool_name: Literal["text.normalize"]
    version: Literal["1"]
    executor: Literal["builtin:text.normalize@1"]


class PluginManifest(Contract):
    manifest_version: Literal[1]
    plugin_key: str = Field(min_length=1, max_length=100, pattern=r"^[a-z][a-z0-9_.-]+$")
    name: str = Field(min_length=1, max_length=100)
    version: str = Field(pattern=r"^\d{1,4}\.\d{1,4}\.\d{1,4}$")
    tools: tuple[ManifestTool, ...] = Field(min_length=1, max_length=1)

    @field_validator("manifest_version", mode="before")
    @classmethod
    def strict_version(cls, value):
        if type(value) is not int:
            raise ValueError("Versão exige inteiro explícito.")
        return value

    @field_validator("name")
    @classmethod
    def useful_name(cls, value: str) -> str:
        if value != value.strip() or any(ord(c) < 32 for c in value):
            raise ValueError("Nome exige texto sem controles ou espaços externos.")
        return value


class PluginInstallInput(Contract):
    manifest: PluginManifest
    client_request_id: UUID


class PluginUpdate(Contract):
    expected_revision: int = Field(ge=1, strict=True)
    enabled: bool = Field(strict=True)


class ToolGrantInput(Contract):
    expected_revision: int = Field(ge=0, strict=True)
    enabled: bool = Field(strict=True)


class TextInput(Contract):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, hide_input_in_errors=True)
    text: str = Field(max_length=4096)
    operation: Literal["trim", "upper", "lower"]

    @field_validator("text")
    @classmethod
    def bounded_text(cls, value: str) -> str:
        if "\x00" in value or len(value.encode("utf-8")) > 8192:
            raise ValueError("Texto excede limites ou contém NUL.")
        return value


class TextOutput(Contract):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, hide_input_in_errors=True)
    text: str = Field(max_length=8192)


class ToolInvocationInput(Contract):
    plugin_id: UUID
    tool_name: Literal["text.normalize"]
    version: Literal["1"]
    arguments: TextInput
    client_request_id: UUID


CATALOG_MANIFEST = PluginManifest(
    manifest_version=1,
    plugin_key="bees.text",
    name="Texto local",
    version="1.0.0",
    tools=[
        ManifestTool(tool_name="text.normalize", version="1", executor="builtin:text.normalize@1")
    ],
)


def _execute_builtin(arguments: TextInput) -> dict:
    if arguments.operation == "trim":
        text = arguments.text.strip()
    elif arguments.operation == "upper":
        text = arguments.text.upper()
    else:
        text = arguments.text.lower()
    return {"text": text}


class ToolService:
    def __init__(self, database: Database, *, clock: Callable[[], datetime] = utc_now) -> None:
        self.store = StateStore(database)
        self.policies = PolicyService(database)
        self.clock = clock

    @staticmethod
    def _binding(unit, claim: ExecutionClaim, agent_id: UUID, run_id: UUID, *, now) -> dict:
        unit.execution.assert_claim(claim, now=now)
        task = unit.tasks.get(claim.task.id)
        if task.agent_id != agent_id or claim.run.id != run_id:
            raise NotFoundError("Invocação não pertence à abelha/execução.")
        return {
            "task_id": str(task.id),
            "agent_id": str(task.agent_id),
            "run_id": str(claim.run.id),
            "owner_id": str(claim.owner_id),
            "control_revision": claim.task.control_revision,
            "leases": [
                {
                    "resource_key": token.resource_key,
                    "owner_id": str(token.owner_id),
                    "run_id": str(token.run_id),
                    "generation": token.generation,
                }
                for token in sorted(claim.leases, key=lambda token: token.resource_key)
            ],
        }

    def _bound_claim(self, unit, action, claim, agent_id, *, now):
        binding = self._binding(unit, claim, agent_id, action.run_id, now=now)
        if action.execution_binding != binding or action.lease_generation != claim.generation:
            raise RevisionConflict("Ação pertence a outro dono/geração de execução.")

    @staticmethod
    def _available(unit, claim):
        task = unit.tasks.get(claim.task.id)
        if task.control_revision != claim.task.control_revision:
            raise ToolError("execution_unavailable", "Controle mudou desde a aquisição da tarefa.")
        if task.active_milliseconds >= task.max_active_seconds * 1000:
            raise ToolError("task_limit_reached", "Tempo ativo da tarefa esgotado.")
        if unit.actions.unresolved(task.id) or unit.model_calls.unresolved(task.id):
            raise ToolError("execution_unavailable", "Journal pendente exige aguardar/reconhecer.")

    @staticmethod
    def _obsolete(unit, action):
        run = unit.runs.get(action.run_id)
        task = unit.tasks.get(run.task_id)
        agent = unit.agents.get(task.agent_id)
        return (
            task.desired_state == "cancelled"
            or task.control_revision != action.metadata.get("task_control_revision")
            or agent.revision != action.metadata.get("agent_revision")
            or unit.runs.latest(task.id).id != run.id
        )

    @staticmethod
    def catalog() -> list[dict]:
        # Projeção nova em cada consulta: consumidores não mutam o registro confiável.
        return [CATALOG_MANIFEST.model_dump(mode="json")]

    @staticmethod
    def _manifest(plugin: PluginInstallation) -> PluginManifest:
        try:
            value = PluginManifest.model_validate(plugin.manifest)
            if digest(value.model_dump(mode="json")) != plugin.manifest_hash:
                raise ValueError()
            return value
        except ValidationError, ValueError, TypeError:
            raise ToolError(
                "invalid_manifest", "Manifesto não corresponde ao contrato instalado."
            ) from None

    @staticmethod
    def plugin_view(plugin: PluginInstallation) -> dict:
        return plugin.model_dump(mode="json", exclude={"metadata"})

    def install(self, value: PluginInstallInput | dict) -> PluginInstallation:
        try:
            value = PluginInstallInput.model_validate(value)
        except ValidationError, ValueError, TypeError:
            raise ToolError(
                "invalid_manifest", "Manifesto incompatível com executores locais disponíveis."
            ) from None
        manifest = value.manifest.model_dump(mode="json")
        manifest_hash = digest(manifest)
        with self.store.transaction(actor="user", source="plugin_user") as unit:
            previous = unit.plugins.get(value.client_request_id)
            if previous is not None:
                if previous.manifest_hash != manifest_hash:
                    raise ToolError(
                        "idempotency_conflict", "Identificador já utilizado com outro manifesto."
                    )
                return previous
            return unit.plugins.create(
                PluginInstallation(
                    id=value.client_request_id, manifest=manifest, manifest_hash=manifest_hash
                )
            )

    def list_plugins(self, *, limit: int = 100, offset: int = 0) -> list[PluginInstallation]:
        with self.store.transaction(write=False) as unit:
            return unit.plugins.list(limit=limit, offset=offset)

    def update_plugin(self, plugin_id: UUID, value: PluginUpdate | dict) -> PluginInstallation:
        value = PluginUpdate.model_validate(value)
        with self.store.transaction(actor="user", source="plugin_user") as unit:
            plugin = unit.plugins.get(plugin_id)
            if plugin is None:
                raise NotFoundError("Extensão não encontrada.")
            self._manifest(plugin)
            return unit.plugins.update(
                plugin.model_copy(update={"enabled": value.enabled}), value.expected_revision
            )

    def set_grant(
        self, agent_id: UUID, plugin_id: UUID, tool_name: str, value: ToolGrantInput | dict
    ) -> ToolGrant:
        value = ToolGrantInput.model_validate(value)
        with self.store.transaction(actor="user", source="tool_grant_user") as unit:
            agent = unit.agents.get(agent_id)
            plugin = unit.plugins.get(plugin_id)
            if agent is None or plugin is None:
                raise NotFoundError("Abelha ou extensão não encontrada.")
            if tool_name not in {tool.tool_name for tool in self._manifest(plugin).tools}:
                raise ToolError("unknown_tool", "Ferramenta não pertence ao manifesto instalado.")
            grant = unit.tool_grants.find(agent_id, plugin_id, tool_name)
            if grant is None:
                if value.expected_revision != 0:
                    raise RevisionConflict("Concessão inexistente exige revisão zero.")
                return unit.tool_grants.create(
                    ToolGrant(
                        agent_id=agent_id,
                        plugin_id=plugin_id,
                        tool_name=tool_name,
                        enabled=value.enabled,
                    )
                )
            return unit.tool_grants.update(
                grant.model_copy(update={"enabled": value.enabled}), value.expected_revision
            )

    def list_tools(self, agent_id: UUID, *, limit: int = 100, offset: int = 0) -> list[dict]:
        with self.store.transaction(write=False) as unit:
            agent = unit.agents.get(agent_id)
            if agent is None:
                raise NotFoundError("Abelha não encontrada.")
            rows = []
            for plugin in unit.plugins.list(limit=limit, offset=offset):
                manifest = self._manifest(plugin)
                for tool in manifest.tools:
                    grant = unit.tool_grants.find(agent_id, plugin.id, tool.tool_name)
                    rows.append(
                        {
                            "plugin_id": str(plugin.id),
                            "plugin_name": manifest.name,
                            "plugin_version": manifest.version,
                            "plugin_enabled": plugin.enabled,
                            "plugin_revision": plugin.revision,
                            "tool_name": tool.tool_name,
                            "version": tool.version,
                            "input_schema": TextInput.model_json_schema(),
                            "output_schema": TextOutput.model_json_schema(),
                            "capabilities": ["pure_text"],
                            "grant_id": str(grant.id) if grant else None,
                            "grant_revision": grant.revision if grant else 0,
                            "granted": bool(grant and grant.enabled),
                            "available": bool(
                                plugin.enabled
                                and grant
                                and grant.enabled
                                and agent.status == "active"
                            ),
                        }
                    )
            return rows

    def _authority(
        self,
        unit: UnitOfWork,
        agent_id: UUID,
        run_id: UUID,
        plugin_id: UUID,
        tool_name: str,
        arguments: TextInput,
    ) -> tuple[ActionIntent, dict]:
        agent = unit.agents.get(agent_id)
        run = unit.runs.get(run_id)
        task = unit.tasks.get(run.task_id) if run else None
        if agent is None or run is None or task is None or task.agent_id != agent_id:
            raise NotFoundError("Execução não encontrada nesta abelha.")
        latest = unit.runs.latest(task.id)
        if (
            agent.status != "active"
            or task.desired_state != "running"
            or task.status != "running"
            or run.status != "running"
            or latest is None
            or latest.id != run.id
        ):
            raise ToolError(
                "execution_unavailable", "Execução não está disponível para uma nova ação."
            )
        plugin = unit.plugins.get(plugin_id)
        if plugin is None:
            raise ToolError("unknown_tool", "Extensão não instalada.")
        manifest = self._manifest(plugin)
        if not plugin.enabled:
            raise ToolError("tool_disabled", "Extensão desabilitada.")
        if tool_name not in {tool.tool_name for tool in manifest.tools}:
            raise ToolError("unknown_tool", "Ferramenta não registrada nesta extensão.")
        grant = unit.tool_grants.find(agent_id, plugin_id, tool_name)
        if grant is None or not grant.enabled:
            raise ToolError("tool_not_granted", "Uso não concedido para esta abelha.")
        intent = ActionIntent(
            agent_id=agent_id,
            tool_name=tool_name,
            action="transform",
            environment_id="control_plane",
            resource=f"plugin:{plugin.id}",
            identity="bees_user",
            parameters={
                "operation": arguments.operation,
                "input_hash": digest(arguments.model_dump(mode="json")),
            },
        )
        snapshot = {
            "contract_version": 1,
            "plugin_id": str(plugin.id),
            "plugin_revision": plugin.revision,
            "manifest_hash": plugin.manifest_hash,
            "grant_id": str(grant.id),
            "grant_revision": grant.revision,
            "agent_revision": agent.revision,
            "task_control_revision": task.control_revision,
            "run_id": str(run.id),
            "tool_version": "1",
            "intent_hash": digest(intent.model_dump(mode="json")),
        }
        return intent, snapshot

    def prepare(
        self,
        agent_id: UUID,
        run_id: UUID,
        value: ToolInvocationInput | dict,
        *,
        claim: ExecutionClaim,
    ) -> Action:
        try:
            value = ToolInvocationInput.model_validate(value)
        except ValidationError, ValueError, TypeError:
            raise ToolError(
                "invalid_tool_input", "Entrada incompatível com o contrato da ferramenta."
            ) from None
        with self.store.transaction(
            actor="executor", source="tool_executor", correlation_id=str(value.client_request_id)
        ) as unit:
            previous = unit.actions.get(value.client_request_id)
            if previous is not None:
                # Replay não altera intenção nem executa novamente, inclusive após revogação.
                if (
                    previous.run_id != run_id
                    or previous.metadata.get("plugin_id") != str(value.plugin_id)
                    or previous.tool_name != value.tool_name
                    or previous.parameters.get("input_hash")
                    != digest(value.arguments.model_dump(mode="json"))
                ):
                    raise ToolError(
                        "idempotency_conflict", "Identificador já utilizado com outra intenção."
                    )
                owner = unit.tasks.get(unit.runs.get(previous.run_id).task_id)
                if owner.agent_id != agent_id:
                    raise NotFoundError("Ação não encontrada nesta abelha.")
                return previous
            binding = self._binding(unit, claim, agent_id, run_id, now=self.clock())
            self._available(unit, claim)
            intent, snapshot = self._authority(
                unit, agent_id, run_id, value.plugin_id, value.tool_name, value.arguments
            )
            decision = self.policies.evaluate(unit, intent)
            if not decision.allowed:
                raise ToolError(
                    "tool_policy_ask" if decision.effect == "ask" else "tool_policy_denied",
                    "Política atual exige revisão humana antes desta ação.",
                )
            action = unit.actions.create(
                Action(
                    id=value.client_request_id,
                    run_id=run_id,
                    tool_name=value.tool_name,
                    parameters=intent.parameters,
                    idempotency_key=f"tool:{value.client_request_id}",
                    execution_binding=binding,
                    lease_generation=claim.generation,
                    metadata={
                        **snapshot,
                        "intent": intent.model_dump(mode="json"),
                        "rules_hash": decision.rules_hash,
                    },
                )
            )
            return unit.actions.update(
                action.model_copy(update={"status": "ready"}), action.revision
            )

    @staticmethod
    def _owned_action(unit: UnitOfWork, agent_id: UUID, action_id: UUID) -> Action:
        action = unit.actions.get(action_id)
        run = unit.runs.get(action.run_id) if action else None
        task = unit.tasks.get(run.task_id) if run else None
        if (
            action is None
            or task is None
            or task.agent_id != agent_id
            or "contract_version" not in action.metadata
        ):
            raise NotFoundError("Invocação não encontrada nesta abelha.")
        return action

    def get_invocation(self, agent_id: UUID, action_id: UUID) -> Action:
        with self.store.transaction(write=False) as unit:
            return self._owned_action(unit, agent_id, action_id)

    def mark_unknown(
        self,
        agent_id: UUID,
        action_id: UUID,
        *,
        expected_revision: int,
        evidence_ref: str,
        owner_id: UUID,
    ) -> Action:
        """Recuperação conservadora pelo supervisor após interromper o dono antigo.

        Não prova ausência de efeito e não libera repetição; nunca chama executor.
        """
        if not isinstance(evidence_ref, str) or not re.fullmatch(
            r"[a-zA-Z0-9:_-]{1,120}", evidence_ref
        ):
            raise ValueError("Recuperação exige referência opaca à evidência de interrupção.")
        with self.store.transaction(
            actor="supervisor", source="tool_recovery", correlation_id=str(action_id)
        ) as unit:
            action = self._owned_action(unit, agent_id, action_id)
            binding = action.execution_binding
            if binding is None or binding.get("owner_id") != str(owner_id):
                raise RevisionConflict("Supervisor deve identificar o dono do despacho.")
            if type(expected_revision) is not int or expected_revision < 1:
                raise ValueError("Revisão deve ser inteiro positivo.")
            if action.revision != expected_revision:
                raise RevisionConflict("Journal mudou antes da recuperação.")
            if action.status == "outcome_unknown":
                return action
            if action.status != "dispatch_started":
                raise ToolError(
                    "invocation_unavailable",
                    "Somente despacho sem resultado pode ser marcado desconhecido.",
                )
            now = self.clock()
            for token in binding["leases"]:
                lease = unit.execution._lease(token["resource_key"])
                if (
                    lease
                    and lease[:3] == (token["owner_id"], token["run_id"], token["generation"])
                    and lease[3] > now.astimezone(UTC).isoformat(timespec="microseconds")
                ):
                    raise ToolError(
                        "owner_still_active", "Dono ainda possui lease; interrompa e libere."
                    )
            action = unit.actions.update(
                action.model_copy(
                    update={
                        "status": "outcome_unknown",
                        "metadata": {
                            **action.metadata,
                            "error_code": "tool_interrupted_unknown",
                            "recovery_evidence_ref": evidence_ref,
                        },
                    }
                ),
                expected_revision,
            )
            unit.execution.quarantine_action(action, now=now)
            return action

    def execute(
        self,
        agent_id: UUID,
        action_id: UUID,
        arguments: TextInput | dict,
        *,
        expected_revision: int,
        claim: ExecutionClaim,
    ) -> Action:
        try:
            arguments = TextInput.model_validate(arguments)
        except ValidationError, ValueError, TypeError:
            raise ToolError(
                "invalid_tool_input", "Entrada incompatível com o contrato da ferramenta."
            ) from None
        if type(expected_revision) is not int or expected_revision < 1:
            raise ValueError("Revisão deve ser inteiro positivo.")
        with self.store.transaction(
            actor="executor", source="tool_executor", correlation_id=str(action_id)
        ) as unit:
            action = self._owned_action(unit, agent_id, action_id)
            if action.parameters.get("input_hash") != digest(arguments.model_dump(mode="json")):
                raise ToolError("invocation_changed", "Parâmetros diferem da intenção preparada.")
            if action.status in (
                "confirmed",
                "failed_no_effect",
                "outcome_unknown",
                "dispatch_started",
            ):
                # dispatch_started pode ter efeito em voo: consulta somente, nunca retry.
                return action
            if action.revision != expected_revision:
                raise RevisionConflict("Invocação alterada; releia seu estado.")
            if action.status != "ready":
                raise ToolError("invocation_unavailable", "Invocação não disponível para despacho.")
            self._bound_claim(unit, action, claim, agent_id, now=self.clock())
            self._available(unit, claim)
            try:
                intent, snapshot = self._authority(
                    unit,
                    agent_id,
                    action.run_id,
                    UUID(action.metadata["plugin_id"]),
                    action.tool_name,
                    arguments,
                )
            except KeyError, ValueError, TypeError:
                raise ToolError(
                    "invocation_changed", "Snapshot inválido; prepare uma nova ação."
                ) from None
            if any(
                action.metadata.get(name) != value for name, value in snapshot.items()
            ) or action.metadata.get("intent") != intent.model_dump(mode="json"):
                raise ToolError(
                    "invocation_changed", "Autoridade mudou desde o preparo; prepare uma nova ação."
                )
            decision = self.policies.evaluate(unit, intent)
            if not decision.allowed:
                raise ToolError(
                    "tool_policy_ask" if decision.effect == "ask" else "tool_policy_denied",
                    "Política atual impede despacho sem revisão humana.",
                )
            action = unit.actions.update(
                action.model_copy(
                    update={
                        "status": "dispatch_started",
                        "metadata": {**action.metadata, "dispatch_rules_hash": decision.rules_hash},
                    }
                ),
                action.revision,
            )
        # O efeito ocorre fora da transação; exceções nunca expõem texto/segredos.
        try:
            result = TextOutput.model_validate(_execute_builtin(arguments)).model_dump(mode="json")
        except Exception:
            with self.store.transaction(
                actor="executor", source="tool_executor", correlation_id=str(action_id)
            ) as unit:
                current = self._owned_action(unit, agent_id, action_id)
                try:
                    self._bound_claim(unit, current, claim, agent_id, now=self.clock())
                except RevisionConflict:
                    raise RevisionConflict("Dono antigo não pode concluir a ação.") from None
                current = unit.actions.update(
                    current.model_copy(
                        update={
                            "status": "outcome_unknown",
                            "metadata": {
                                **current.metadata,
                                "error_code": "tool_result_unknown",
                                "obsolete": self._obsolete(unit, current),
                            },
                        }
                    ),
                    current.revision,
                )
                unit.execution.quarantine_action(current, now=self.clock())
                return current
        with self.store.transaction(
            actor="executor", source="tool_executor", correlation_id=str(action_id)
        ) as unit:
            current = self._owned_action(unit, agent_id, action_id)
            self._bound_claim(unit, current, claim, agent_id, now=self.clock())
            return unit.actions.update(
                current.model_copy(
                    update={
                        "status": "confirmed",
                        "result": result,
                        "metadata": current.metadata | {"obsolete": self._obsolete(unit, current)},
                    }
                ),
                current.revision,
            )
