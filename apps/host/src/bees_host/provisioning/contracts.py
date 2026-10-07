"""Contratos internos fechados; nenhum campo seleciona caminho, script ou credencial."""

import hashlib
import json
from datetime import datetime
from typing import Literal, Protocol
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

ISO_SHA256 = "a7ef94ac2fb9a7fec454552abd629b7cc9d5155c886165a45649f5ce6167e355"
ISO_SIZE = 792723456
PAYLOAD_SHA256 = "302ad0db9f2b8627213fcbcc2d26e7d0047921a781006a656c4b0d72a5f734a7"
PAYLOAD_SIZE = 387782226
RECIPE_SHA256 = "60442f6cb9ce9c302ff9dd503aea5019fd0d93eab746b71a15aa34cfcbdb09ff"
INVENTORY_SHA256 = "59b71f88fb8537fcabcce0b644ef1fb69ab144a2e0dfd09cd8e1839888f3813a"
INVENTORY_SIZE = 281987
OPERATIONS = ("create_vhd", "create_vm", "configure_vm", "remove_nic", "attach_iso", "verify")
Operation = Literal["create_vhd", "create_vm", "configure_vm", "remove_nic", "attach_iso", "verify"]
Hash = str


class ProvisionError(RuntimeError):
    """Código público fixo, sem stdout, caminhos ou credenciais."""


class Closed(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, hide_input_in_errors=True)


class Plan(Closed):
    format: Literal[1]
    plan_id: UUID
    installation_id: UUID
    agent_id: UUID
    host_id: UUID
    host_revision: int = Field(ge=1)
    environment_id: UUID
    environment_revision: int = Field(ge=1)
    job_id: UUID
    template_id: Literal["linux-desktop-v1"]
    driver: Literal["hyperv"]
    mode: Literal["create_stopped_hardware"]
    vm_name: str
    cpu_count: int = Field(ge=1, le=4)
    memory_bytes: int = Field(ge=2048 * 1024**2, le=8192 * 1024**2)
    disk_bytes: int = Field(ge=20 * 1024**3, le=100 * 1024**3)
    image_iso_sha256: Literal[ISO_SHA256]
    image_iso_size: Literal[ISO_SIZE]
    payload_sha256: Literal[PAYLOAD_SHA256]
    payload_size: Literal[PAYLOAD_SIZE]
    catalog_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    network: Literal["none"]
    storage_profile: Literal["bees-managed-v1"]
    boot: Literal[False]

    @model_validator(mode="after")
    def coherent(self):
        if type(self.format) is not int or type(self.boot) is not bool or self.boot is not False:
            raise ValueError("plan_invalid")
        if self.vm_name != f"Bees-{self.installation_id}-{self.environment_id}":
            raise ValueError("plan_invalid")
        if self.memory_bytes % 1024**2 or self.disk_bytes % 1024**3:
            raise ValueError("plan_invalid")
        for name in (
            "plan_id",
            "installation_id",
            "agent_id",
            "host_id",
            "environment_id",
            "job_id",
        ):
            if not getattr(self, name).int:
                raise ValueError("plan_invalid")
        catalog = {
            "format": 1,
            "template_id": "linux-desktop-v1",
            "image_iso": {"sha256": ISO_SHA256, "size": ISO_SIZE},
            "payload": {"sha256": PAYLOAD_SHA256, "size": PAYLOAD_SIZE},
        }
        if self.catalog_hash != hashlib.sha256(canonical(catalog)).hexdigest():
            raise ValueError("plan_invalid")
        return self

    def digest(self) -> str:
        return hashlib.sha256(canonical(self.model_dump(mode="json"))).hexdigest()


class Claim(Closed):
    claim_id: UUID
    installation_id: UUID
    host_id: UUID
    provisioner_id: UUID
    plan_id: UUID
    plan_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    owner_id: UUID
    generation: int = Field(ge=1, le=2**63 - 1)
    revision: int = Field(ge=1)
    lease_expires_at: AwareDatetime
    status: Literal["claimed", "dispatch_started", "confirmed", "outcome_unknown", "aborted"]
    plan: Plan

    @model_validator(mode="after")
    def coherent(self):
        if (
            self.installation_id != self.plan.installation_id
            or self.host_id != self.plan.host_id
            or self.plan_id != self.plan.plan_id
            or self.plan_hash != self.plan.digest()
        ):
            raise ValueError("claim_invalid")
        if any(not value.int for value in (self.claim_id, self.provisioner_id, self.owner_id)):
            raise ValueError("claim_invalid")
        return self


class DispatchPermit(Closed):
    claim: Claim
    effect_request_id: UUID
    operation: Operation
    status: Literal["dispatch_started", "confirmed", "outcome_unknown"]
    cached: bool
    dispatch_allowed: bool


class ReceiptResult(Closed):
    vm_id: UUID | None
    verified: bool
    cpu_count: int | None = Field(default=None, ge=1, le=4)
    memory_bytes: int | None = Field(default=None, ge=2048 * 1024**2, le=8192 * 1024**2)
    disk_bytes: int | None = Field(default=None, ge=20 * 1024**3, le=100 * 1024**3)
    powered_off: bool | None = None
    network_none: bool | None = None
    image_iso_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


class Inventory(Closed):
    found: bool
    vm_id: UUID | None
    owned: bool
    powered_off: bool
    generation2: bool
    cpu_count: int | None
    memory_bytes: int | None
    disk_bytes: int | None
    fixed_vhdx: bool
    network_adapter_count: int = Field(ge=0, le=64)
    iso_attached: bool


class ProvisionAuthority(Protocol):
    """Adaptador confiável interno: cada método autentica bp_ e revalida o core online.

    Não é callback recebido de modelo/arquivo. Nenhum adaptador HTTP/CLI é instalado
    nesta entrega; fixtures chamam serviço real no laboratório e nada no hospedeiro.
    """

    def assert_current(self, claim: Claim) -> Claim: ...

    def begin_dispatch(
        self, claim: Claim, operation: Operation, client_request_id: UUID
    ) -> DispatchPermit: ...

    def record_receipt(
        self, claim: Claim, effect_request_id: UUID, client_request_id: UUID, result: ReceiptResult
    ) -> DispatchPermit: ...


def canonical(value: dict) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def check_claim(current: Claim, expected: Claim, now: datetime, *, remaining: int = 0) -> None:
    fixed = (
        "claim_id",
        "installation_id",
        "host_id",
        "provisioner_id",
        "plan_id",
        "plan_hash",
        "owner_id",
        "generation",
        "plan",
    )
    if (
        any(getattr(current, name) != getattr(expected, name) for name in fixed)
        or current.status not in {"claimed", "dispatch_started"}
        or current.revision < expected.revision
        or (current.lease_expires_at - now).total_seconds() <= remaining
    ):
        raise ProvisionError("provision_claim_stale")
