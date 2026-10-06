"""Políticas declaradas pelo usuário, avaliadas no despacho com escopo exato.

Este serviço só pode ser exposto por uma aplicação autenticada. Textos, memórias,
argumentos de ferramentas e respostas do modelo não são canais de alteração de
regras. O registro de ações é criado por código confiável, nunca pelo modelo.
"""

import hashlib
import json
import math
from dataclasses import dataclass
from typing import Literal, Self
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    TypeAdapter,
    ValidationError,
    field_validator,
    model_validator,
)

from bees_core.models import Policy
from bees_core.storage.database import Database
from bees_core.storage.store import NotFoundError, StateStore, UnitOfWork, _pagination

PolicyEffect = Literal["allow", "ask", "deny"]


class PolicyError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _canonical(value: object) -> str:
    return json.dumps(
        value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")
    )


def _exact_text(value: str | None) -> str | None:
    if value is not None and (
        not value or value != value.strip() or any(ord(character) < 32 for character in value)
    ):
        raise ValueError("Escopo exige texto explícito sem espaços externos ou controles.")
    return value


def _small_parameters(value: dict[str, JsonValue]) -> dict[str, JsonValue]:
    """Limites explícitos evitam documentos profundos e igualdade não determinística."""
    count = 0

    def visit(item: JsonValue, depth: int) -> None:
        nonlocal count
        count += 1
        if depth > 12 or count > 512:
            raise ValueError("Parâmetros excedem os limites de estrutura.")
        if isinstance(item, float) and not math.isfinite(item):
            raise ValueError("Parâmetros exigem números finitos.")
        if isinstance(item, str) and (len(item) > 4096 or "\x00" in item):
            raise ValueError("Parâmetro textual inválido.")
        if isinstance(item, dict):
            if len(item) > 64:
                raise ValueError("Parâmetros excedem o limite de campos.")
            for key, nested in item.items():
                if len(key) > 128 or _exact_text(key) is None:
                    raise ValueError("Nome de parâmetro inválido.")
                visit(nested, depth + 1)
        elif isinstance(item, list):
            for nested in item:
                visit(nested, depth + 1)

    visit(value, 0)
    if len(_canonical(value).encode("utf-8")) > 8192:
        raise ValueError("Parâmetros excedem 8 KiB.")
    return value


class PolicyScope(BaseModel):
    """Campos ausentes abrangem qualquer valor; campos presentes têm igualdade exata.

    Não há normalização de caminhos, regex, prefixos ou padrões de wildcard.
    Uma restrição em parameters compara cada valor completo, incluindo seu tipo.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    tool_name: str | None = Field(default=None, min_length=1, max_length=200)
    action: str | None = Field(default=None, min_length=1, max_length=200)
    environment_id: str | None = Field(default=None, min_length=1, max_length=200)
    resource: str | None = Field(default=None, min_length=1, max_length=2048)
    identity: str | None = Field(default=None, min_length=1, max_length=200)
    parameters: dict[str, JsonValue] = Field(default_factory=dict)

    @field_validator("tool_name", "action", "environment_id", "resource", "identity")
    @classmethod
    def exact_text(cls, value: str | None) -> str | None:
        return _exact_text(value)

    @field_validator("parameters")
    @classmethod
    def small_parameters(cls, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        return _small_parameters(value)


class ActionIntent(PolicyScope):
    """Autoridade definida pelo executor, separada dos argumentos da ação."""

    agent_id: UUID
    tool_name: str = Field(min_length=1, max_length=200)
    action: str = Field(min_length=1, max_length=200)
    environment_id: str = Field(min_length=1, max_length=200)


class PolicyInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    name: str = Field(min_length=1, max_length=200)
    effect: PolicyEffect
    scope: PolicyScope = Field(default_factory=PolicyScope)
    reason: str = Field(default="", max_length=2000)

    @field_validator("name")
    @classmethod
    def useful_name(cls, value: str) -> str:
        return _exact_text(value)

    @field_validator("reason")
    @classmethod
    def useful_reason(cls, value: str) -> str:
        if "\x00" in value:
            raise ValueError("Motivo inválido.")
        return value


class PolicyUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    expected_revision: int = Field(ge=1, strict=True)
    name: str | None = Field(default=None, min_length=1, max_length=200)
    effect: PolicyEffect | None = None
    scope: PolicyScope | None = None
    reason: str | None = Field(default=None, max_length=2000)
    status: Literal["active", "revoked"] | None = None

    @model_validator(mode="after")
    def useful_changes(self) -> Self:
        fields = self.model_fields_set - {"expected_revision"}
        if not fields or any(getattr(self, name) is None for name in fields):
            raise ValueError("Alteração exige ao menos um campo válido, sem valores nulos.")
        if "name" in fields:
            _exact_text(self.name)
        if self.reason is not None and "\x00" in self.reason:
            raise ValueError("Motivo inválido.")
        return self


class ModelGenerateParameters(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    model: str = Field(min_length=1, max_length=200)
    request_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")

    @field_validator("model")
    @classmethod
    def exact_model(cls, value: str) -> str:
        return _exact_text(value)


@dataclass(frozen=True)
class ToolActionDescriptor:
    """Contrato confiável, não uma extensão carregada de conteúdo externo."""

    tool_name: str
    action: str
    parameters_model: type[BaseModel]
    default_effect: PolicyEffect = "ask"
    environment_id: str | None = None
    requires_resource: bool = False
    requires_identity: bool = False
    version: str = "1"

    def __post_init__(self) -> None:
        for value in (self.tool_name, self.action, self.version):
            if _exact_text(value) is None:
                raise ValueError("Descritor exige identificadores explícitos.")
        _exact_text(self.environment_id)
        if self.default_effect not in ("allow", "ask", "deny"):
            raise ValueError("Efeito padrão desconhecido.")
        if not issubclass(self.parameters_model, BaseModel):
            raise ValueError("Descritor exige modelo de parâmetros tipado.")
        if self.parameters_model.model_config.get("extra") != "forbid":
            raise ValueError("Contrato de parâmetros deve rejeitar campos desconhecidos.")

    def validate(self, intent: ActionIntent) -> dict[str, JsonValue]:
        if (
            (self.environment_id is not None and self.environment_id != intent.environment_id)
            or (self.requires_resource and intent.resource is None)
            or (self.requires_identity and intent.identity is None)
        ):
            raise ValueError("Autoridade não corresponde ao contrato da ação.")
        parameters = self.parameters_model.model_validate(intent.parameters, strict=True)
        validated = parameters.model_dump(mode="json", exclude_unset=True)
        _small_parameters(validated)
        # Rejeitar coerção/normalização: o despacho deve usar os mesmos valores avaliados.
        if _canonical(validated) != _canonical(intent.parameters):
            raise ValueError("Parâmetros concretos diferem do contrato validado.")
        return validated

    def snapshot(self) -> dict:
        return {
            "tool_name": self.tool_name,
            "action": self.action,
            "default_effect": self.default_effect,
            "environment_id": self.environment_id,
            "requires_resource": self.requires_resource,
            "requires_identity": self.requires_identity,
            "version": self.version,
            "parameters_schema": self.parameters_model.model_json_schema(),
        }


@dataclass(frozen=True)
class ToolRegistry:
    descriptors: tuple[ToolActionDescriptor, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "descriptors", tuple(self.descriptors))
        keys = [(descriptor.tool_name, descriptor.action) for descriptor in self.descriptors]
        if len(keys) != len(set(keys)):
            raise ValueError("Ação duplicada no registro confiável.")

    def get(self, tool_name: str, action: str) -> ToolActionDescriptor | None:
        return next(
            (
                descriptor
                for descriptor in self.descriptors
                if (descriptor.tool_name, descriptor.action) == (tool_name, action)
            ),
            None,
        )


DEFAULT_REGISTRY = ToolRegistry(
    (
        ToolActionDescriptor(
            tool_name="model",
            action="generate",
            parameters_model=ModelGenerateParameters,
            default_effect="allow",
            environment_id="control_plane",
            requires_resource=True,
        ),
    )
)


class PolicyDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    effect: PolicyEffect
    reason_code: Literal[
        "default",
        "matched_rule",
        "unknown_action",
        "invalid_parameters",
        "invalid_policy",
        "agent_unavailable",
    ]
    matched_policy_ids: tuple[UUID, ...] = ()
    deciding_policy_ids: tuple[UUID, ...] = ()
    rules_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    reasons: tuple[str, ...] = ()

    @property
    def allowed(self) -> bool:
        return self.effect == "allow"


def _matches(scope: PolicyScope, intent: ActionIntent) -> bool:
    for name in ("tool_name", "action", "environment_id", "resource", "identity"):
        expected = getattr(scope, name)
        if expected is not None and expected != getattr(intent, name):
            return False
    return all(
        name in intent.parameters and _canonical(expected) == _canonical(intent.parameters[name])
        for name, expected in scope.parameters.items()
    )


class PolicyService:
    """Mutação exclusiva do canal confiável de usuário; avaliação nunca escreve regras."""

    def __init__(self, database: Database, registry: ToolRegistry | None = None) -> None:
        self.store = StateStore(database)
        self.registry = DEFAULT_REGISTRY if registry is None else registry

    @staticmethod
    def _agent(unit: UnitOfWork, agent_id: UUID | None) -> None:
        if agent_id is not None and unit.agents.get(agent_id) is None:
            raise NotFoundError("Abelha não encontrada.")

    @staticmethod
    def _owned(unit: UnitOfWork, agent_id: UUID | None, policy_id: UUID | str) -> Policy:
        policy = unit.policies.get(policy_id)
        if policy is None or policy.agent_id != agent_id:
            raise NotFoundError("Política não encontrada neste escopo.")
        return policy

    @staticmethod
    def _relevant(unit: UnitOfWork, agent_id: UUID | None) -> list[Policy]:
        # O repositório legado usa None como ausência de filtro, não IS NULL.
        # Percorrer todas as páginas impede ignorar uma negativa após os primeiros 1000.
        policies = []
        offset = 0
        while True:
            page = unit.policies.list(limit=1000, offset=offset)
            policies.extend(policy for policy in page if policy.agent_id in (None, agent_id))
            if len(page) < 1000:
                break
            offset += len(page)
        return sorted(policies, key=lambda policy: (policy.created_at, str(policy.id)))

    def _validate_scope(self, scope: PolicyScope) -> None:
        if scope.tool_name is None or scope.action is None:
            return
        descriptor = self.registry.get(scope.tool_name, scope.action)
        if descriptor is None:
            return
        for name, parameter in scope.parameters.items():
            field = descriptor.parameters_model.model_fields.get(name)
            if field is None:
                raise ValueError("Escopo contém parâmetro desconhecido desta ação.")
            validated = TypeAdapter(field.rebuild_annotation()).validate_python(
                parameter, strict=True
            )
            if _canonical(validated) != _canonical(parameter):
                raise ValueError("Parâmetro de escopo exige igualdade com valor validado.")

    def _checked_scope(self, raw: PolicyScope | dict) -> None:
        try:
            scope = PolicyScope.model_validate(raw)
            self._validate_scope(scope)
        except ValidationError, ValueError, TypeError:
            raise PolicyError(
                "invalid_policy", "Escopo da política incompatível com o contrato da ação."
            ) from None

    def create(
        self,
        agent_id: UUID | None,
        value: PolicyInput | dict,
        *,
        client_request_id: UUID | None = None,
    ) -> Policy:
        raw = value.model_dump(mode="python") if isinstance(value, PolicyInput) else value
        value = PolicyInput.model_validate(raw)
        self._checked_scope(value.scope)
        request_hash = hashlib.sha256(
            _canonical(
                {
                    "agent_id": str(agent_id) if agent_id else None,
                    "input": value.model_dump(mode="json"),
                }
            ).encode("utf-8")
        ).hexdigest()
        with self.store.transaction(actor="user", source="policy_user") as unit:
            self._agent(unit, agent_id)
            if client_request_id is not None:
                previous = unit.policies.get(client_request_id)
                if previous is not None:
                    if (
                        previous.agent_id != agent_id
                        or previous.metadata.get("policy_request_hash") != request_hash
                    ):
                        raise PolicyError(
                            "idempotency_conflict",
                            "Identificador de envio já usado com outro conteúdo.",
                        )
                    return previous
            return unit.policies.create(
                Policy(
                    **({"id": client_request_id} if client_request_id is not None else {}),
                    agent_id=agent_id,
                    name=value.name,
                    effect=value.effect,
                    scope=value.scope.model_dump(mode="json"),
                    source="user",
                    reason=value.reason,
                    metadata={"policy_request_hash": request_hash} if client_request_id else {},
                )
            )

    def update(
        self, agent_id: UUID | None, policy_id: UUID | str, value: PolicyUpdate | dict
    ) -> Policy:
        raw = (
            value.model_dump(mode="python", exclude_unset=True)
            if isinstance(value, PolicyUpdate)
            else value
        )
        value = PolicyUpdate.model_validate(raw)
        with self.store.transaction(actor="user", source="policy_user") as unit:
            self._agent(unit, agent_id)
            previous = self._owned(unit, agent_id, policy_id)
            changes = value.model_dump(mode="json", exclude_unset=True)
            revision = changes.pop("expected_revision")
            updated = Policy.model_validate(previous.model_dump(mode="json") | changes)
            # Revogar precisa funcionar mesmo quando um registro legado é inválido.
            # Escopo editado e toda reativação continuam exigindo contrato válido.
            if updated.status == "active" or "scope" in changes:
                self._checked_scope(updated.scope)
            return unit.policies.update(updated, expected_revision=revision)

    def get(self, agent_id: UUID | None, policy_id: UUID | str) -> Policy:
        with self.store.transaction(write=False) as unit:
            self._agent(unit, agent_id)
            return self._owned(unit, agent_id, policy_id)

    def list(self, agent_id: UUID | None, *, limit: int = 100, offset: int = 0) -> list[Policy]:
        _pagination(limit, offset)
        with self.store.transaction(write=False) as unit:
            self._agent(unit, agent_id)
            return self._relevant(unit, agent_id)[offset : offset + limit]

    def evaluate(self, unit: UnitOfWork, intent: ActionIntent | dict) -> PolicyDecision:
        raw = intent.model_dump(mode="python") if isinstance(intent, ActionIntent) else intent
        intent = ActionIntent.model_validate(raw)
        policies = [p for p in self._relevant(unit, intent.agent_id) if p.status == "active"]
        descriptor = self.registry.get(intent.tool_name, intent.action)
        snapshot = {
            "intent": intent.model_dump(mode="json"),
            "descriptor": descriptor.snapshot() if descriptor is not None else None,
            "policies": [
                p.model_dump(mode="json") for p in sorted(policies, key=lambda p: str(p.id))
            ],
        }
        rules_hash = hashlib.sha256(_canonical(snapshot).encode("utf-8")).hexdigest()

        def reject(code: str) -> PolicyDecision:
            return PolicyDecision(effect="deny", reason_code=code, rules_hash=rules_hash)

        agent = unit.agents.get(intent.agent_id)
        if agent is None or agent.status != "active":
            return reject("agent_unavailable")
        if descriptor is None:
            return reject("unknown_action")
        try:
            descriptor.validate(intent)
        except ValidationError, ValueError, TypeError:
            return reject("invalid_parameters")
        matches = []
        for policy in policies:
            try:
                scope = PolicyScope.model_validate(policy.scope)
                self._validate_scope(scope)
            except ValidationError, ValueError:
                return reject("invalid_policy")
            if policy.source != "user":
                return reject("invalid_policy")
            if _matches(scope, intent):
                matches.append(policy)
        if not matches:
            return PolicyDecision(
                effect=descriptor.default_effect, reason_code="default", rules_hash=rules_hash
            )
        effect = max((p.effect for p in matches), key={"allow": 0, "ask": 1, "deny": 2}.get)
        deciding = [policy for policy in matches if policy.effect == effect]
        return PolicyDecision(
            effect=effect,
            reason_code="matched_rule",
            matched_policy_ids=tuple(p.id for p in matches),
            deciding_policy_ids=tuple(p.id for p in deciding),
            rules_hash=rules_hash,
            reasons=tuple(policy.reason for policy in deciding if policy.reason),
        )
