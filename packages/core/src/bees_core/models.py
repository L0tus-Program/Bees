"""Contratos canônicos do Bees, independentes de transporte e fornecedor."""

from datetime import UTC, datetime
from typing import Literal, Self
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    field_validator,
    model_validator,
)

TaskStatus = Literal[
    "queued",
    "running",
    "waiting_approval",
    "waiting_resource",
    "paused",
    "completed",
    "failed",
    "cancelled",
]
ActionStatus = Literal[
    "prepared",
    "awaiting_approval",
    "ready",
    "dispatch_started",
    "confirmed",
    "failed_no_effect",
    "outcome_unknown",
    "cancelled",
]
EntityType = Literal[
    "agent",
    "conversation",
    "message",
    "task",
    "run",
    "action",
    "policy",
    "approval",
    "routine",
    "memory",
    "artifact",
    "model_call",
    "task_command",
    "plugin",
    "tool_grant",
    "environment",
    "host_job",
    "host_link",
]


def utc_now() -> datetime:
    return datetime.now(UTC)


class Record(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: UUID = Field(default_factory=uuid4)
    created_at: AwareDatetime = Field(default_factory=utc_now)
    updated_at: AwareDatetime = Field(default_factory=utc_now)
    revision: int = Field(default=1, ge=1)
    metadata: dict[str, JsonValue] = Field(default_factory=dict)

    @model_validator(mode="after")
    def normalize_dates(self) -> Self:
        for name in type(self).model_fields:
            value = getattr(self, name)
            if isinstance(value, datetime):
                object.__setattr__(self, name, value.astimezone(UTC))
        if self.updated_at < self.created_at:
            raise ValueError("updated_at não pode preceder created_at.")
        return self


class Agent(Record):
    model_config = ConfigDict(hide_input_in_errors=True)

    name: str = Field(min_length=1, max_length=200)
    purpose: str = ""
    instructions: str = ""
    provider_config: dict[str, JsonValue] = Field(default_factory=dict)
    status: Literal["active", "paused", "archived"] = "active"

    @field_validator("provider_config", mode="before")
    @classmethod
    def validated_provider_config(cls, value):
        if value == {}:
            return {}
        # Importação tardia mantém o contrato de provedor independente de Agent.
        from bees_core.providers.contracts import ProviderConfig

        try:
            data = value.model_dump(mode="python") if isinstance(value, ProviderConfig) else value
            return ProviderConfig.model_validate(data).model_dump(mode="json")
        except ValueError, TypeError:
            raise ValueError(
                "Configuração de provedor inválida; credenciais exigem referência privada."
            ) from None


class Conversation(Record):
    agent_id: UUID
    title: str = ""
    status: Literal["active", "archived"] = "active"


class Message(Record):
    conversation_id: UUID
    role: Literal["user", "assistant", "system", "tool"]
    content: str
    source: str = "user"


class Task(Record):
    agent_id: UUID
    conversation_id: UUID | None = None
    routine_id: UUID | None = None
    title: str = Field(min_length=1, max_length=200)
    objective: str = Field(min_length=1)
    expected_result: str = ""
    status: TaskStatus = "queued"
    submission_key: UUID | None = None
    desired_state: Literal["running", "paused", "cancelled"] = "running"
    control_revision: int = Field(default=0, ge=0)
    max_calls: int = Field(default=3, ge=1, le=50)
    max_active_seconds: int = Field(default=120, ge=1, le=1800)
    calls_started: int = Field(default=0, ge=0)
    active_milliseconds: int = Field(default=0, ge=0)


class Run(Record):
    task_id: UUID
    status: TaskStatus = "queued"
    checkpoint: dict[str, JsonValue] = Field(default_factory=dict)
    provider: str = ""
    model: str = ""
    environment_id: str | None = None
    started_at: AwareDatetime | None = None
    finished_at: AwareDatetime | None = None
    error: str | None = None
    provider_config: dict[str, JsonValue] = Field(default_factory=dict)

    @field_validator("provider_config", mode="before")
    @classmethod
    def validated_config(cls, value):
        return Agent.validated_provider_config(value)

    @model_validator(mode="after")
    def ordered_run_dates(self) -> Self:
        if self.started_at and self.finished_at and self.finished_at < self.started_at:
            raise ValueError("finished_at não pode preceder started_at.")
        return self


class ModelCall(Record):
    model_config = ConfigDict(hide_input_in_errors=True)

    run_id: UUID
    ordinal: int = Field(ge=1)
    phase: Literal["final", "draft", "review"] = "final"
    status: Literal[
        "prepared",
        "dispatch_started",
        "confirmed",
        "failed_no_effect",
        "outcome_unknown",
        "cancelled",
    ] = "prepared"
    task_control_revision: int = Field(ge=0)
    agent_revision: int = Field(ge=1)
    lease_generation: int = Field(ge=1)
    provider_config: dict[str, JsonValue]
    request: dict[str, JsonValue]
    snapshot: dict[str, JsonValue] = Field(default_factory=dict)
    request_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    response: dict[str, JsonValue] | None = None
    output_message_id: UUID | None = None
    error_code: str | None = Field(default=None, max_length=100, pattern=r"^[a-z0-9_]+$")
    started_at: AwareDatetime | None = None
    finished_at: AwareDatetime | None = None
    unknown_acknowledged_at: AwareDatetime | None = None

    @field_validator("provider_config", mode="before")
    @classmethod
    def validated_config(cls, value):
        from bees_core.providers.contracts import ProviderConfig

        return ProviderConfig.model_validate(value).model_dump(mode="json")

    @field_validator("request", mode="before")
    @classmethod
    def validated_request(cls, value):
        from bees_core.providers.contracts import ChatRequest

        return ChatRequest.model_validate(value).model_dump(mode="json")

    @field_validator("response", mode="before")
    @classmethod
    def validated_response(cls, value):
        if value is None:
            return None
        from bees_core.providers.contracts import ChatResponse

        return ChatResponse.model_validate(value).model_dump(mode="json")

    @model_validator(mode="after")
    def consistent_result(self) -> Self:
        if self.status == "confirmed" and self.response is None:
            raise ValueError("Chamada confirmada exige resposta normalizada.")
        if self.response is not None and self.status != "confirmed":
            raise ValueError("Somente chamada confirmada possui resposta.")
        if self.output_message_id is not None and self.status != "confirmed":
            raise ValueError("Publicação exige chamada confirmada.")
        if self.unknown_acknowledged_at is not None and self.status != "outcome_unknown":
            raise ValueError("Reconhecimento de risco exige resultado desconhecido.")
        return self


class TaskCommand(Record):
    task_id: UUID
    client_request_id: UUID
    kind: Literal["pause", "resume", "cancel", "redirect"]
    expected_revision: int = Field(ge=1)
    payload: dict[str, JsonValue] = Field(default_factory=dict)


class LeaseToken(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    resource_key: str
    owner_id: UUID
    run_id: UUID
    generation: int = Field(ge=1)
    expires_at: AwareDatetime


class ExecutionClaim(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    task: Task
    run: Run
    owner_id: UUID
    leases: tuple[LeaseToken, ...]

    @property
    def generation(self) -> int:
        return self.leases[0].generation


class Action(Record):
    run_id: UUID
    tool_name: str = Field(min_length=1, max_length=200)
    parameters: dict[str, JsonValue] = Field(default_factory=dict)
    status: ActionStatus = "prepared"
    result: dict[str, JsonValue] | None = None
    idempotency_key: str | None = None
    attempt: int = Field(default=1, ge=1)
    policy_revision: int | None = Field(default=None, ge=1)
    lease_generation: int | None = Field(default=None, ge=1)


class Policy(Record):
    agent_id: UUID | None = None
    name: str = Field(min_length=1, max_length=200)
    effect: Literal["allow", "ask", "deny"]
    scope: dict[str, JsonValue] = Field(default_factory=dict)
    status: Literal["active", "revoked"] = "active"
    source: str = "user"
    reason: str = ""


class PluginInstallation(Record):
    manifest: dict[str, JsonValue]
    manifest_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    enabled: bool = Field(default=False, strict=True)


class ToolGrant(Record):
    agent_id: UUID
    plugin_id: UUID
    tool_name: str = Field(min_length=1, max_length=200)
    enabled: bool = Field(default=False, strict=True)


class Environment(Record):
    agent_id: UUID
    name: str = Field(min_length=1, max_length=100)
    template_id: Literal["linux-desktop-v1"] = "linux-desktop-v1"
    cpu_count: int = Field(default=2, ge=1, le=4, strict=True)
    memory_mib: int = Field(default=4096, ge=2048, le=8192, strict=True)
    disk_gib: int = Field(default=30, ge=20, le=100, strict=True)
    status: Literal["awaiting_host", "provisioning", "outcome_unknown", "cancelled"] = (
        "awaiting_host"
    )
    reason_code: str = "host_setup_required"
    client_request_id: UUID
    request_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class HostJob(Record):
    environment_id: UUID
    operation: Literal["create"] = "create"
    status: Literal["awaiting_host", "dispatch_started", "outcome_unknown", "cancelled"] = (
        "awaiting_host"
    )
    correlation_id: UUID
    owner_id: UUID | None = None
    dispatched_at: AwareDatetime | None = None
    recovery_evidence: UUID | None = None

    @model_validator(mode="after")
    def dispatch_evidence(self) -> Self:
        started = self.status in ("dispatch_started", "outcome_unknown")
        if started != (self.owner_id is not None and self.dispatched_at is not None):
            raise ValueError("Despacho exige identidade do dono e instante comprovado.")
        if not started and (self.owner_id is not None or self.dispatched_at is not None):
            raise ValueError("Pedido não iniciado não pode conter evidência de despacho.")
        if self.status == "outcome_unknown" and self.recovery_evidence is None:
            raise ValueError("Recuperação exige evidência opaca do supervisor.")
        if self.status != "outcome_unknown" and self.recovery_evidence is not None:
            raise ValueError("Evidência de recuperação exige resultado desconhecido.")
        return self


class Approval(Record):
    action_id: UUID
    policy_id: UUID | None = None
    status: Literal["pending", "approved", "denied", "revoked"] = "pending"
    decision: Literal["allow_once", "allow_rule", "ask", "deny"] | None = None
    scope: dict[str, JsonValue] = Field(default_factory=dict)
    actor: str = "user"
    reason: str = ""
    decided_at: AwareDatetime | None = None


class Routine(Record):
    agent_id: UUID
    name: str = Field(min_length=1, max_length=200)
    instructions: str = Field(min_length=1)
    schedule: dict[str, JsonValue] = Field(default_factory=dict)
    timezone: str = "UTC"
    next_run_at: AwareDatetime | None = None
    status: Literal["active", "paused", "cancelled"] = "active"
    misfire_policy: Literal["skip", "catch_up"] = "skip"
    overlap_policy: Literal["skip", "queue"] = "skip"

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as error:
            raise ValueError("timezone deve ser um fuso IANA válido.") from error
        return value


class Memory(Record):
    agent_id: UUID | None = None
    task_id: UUID | None = None
    scope: Literal["user", "agent", "task"]
    content: str = Field(min_length=1)
    source: str = "user"
    status: Literal["active", "archived"] = "active"
    deleted_at: AwareDatetime | None = None

    @field_validator("metadata")
    @classmethod
    def validated_memory_details(cls, value):
        if "memory_details" in value:
            from bees_core.memory import MemoryDetails

            value = dict(value)
            value["memory_details"] = MemoryDetails.model_validate(
                value["memory_details"]
            ).model_dump(mode="json")
        return value

    @model_validator(mode="after")
    def consistent_scope(self) -> Self:
        if self.scope == "user" and (self.agent_id is not None or self.task_id is not None):
            raise ValueError("Memória de usuário não pertence a agente/tarefa.")
        if self.scope == "agent" and (self.agent_id is None or self.task_id is not None):
            raise ValueError("Memória de agente exige agente e não pertence a tarefa.")
        if self.scope == "task" and (self.agent_id is None or self.task_id is None):
            raise ValueError("Memória de tarefa exige agente e tarefa.")
        return self


class Artifact(Record):
    task_id: UUID
    run_id: UUID | None = None
    name: str = Field(min_length=1, max_length=200)
    media_type: str = "application/octet-stream"
    storage_key: str | None = None
    sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(default=0, ge=0)
    version: int = Field(default=1, ge=1)
    status: Literal["draft", "ready", "failed", "deleted"] = "draft"

    @model_validator(mode="after")
    def ready_metadata(self) -> Self:
        if self.status == "ready" and (not self.storage_key or self.sha256 is None):
            raise ValueError(
                "Artefato ready exige storage_key e sha256; blob não é verificado aqui."
            )
        return self


class DomainEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    seq: int
    id: UUID
    created_at: AwareDatetime
    entity_type: EntityType
    entity_id: UUID
    event_type: Literal["created", "updated", "reconciled", "deleted"]
    payload: dict[str, JsonValue]
    actor: str
    source: str
    correlation_id: str | None = None
