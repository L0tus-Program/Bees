"""Autoridade interna de provisionamento; não executa comandos ou inicia VMs.

Planos são dados do operador e do pedido canônico. Uma credencial distinta
autoriza claims; a aprovação humana é consumida junto do primeiro intent.
Cada efeito exige guarda online nova. Receipts não são autorização de retry.
"""

import hashlib
import json
import math
import re
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from bees_core.security.hosts import HostService
from bees_core.storage.database import Database
from bees_core.storage.store import NotFoundError, RevisionConflict

OPERATIONS = ("create_vhd", "create_vm", "configure_vm", "remove_nic", "attach_iso", "verify")
Operation = Literal["create_vhd", "create_vm", "configure_vm", "remove_nic", "attach_iso", "verify"]
TOKEN = re.compile(r"bp_[A-Za-z0-9_-]{43}\Z")
MAX_GENERATION = 2**63 - 1


class ProvisioningError(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    @field_validator("*", mode="after")
    @classmethod
    def nonzero_uuid(cls, value):
        if isinstance(value, UUID) and not value.int:
            raise ValueError("UUID não pode ser nulo.")
        return value


class ArtifactPin(Contract):
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size: int = Field(ge=1, le=8 * 1024**3, strict=True)


class TrustedProvisioningCatalog(Contract):
    """Somente composição privada do operador; nunca payload de API ou do modelo."""

    format: Literal[1] = 1
    template_id: Literal["linux-desktop-v1"] = "linux-desktop-v1"
    image_iso: ArtifactPin
    payload: ArtifactPin


# Kit verificado no checkpoint 1a45be5. Não representa arquivos presentes no host.
DEFAULT_PROVISIONING_CATALOG = TrustedProvisioningCatalog(
    image_iso=ArtifactPin(
        sha256="a7ef94ac2fb9a7fec454552abd629b7cc9d5155c886165a45649f5ce6167e355",
        size=792723456,
    ),
    payload=ArtifactPin(
        sha256="302ad0db9f2b8627213fcbcc2d26e7d0047921a781006a656c4b0d72a5f734a7",
        size=387782226,
    ),
)


class PreparePlanInput(Contract):
    host_id: UUID
    expected_environment_revision: int = Field(ge=1, strict=True)
    expected_host_revision: int = Field(ge=1, strict=True)
    client_request_id: UUID


class PlanDecisionInput(Contract):
    expected_revision: int = Field(ge=1, strict=True)
    plan_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    client_request_id: UUID


PlanCommandInput = PlanDecisionInput


class IssueProvisionerInput(Contract):
    expected_host_revision: int = Field(ge=1, strict=True)
    client_request_id: UUID


class ProvisionerRevokeInput(Contract):
    expected_revision: int = Field(ge=1, strict=True)
    client_request_id: UUID


class ClaimInput(Contract):
    plan_id: UUID
    plan_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    owner_id: UUID
    client_request_id: UUID


class ClaimBinding(Contract):
    claim_id: UUID
    owner_id: UUID
    generation: int = Field(ge=1, le=MAX_GENERATION, strict=True)


class RenewInput(ClaimBinding):
    client_request_id: UUID


class BeginDispatchInput(ClaimBinding):
    operation: Operation
    client_request_id: UUID


class HardwareReceipt(Contract):
    vm_id: UUID | None
    verified: bool = Field(strict=True)
    cpu_count: int | None = Field(default=None, ge=1, le=4, strict=True)
    memory_bytes: int | None = Field(default=None, ge=2048 * 1024**2, strict=True)
    disk_bytes: int | None = Field(default=None, ge=20 * 1024**3, strict=True)
    powered_off: bool | None = Field(default=None, strict=True)
    network_none: bool | None = Field(default=None, strict=True)
    image_iso_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")

    @field_validator("vm_id")
    @classmethod
    def physical_vm_id(cls, value):
        if value is not None and str(value) in {
            "ffffffff-ffff-ffff-ffff-ffffffffffff",
            "90db8b89-0d35-4f79-8ce9-49ea0ac8b7cd",
            "e0e16197-dd56-4a10-9195-5ee7a155a838",
            "a42e7cda-d03f-480c-9cc2-a4de20abb878",
        }:
            raise ValueError("VMID especial não representa uma VM física.")
        return value


class ReceiptInput(ClaimBinding):
    effect_request_id: UUID
    client_request_id: UUID
    result: HardwareReceipt


class UnknownInput(ClaimBinding):
    effect_request_id: UUID
    client_request_id: UUID


class AcknowledgeInput(Contract):
    expected_revision: int = Field(ge=1, strict=True)
    client_request_id: UUID


@dataclass(frozen=True)
class IssuedProvisioner:
    installation_id: UUID
    host_id: UUID
    provisioner_id: UUID
    credential: SecretStr = field(repr=False)
    revision: int = 1


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _digest(value):
    return hashlib.sha256(_json(value).encode()).hexdigest()


def _credential_hash(token):
    return hashlib.sha256(("bees:provisioner:credential:1:" + token).encode("ascii")).hexdigest()


def _iso(now):
    return datetime.fromtimestamp(now, UTC).isoformat() if now is not None else None


_COLUMNS = {
    "provisioning_plans": "id,installation_id,host_id,environment_id,job_id,plan_hash,plan_json,"
    "client_request_id,request_hash,created_at",
    "provisioning_authorizations": "plan_id,revision,status,authorized_at,expires_at,"
    "consumed_claim_id",
    "provisioning_credentials": "id,installation_id,host_id,host_revision,credential_hash,status,"
    "revision,issue_request_id,created_at,revoked_at",
    "provisioning_claims": "id,plan_id,provisioner_id,host_id,owner_id,generation,"
    "authorization_revision,"
    "revision,status,client_request_id,request_hash,created_at,lease_expires_at,recovery_evidence,"
    "acknowledged_at",
    "provisioning_effects": "effect_request_id,claim_id,ordinal,operation,status,request_hash,"
    "created_at,confirmed_at,result_json,receipt_request_id,receipt_hash",
    "environments": "id,agent_id,revision,status,template_id,cpu_count,memory_mib,disk_gib",
    "host_jobs": "id,environment_id,revision,status,owner_id,correlation_id",
}


def _row(connection, table, value):
    key = (
        "plan_id"
        if table == "provisioning_authorizations"
        else ("effect_request_id" if table == "provisioning_effects" else "id")
    )
    columns = _COLUMNS[table]
    row = connection.execute(
        f"SELECT {columns} FROM {table} WHERE {key}=?", (str(value),)
    ).fetchone()
    if row is None:
        raise NotFoundError("Registro de provisionamento não encontrado.")
    return dict(zip(columns.split(","), row, strict=True))


class ProvisioningService:
    AUTHORIZATION_TTL = 900
    LEASE_SECONDS = 60
    MAX_CLAIM_SECONDS = 300

    def __init__(
        self,
        database: Database,
        catalog: TrustedProvisioningCatalog | None = DEFAULT_PROVISIONING_CATALOG,
        *,
        clock: Callable[[], float] = time.time,
    ):
        self.database, self.catalog, self.clock = database, catalog, clock
        if catalog is not None and not isinstance(catalog, TrustedProvisioningCatalog):
            raise ValueError("Catálogo exige configuração confiável tipada.")

    def _now(self):
        now = float(self.clock())
        if not math.isfinite(now) or now < 0:
            raise ValueError("Relógio inválido.")
        return now

    @staticmethod
    def _human(connection):
        if not connection.execute("SELECT EXISTS(SELECT 1 FROM identity_users)").get:
            raise ProvisioningError("provisioning_identity_required")

    @staticmethod
    def _event(
        connection, entity_id, operation, now, request_id=None, *, actor="user", payload=None
    ):
        connection.execute(
            "INSERT INTO domain_events(id,created_at,entity_type,entity_id,event_type,payload_json,"
            "actor,source,correlation_id) VALUES(?,?,?,?,?,?,?,?,?)",
            (
                str(uuid4()),
                _iso(now),
                "provisioning",
                str(entity_id),
                "updated",
                _json({"operation": operation, **(payload or {})}),
                actor,
                "provisioning",
                str(request_id) if request_id else None,
            ),
        )

    @staticmethod
    def _command(connection, entity, request_id, command, payload, now, *, save=False):
        request_hash = _digest({"command": command, **payload})
        prior = connection.execute(
            "SELECT request_hash FROM provisioning_commands WHERE entity_id=? AND "
            "client_request_id=?",
            (str(entity), str(request_id)),
        ).get
        if prior is not None:
            if prior != request_hash:
                raise ProvisioningError("provisioning_request_conflict")
            return True
        if save:
            connection.execute(
                "INSERT INTO provisioning_commands VALUES(?,?,?,?,?)",
                (str(entity), str(request_id), command, request_hash, now),
            )
        return False

    def _host(self, connection, host_id, revision, *, online=False, capable=False):
        host = HostService._row(connection, host_id)
        if host["status"] != "active" or host["revision"] != revision:
            raise ProvisioningError("provisioning_host_changed")
        if online:
            seen = host["last_seen"]
            if seen is None or not 0 <= self._now() - seen < HostService.REPORT_TTL:
                raise ProvisioningError("provisioning_host_offline")
        if capable:
            diagnostic = json.loads(host["diagnostic_json"] or "{}")
            probe = diagnostic.get("probe", {})
            if (
                diagnostic.get("driver") != "hyperv"
                or set(probe) != {"platform", "module", "service"}
                or any(type(value) is not bool or not value for value in probe.values())
            ):
                raise ProvisioningError("provisioning_driver_unavailable")
        return host

    def _authenticate(self, connection, token, *, online=False):
        if not isinstance(token, str) or TOKEN.fullmatch(token) is None:
            raise ProvisioningError("provisioning_credentials_invalid")
        credential_id = connection.execute(
            "SELECT id FROM provisioning_credentials WHERE credential_hash=?",
            (_credential_hash(token),),
        ).get
        if credential_id is None:
            raise ProvisioningError("provisioning_credentials_invalid")
        credential = _row(connection, "provisioning_credentials", credential_id)
        if (
            credential["status"] != "active"
            or HostService._installation(connection) != credential["installation_id"]
        ):
            raise ProvisioningError("provisioning_credentials_invalid")
        self._host(connection, credential["host_id"], credential["host_revision"], online=online)
        return credential

    def _context(self, connection, plan, *, started=False):
        snapshot = json.loads(plan["plan_json"])
        if (
            self.catalog is None
            or _digest(self.catalog.model_dump(mode="json")) != snapshot["catalog_hash"]
        ):
            raise ProvisioningError("provisioning_catalog_changed")
        if HostService._installation(connection) != plan["installation_id"]:
            raise ProvisioningError("provisioning_installation_changed")
        self._host(connection, plan["host_id"], snapshot["host_revision"])
        environment = _row(connection, "environments", plan["environment_id"])
        job = _row(connection, "host_jobs", plan["job_id"])
        if (
            connection.execute("SELECT status FROM agents WHERE id=?", (snapshot["agent_id"],)).get
            != "active"
        ):
            raise ProvisioningError("provisioning_agent_inactive")
        expected_state = "provisioning" if started else "awaiting_host"
        expected_job = "dispatch_started" if started else "awaiting_host"
        if (
            environment["status"] != expected_state
            or job["status"] != expected_job
            or environment["revision"] != snapshot["environment_revision"] + int(started)
            or job["environment_id"] != environment["id"]
            or environment["agent_id"] != snapshot["agent_id"]
        ):
            raise ProvisioningError("provisioning_environment_changed")
        return snapshot, environment, job

    def _view(self, connection, plan):
        auth = _row(connection, "provisioning_authorizations", plan["id"])
        status = auth["status"]
        claim_state = connection.execute(
            "SELECT status FROM provisioning_claims WHERE plan_id=? AND status!='aborted' "
            "ORDER BY generation DESC LIMIT 1",
            (plan["id"],),
        ).get
        if claim_state == "outcome_unknown" or (
            auth["status"] != "revoked" and claim_state in ("dispatch_started", "confirmed")
        ):
            status = "hardware_verified" if claim_state == "confirmed" else claim_state
        context_valid = True
        reason_code = None
        try:
            self._context(
                connection, plan, started=claim_state in ("dispatch_started", "confirmed")
            )
        except ProvisioningError as error:
            context_valid = False
            reason_code = {
                "provisioning_host_changed": "host_changed",
                "provisioning_installation_changed": "installation_changed",
                "provisioning_catalog_changed": "catalog_changed",
                "provisioning_agent_inactive": "agent_inactive",
                "provisioning_environment_changed": "environment_changed",
            }.get(error.code, "environment_changed")
        if context_valid and auth["status"] == "authorized" and self._now() >= auth["expires_at"]:
            reason_code = "authorization_expired"
        return {
            "plan_id": plan["id"],
            "plan_hash": plan["plan_hash"],
            "revision": auth["revision"],
            "status": status,
            "created_at": _iso(plan["created_at"]),
            "authorization_expires_at": _iso(auth["expires_at"]),
            "usable": False,
            "context_valid": context_valid,
            "reason_code": reason_code,
            "plan": json.loads(plan["plan_json"]),
        }

    @staticmethod
    def _owned(connection, plan, agent_id, environment_id):
        if agent_id is None and environment_id is None:
            return
        if agent_id is None or environment_id is None:
            raise ValueError("Conferência de vínculo exige abelha e computador.")
        environment = _row(connection, "environments", environment_id)
        if plan["environment_id"] != str(environment_id) or environment["agent_id"] != str(
            agent_id
        ):
            raise NotFoundError("Plano não encontrado para esta abelha e computador.")

    def current_plan(self, agent_id: UUID, environment_id: UUID):
        with self.database.transaction(write=False) as connection:
            environment = _row(connection, "environments", environment_id)
            if environment["agent_id"] != str(agent_id):
                raise NotFoundError("Computador não encontrado para esta abelha.")
            plan_id = connection.execute(
                "SELECT id FROM provisioning_plans WHERE environment_id=? ORDER BY rowid "
                "DESC LIMIT 1",
                (str(environment_id),),
            ).get
            return (
                self._view(connection, _row(connection, "provisioning_plans", plan_id))
                if plan_id
                else None
            )

    def active_host_summary(self):
        with self.database.transaction(write=False) as connection:
            row = connection.execute(
                "SELECT host_id,revision,fingerprint FROM host_links WHERE status='active'"
            ).fetchone()
            return (
                dict(zip(("host_id", "revision", "fingerprint"), row, strict=True)) if row else None
            )

    def get(self, plan_id: UUID):
        with self.database.transaction(write=False) as connection:
            return self._view(connection, _row(connection, "provisioning_plans", plan_id))

    def list(self, agent_id: UUID, environment_id: UUID, limit=100):
        if type(limit) is not int or not 1 <= limit <= 101:
            raise ValueError("Paginação inválida.")
        with self.database.transaction(write=False) as connection:
            environment = _row(connection, "environments", environment_id)
            if environment["agent_id"] != str(agent_id):
                raise NotFoundError("Computador não encontrado para esta abelha.")
            ids = connection.execute(
                "SELECT id FROM provisioning_plans WHERE environment_id=? ORDER BY "
                "created_at DESC,id DESC LIMIT ?",
                (str(environment_id), limit),
            ).fetchall()
            return [
                self._view(connection, _row(connection, "provisioning_plans", row[0]))
                for row in ids
            ]

    def prepare_plan(self, agent_id: UUID, environment_id: UUID, value: PreparePlanInput | dict):
        value = PreparePlanInput.model_validate(value)
        now = self._now()
        payload = {
            "agent_id": str(agent_id),
            "environment_id": str(environment_id),
            **value.model_dump(mode="json"),
        }
        request_hash = _digest(payload)
        with self.database.transaction() as connection:
            self._human(connection)
            prior = connection.execute(
                "SELECT id,request_hash FROM provisioning_plans WHERE client_request_id=?",
                (str(value.client_request_id),),
            ).fetchone()
            if prior:
                if prior[1] != request_hash:
                    raise ProvisioningError("provisioning_request_conflict")
                return self._view(connection, _row(connection, "provisioning_plans", prior[0]))
            environment = _row(connection, "environments", environment_id)
            if environment["agent_id"] != str(agent_id):
                raise NotFoundError("Computador não encontrado para esta abelha.")
            if environment["revision"] != value.expected_environment_revision:
                raise RevisionConflict("Pedido do computador mudou.")
            if environment["status"] != "awaiting_host":
                raise ProvisioningError("provisioning_environment_changed")
            self._host(connection, value.host_id, value.expected_host_revision)
            if self.catalog is None:
                raise ProvisioningError("provisioning_catalog_unavailable")
            job_id = connection.execute(
                "SELECT id FROM host_jobs WHERE environment_id=?", (str(environment_id),)
            ).get
            job = _row(connection, "host_jobs", job_id)
            if job["status"] != "awaiting_host":
                raise ProvisioningError("provisioning_environment_changed")
            if connection.execute(
                "SELECT EXISTS(SELECT 1 FROM provisioning_plans p JOIN "
                "provisioning_authorizations a "
                "ON a.plan_id=p.id WHERE p.environment_id=? AND a.status!='revoked')",
                (str(environment_id),),
            ).get:
                raise ProvisioningError("provisioning_plan_exists")
            installation_id = HostService._installation(connection)
            plan_id = str(uuid4())
            snapshot = {
                "format": 1,
                "plan_id": plan_id,
                "installation_id": installation_id,
                "agent_id": str(agent_id),
                "host_id": str(value.host_id),
                "host_revision": value.expected_host_revision,
                "environment_id": str(environment_id),
                "environment_revision": environment["revision"],
                "job_id": job_id,
                "template_id": environment["template_id"],
                "driver": "hyperv",
                "mode": "create_stopped_hardware",
                "vm_name": f"Bees-{installation_id}-{environment_id}",
                "cpu_count": environment["cpu_count"],
                "memory_bytes": environment["memory_mib"] * 1024**2,
                "disk_bytes": environment["disk_gib"] * 1024**3,
                "image_iso_sha256": self.catalog.image_iso.sha256,
                "image_iso_size": self.catalog.image_iso.size,
                "payload_sha256": self.catalog.payload.sha256,
                "payload_size": self.catalog.payload.size,
                "catalog_hash": _digest(self.catalog.model_dump(mode="json")),
                "network": "none",
                "storage_profile": "bees-managed-v1",
                "boot": False,
            }
            connection.execute(
                "INSERT INTO provisioning_plans VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    plan_id,
                    installation_id,
                    str(value.host_id),
                    str(environment_id),
                    job_id,
                    _digest(snapshot),
                    _json(snapshot),
                    str(value.client_request_id),
                    request_hash,
                    now,
                ),
            )
            connection.execute(
                "INSERT INTO provisioning_authorizations(plan_id,status) VALUES(?,'prepared')",
                (plan_id,),
            )
            self._context(connection, _row(connection, "provisioning_plans", plan_id))
            self._event(connection, plan_id, "plan_prepared", now, value.client_request_id)
            return self._view(connection, _row(connection, "provisioning_plans", plan_id))

    def _decision(self, plan_id, value, command, *, agent_id=None, environment_id=None):
        value = PlanDecisionInput.model_validate(value)
        now = self._now()
        with self.database.transaction() as connection:
            self._human(connection)
            plan = _row(connection, "provisioning_plans", plan_id)
            self._owned(connection, plan, agent_id, environment_id)
            payload = value.model_dump(mode="json")
            if self._command(connection, plan_id, value.client_request_id, command, payload, now):
                return self._view(connection, plan)
            auth = _row(connection, "provisioning_authorizations", plan_id)
            if value.plan_hash != plan["plan_hash"]:
                raise ProvisioningError("provisioning_plan_changed")
            if auth["revision"] != value.expected_revision:
                raise RevisionConflict("Autorização de provisionamento mudou.")
            if command == "authorize_once":
                self._context(connection, plan)
                if (
                    auth["consumed_claim_id"]
                    or auth["status"] == "revoked"
                    or (auth["status"] == "authorized" and now < auth["expires_at"])
                ):
                    raise ProvisioningError("provisioning_authorization_conflict")
                connection.execute(
                    "UPDATE provisioning_authorizations SET "
                    "status='authorized',revision=revision+1,"
                    "authorized_at=?,expires_at=? WHERE plan_id=?",
                    (now, now + self.AUTHORIZATION_TTL, str(plan_id)),
                )
            else:
                if auth["status"] == "revoked":
                    raise ProvisioningError("provisioning_authorization_conflict")
                connection.execute(
                    "UPDATE provisioning_authorizations SET "
                    "status='revoked',revision=revision+1 WHERE plan_id=?",
                    (str(plan_id),),
                )
            self._command(
                connection, plan_id, value.client_request_id, command, payload, now, save=True
            )
            self._event(connection, plan_id, command, now, value.client_request_id)
            return self._view(connection, plan)

    def authorize_once(
        self, plan_id: UUID, value: PlanCommandInput | dict, *, agent_id=None, environment_id=None
    ):
        return self._decision(
            plan_id, value, "authorize_once", agent_id=agent_id, environment_id=environment_id
        )

    def revoke_authorization(
        self, plan_id: UUID, value: PlanCommandInput | dict, *, agent_id=None, environment_id=None
    ):
        return self._decision(
            plan_id, value, "revoke_authorization", agent_id=agent_id, environment_id=environment_id
        )

    def issue_provisioner(self, host_id: UUID, value: IssueProvisionerInput | dict):
        """Somente operador privado; o segredo retornado uma vez deve ir ao cofre próprio."""
        value = IssueProvisionerInput.model_validate(value)
        now = self._now()
        token = "bp_" + secrets.token_urlsafe(32)
        with self.database.transaction() as connection:
            self._human(connection)
            if connection.execute(
                "SELECT EXISTS(SELECT 1 FROM provisioning_credentials WHERE issue_request_id=?)",
                (str(value.client_request_id),),
            ).get:
                raise ProvisioningError("provisioning_credential_already_issued")
            self._host(connection, host_id, value.expected_host_revision)
            if connection.execute(
                "SELECT EXISTS(SELECT 1 FROM provisioning_credentials WHERE host_id=? "
                "AND status='active')",
                (str(host_id),),
            ).get:
                raise ProvisioningError("provisioning_active_credential_exists")
            installation = HostService._installation(connection)
            provisioner_id = str(uuid4())
            connection.execute(
                "INSERT INTO provisioning_credentials(id,installation_id,host_id,host_revision,"
                "credential_hash,status,issue_request_id,created_at) "
                "VALUES(?,?,?,?,?,'active',?,?)",
                (
                    provisioner_id,
                    installation,
                    str(host_id),
                    value.expected_host_revision,
                    _credential_hash(token),
                    str(value.client_request_id),
                    now,
                ),
            )
            self._event(
                connection,
                provisioner_id,
                "provisioner_issued",
                now,
                value.client_request_id,
                actor="operator",
            )
            return IssuedProvisioner(
                UUID(installation), UUID(str(host_id)), UUID(provisioner_id), SecretStr(token)
            )

    def revoke_provisioner(self, provisioner_id: UUID, value: ProvisionerRevokeInput | dict):
        value = ProvisionerRevokeInput.model_validate(value)
        now = self._now()
        with self.database.transaction() as connection:
            self._human(connection)
            row = _row(connection, "provisioning_credentials", provisioner_id)
            payload = value.model_dump(mode="json")
            if not self._command(
                connection,
                provisioner_id,
                value.client_request_id,
                "revoke_provisioner",
                payload,
                now,
            ):
                if row["revision"] != value.expected_revision:
                    raise RevisionConflict("Provisionador mudou.")
                if row["status"] != "active":
                    raise ProvisioningError("provisioning_credentials_invalid")
                connection.execute(
                    "UPDATE provisioning_credentials SET "
                    "status='revoked',revision=revision+1,revoked_at=? WHERE id=?",
                    (now, str(provisioner_id)),
                )
                self._command(
                    connection,
                    provisioner_id,
                    value.client_request_id,
                    "revoke_provisioner",
                    payload,
                    now,
                    save=True,
                )
                self._event(
                    connection, provisioner_id, "provisioner_revoked", now, value.client_request_id
                )
                row = _row(connection, "provisioning_credentials", provisioner_id)
            return self._credential_view(row)

    @staticmethod
    def _credential_view(row):
        return {
            "provisioner_id": row["id"],
            "installation_id": row["installation_id"],
            "host_id": row["host_id"],
            "revision": row["revision"],
            "status": row["status"],
        }

    def session(self, token: str):
        with self.database.transaction(write=False) as connection:
            return self._credential_view(self._authenticate(connection, token))

    @staticmethod
    def _claim_view(connection, claim):
        plan = _row(connection, "provisioning_plans", claim["plan_id"])
        return {
            "claim_id": claim["id"],
            "installation_id": plan["installation_id"],
            "host_id": claim["host_id"],
            "provisioner_id": claim["provisioner_id"],
            "plan_id": claim["plan_id"],
            "plan_hash": plan["plan_hash"],
            "owner_id": claim["owner_id"],
            "generation": claim["generation"],
            "revision": claim["revision"],
            "status": claim["status"],
            "lease_expires_at": _iso(claim["lease_expires_at"]),
            "plan": json.loads(plan["plan_json"]),
        }

    def _bound(self, connection, token, value, *, capable=False):
        credential = self._authenticate(connection, token, online=True)
        claim = _row(connection, "provisioning_claims", value.claim_id)
        if (
            claim["provisioner_id"] != credential["id"]
            or claim["owner_id"] != str(value.owner_id)
            or claim["generation"] != value.generation
        ):
            raise ProvisioningError("provisioning_claim_fenced")
        if (
            claim["status"] not in ("claimed", "dispatch_started")
            or claim["lease_expires_at"] <= self._now()
        ):
            raise ProvisioningError("provisioning_claim_fenced")
        current_generation = connection.execute(
            "SELECT generation FROM provisioning_hosts WHERE host_id=?", (claim["host_id"],)
        ).get
        if current_generation != value.generation:
            raise ProvisioningError("provisioning_claim_fenced")
        plan = _row(connection, "provisioning_plans", claim["plan_id"])
        auth = _row(connection, "provisioning_authorizations", plan["id"])
        consumed = auth["consumed_claim_id"]
        expected_revision = claim["authorization_revision"] + int(consumed is not None)
        if (
            auth["status"] != "authorized"
            or auth["expires_at"] <= self._now()
            or auth["revision"] != expected_revision
            or consumed not in (None, claim["id"])
        ):
            raise ProvisioningError("provisioning_authorization_changed")
        snapshot, _, job = self._context(
            connection, plan, started=claim["status"] == "dispatch_started"
        )
        if claim["status"] == "dispatch_started" and job["owner_id"] != claim["owner_id"]:
            raise ProvisioningError("provisioning_claim_fenced")
        self._host(
            connection, claim["host_id"], snapshot["host_revision"], online=True, capable=capable
        )
        return claim, plan, auth

    def claim(self, token: str, value: ClaimInput | dict):
        value = ClaimInput.model_validate(value)
        now = self._now()
        payload = value.model_dump(mode="json")
        with self.database.transaction() as connection:
            credential = self._authenticate(connection, token, online=True)
            payload_hash = _digest(payload | {"provisioner_id": credential["id"]})
            prior = connection.execute(
                "SELECT id,request_hash FROM provisioning_claims WHERE client_request_id=?",
                (str(value.client_request_id),),
            ).fetchone()
            if prior:
                if prior[1] != payload_hash:
                    raise ProvisioningError("provisioning_request_conflict")
                return self._claim_view(
                    connection, _row(connection, "provisioning_claims", prior[0])
                )
            plan = _row(connection, "provisioning_plans", value.plan_id)
            if plan["plan_hash"] != value.plan_hash or plan["host_id"] != credential["host_id"]:
                raise ProvisioningError("provisioning_plan_changed")
            self._context(connection, plan)
            auth = _row(connection, "provisioning_authorizations", value.plan_id)
            if (
                auth["status"] != "authorized"
                or auth["expires_at"] <= now
                or auth["consumed_claim_id"]
            ):
                raise ProvisioningError("provisioning_authorization_changed")
            if connection.execute(
                "SELECT EXISTS(SELECT 1 FROM provisioning_claims c JOIN provisioning_plans p "
                "ON p.id=c.plan_id WHERE p.installation_id=? AND "
                "c.status IN ('claimed','dispatch_started','outcome_unknown'))",
                (credential["installation_id"],),
            ).get:
                raise ProvisioningError("provisioning_host_quarantined")
            connection.execute(
                "INSERT INTO provisioning_hosts(host_id) VALUES(?) ON CONFLICT DO NOTHING",
                (credential["host_id"],),
            )
            generation = (
                connection.execute(
                    "SELECT generation FROM provisioning_hosts WHERE host_id=?",
                    (credential["host_id"],),
                ).get
                + 1
            )
            if generation > MAX_GENERATION:
                raise ProvisioningError("provisioning_generation_exhausted")
            connection.execute(
                "UPDATE provisioning_hosts SET generation=? WHERE host_id=?",
                (generation, credential["host_id"]),
            )
            claim_id = str(uuid4())
            connection.execute(
                "INSERT INTO provisioning_claims(id,plan_id,provisioner_id,host_id,owner_"
                "id,generation,"
                "authorization_revision,status,client_request_id,request_hash,created_at,"
                "lease_expires_at) "
                "VALUES(?,?,?,?,?,?,?,'claimed',?,?,?,?)",
                (
                    claim_id,
                    str(value.plan_id),
                    credential["id"],
                    credential["host_id"],
                    str(value.owner_id),
                    generation,
                    auth["revision"],
                    str(value.client_request_id),
                    payload_hash,
                    now,
                    now + self.LEASE_SECONDS,
                ),
            )
            self._event(
                connection, claim_id, "claimed", now, value.client_request_id, actor="provisioner"
            )
            return self._claim_view(connection, _row(connection, "provisioning_claims", claim_id))

    def assert_current(self, token: str, value: ClaimBinding | dict):
        value = ClaimBinding.model_validate(value)
        with self.database.transaction(write=False) as connection:
            claim, _, _ = self._bound(connection, token, value, capable=True)
            return self._claim_view(connection, claim)

    def renew(self, token: str, value: RenewInput | dict):
        value = RenewInput.model_validate(value)
        now = self._now()
        with self.database.transaction() as connection:
            claim, _, auth = self._bound(connection, token, value, capable=True)
            payload = value.model_dump(mode="json")
            if not self._command(
                connection, value.claim_id, value.client_request_id, "renew", payload, now
            ):
                expires = min(
                    now + self.LEASE_SECONDS,
                    claim["created_at"] + self.MAX_CLAIM_SECONDS,
                    auth["expires_at"],
                )
                if expires <= now:
                    raise ProvisioningError("provisioning_claim_fenced")
                connection.execute(
                    "UPDATE provisioning_claims SET lease_expires_at=?,revision=revision+1 "
                    "WHERE id=?",
                    (expires, claim["id"]),
                )
                self._command(
                    connection,
                    value.claim_id,
                    value.client_request_id,
                    "renew",
                    payload,
                    now,
                    save=True,
                )
            return self._claim_view(
                connection, _row(connection, "provisioning_claims", claim["id"])
            )

    def _dispatch_view(self, connection, claim, effect, *, cached):
        return {
            "claim": self._claim_view(connection, claim),
            "effect_request_id": effect["effect_request_id"],
            "operation": effect["operation"],
            "status": effect["status"],
            "cached": cached,
            "dispatch_allowed": not cached and effect["status"] == "dispatch_started",
        }

    def begin_dispatch(self, token: str, value: BeginDispatchInput | dict):
        value = BeginDispatchInput.model_validate(value)
        now = self._now()
        with self.database.transaction() as connection:
            credential = self._authenticate(connection, token, online=True)
            claim = _row(connection, "provisioning_claims", value.claim_id)
            if (
                claim["provisioner_id"] != credential["id"]
                or claim["owner_id"] != str(value.owner_id)
                or claim["generation"] != value.generation
            ):
                raise ProvisioningError("provisioning_claim_fenced")
            payload_hash = _digest(value.model_dump(mode="json"))
            prior = connection.execute(
                "SELECT effect_request_id,request_hash FROM provisioning_effects WHERE "
                "effect_request_id=?",
                (str(value.client_request_id),),
            ).fetchone()
            if prior:
                if prior[1] != payload_hash:
                    raise ProvisioningError("provisioning_request_conflict")
                return self._dispatch_view(
                    connection,
                    claim,
                    _row(connection, "provisioning_effects", prior[0]),
                    cached=True,
                )
            claim, plan, auth = self._bound(connection, token, value, capable=True)
            previous = connection.execute(
                "SELECT ordinal,status FROM provisioning_effects WHERE claim_id=? ORDER "
                "BY ordinal DESC LIMIT 1",
                (claim["id"],),
            ).fetchone()
            ordinal = 1 if previous is None else previous[0] + 1
            if (
                ordinal > len(OPERATIONS)
                or OPERATIONS[ordinal - 1] != value.operation
                or (previous and previous[1] != "confirmed")
            ):
                raise ProvisioningError("provisioning_operation_conflict")
            if ordinal == 1:
                if auth["consumed_claim_id"] is not None:
                    raise ProvisioningError("provisioning_authorization_changed")
                connection.execute(
                    "UPDATE provisioning_authorizations SET "
                    "consumed_claim_id=?,revision=revision+1 WHERE plan_id=?",
                    (claim["id"], plan["id"]),
                )
                connection.execute(
                    "UPDATE host_jobs SET status='dispatch_started',owner_id=?,dispatched_at="
                    "?,revision=revision+1,updated_at=? WHERE id=? AND status='awaiting_host'",
                    (claim["owner_id"], _iso(now), _iso(now), plan["job_id"]),
                )
                if connection.changes() != 1:
                    raise ProvisioningError("provisioning_environment_changed")
                connection.execute(
                    "UPDATE environments SET "
                    "status='provisioning',reason_code='hardware_provisioning',revision=revis"
                    "ion+1,updated_at=? WHERE id=? AND status='awaiting_host'",
                    (_iso(now), plan["environment_id"]),
                )
                if connection.changes() != 1:
                    raise ProvisioningError("provisioning_environment_changed")
                connection.execute(
                    "UPDATE provisioning_claims SET "
                    "status='dispatch_started',revision=revision+1 WHERE id=?",
                    (claim["id"],),
                )
            connection.execute(
                "INSERT INTO provisioning_effects(effect_request_id,claim_id,ordinal,oper"
                "ation,status,request_hash,created_at) "
                "VALUES(?,?,?,?,'dispatch_started',?,?)",
                (
                    str(value.client_request_id),
                    claim["id"],
                    ordinal,
                    value.operation,
                    payload_hash,
                    now,
                ),
            )
            self._event(
                connection,
                claim["id"],
                "dispatch_started",
                now,
                value.client_request_id,
                actor="provisioner",
                payload={"operation_name": value.operation, "ordinal": ordinal},
            )
            return self._dispatch_view(
                connection,
                _row(connection, "provisioning_claims", claim["id"]),
                _row(connection, "provisioning_effects", value.client_request_id),
                cached=False,
            )

    def _receipt_result(self, connection, claim, effect, plan, result):
        output = result.model_dump(mode="json", exclude_none=True)
        if not result.verified:
            raise ProvisioningError("provisioning_receipt_invalid")
        known = connection.execute(
            "SELECT result_json FROM provisioning_effects WHERE claim_id=? AND "
            "operation='create_vm' AND status='confirmed'",
            (claim["id"],),
        ).get
        vm_id = json.loads(known)["vm_id"] if known else None
        if effect["operation"] == "create_vhd":
            if result.vm_id is not None:
                raise ProvisioningError("provisioning_receipt_invalid")
        elif effect["operation"] == "create_vm":
            if result.vm_id is None:
                raise ProvisioningError("provisioning_receipt_invalid")
        elif str(result.vm_id) != vm_id:
            raise ProvisioningError("provisioning_receipt_invalid")
        extras = set(output) - {"vm_id", "verified"}
        if effect["operation"] == "verify":
            snapshot = json.loads(plan["plan_json"])
            expected = {
                key: snapshot[key]
                for key in ("cpu_count", "memory_bytes", "disk_bytes", "image_iso_sha256")
            }
            expected.update(powered_off=True, network_none=True)
            if {key: output.get(key) for key in expected} != expected or extras != set(expected):
                raise ProvisioningError("provisioning_receipt_invalid")
        elif extras:
            raise ProvisioningError("provisioning_receipt_invalid")
        return output | {"vm_id": str(result.vm_id) if result.vm_id else None}

    def record_receipt(self, token: str, value: ReceiptInput | dict):
        value = ReceiptInput.model_validate(value)
        now = self._now()
        with self.database.transaction() as connection:
            credential = self._authenticate(connection, token, online=True)
            claim = _row(connection, "provisioning_claims", value.claim_id)
            if (
                claim["provisioner_id"] != credential["id"]
                or claim["owner_id"] != str(value.owner_id)
                or claim["generation"] != value.generation
            ):
                raise ProvisioningError("provisioning_claim_fenced")
            effect = _row(connection, "provisioning_effects", value.effect_request_id)
            payload_hash = _digest(value.model_dump(mode="json"))
            if effect["claim_id"] != claim["id"]:
                raise ProvisioningError("provisioning_claim_fenced")
            if effect["receipt_request_id"] is not None:
                if (
                    effect["receipt_request_id"] != str(value.client_request_id)
                    or effect["receipt_hash"] != payload_hash
                ):
                    raise ProvisioningError("provisioning_request_conflict")
                return self._dispatch_view(connection, claim, effect, cached=True)
            claim, plan, _ = self._bound(connection, token, value, capable=True)
            if effect["status"] != "dispatch_started":
                raise ProvisioningError("provisioning_operation_conflict")
            output = self._receipt_result(connection, claim, effect, plan, value.result)
            if effect["operation"] == "create_vm":
                existing = connection.execute(
                    "SELECT claim_id FROM provisioning_vm_bindings WHERE host_id=? AND vm_id=?",
                    (claim["host_id"], output["vm_id"]),
                ).get
                if existing is not None:
                    raise ProvisioningError("provisioning_vm_binding_conflict")
                connection.execute(
                    "INSERT INTO provisioning_vm_bindings VALUES(?,?,?,?)",
                    (claim["host_id"], output["vm_id"], claim["id"], now),
                )
            connection.execute(
                "UPDATE provisioning_effects SET "
                "status='confirmed',confirmed_at=?,result_json=?,receipt_request_id=?,"
                "receipt_hash=? WHERE effect_request_id=?",
                (
                    now,
                    _json(output),
                    str(value.client_request_id),
                    payload_hash,
                    str(value.effect_request_id),
                ),
            )
            if effect["operation"] == "verify":
                connection.execute(
                    "UPDATE provisioning_claims SET status='confirmed',revision=revision+1 "
                    "WHERE id=?",
                    (claim["id"],),
                )
            self._event(
                connection,
                claim["id"],
                "effect_confirmed",
                now,
                value.client_request_id,
                actor="provisioner",
                payload={"operation_name": effect["operation"]},
            )
            return self._dispatch_view(
                connection,
                _row(connection, "provisioning_claims", claim["id"]),
                _row(connection, "provisioning_effects", value.effect_request_id),
                cached=False,
            ) | {"dispatch_allowed": False}

    def _unknown(self, connection, claim, evidence, now):
        connection.execute(
            "UPDATE provisioning_effects SET status='outcome_unknown' WHERE "
            "claim_id=? AND status='dispatch_started'",
            (claim["id"],),
        )
        connection.execute(
            "UPDATE provisioning_claims SET "
            "status='outcome_unknown',recovery_evidence=?,revision=revision+1 WHERE "
            "id=?",
            (str(evidence), claim["id"]),
        )
        plan = _row(connection, "provisioning_plans", claim["plan_id"])
        connection.execute(
            "UPDATE host_jobs SET status='outcome_unknown',recovery_evidence=?,revisi"
            "on=revision+1,updated_at=? WHERE id=? AND status='dispatch_started'",
            (str(evidence), _iso(now), plan["job_id"]),
        )
        connection.execute(
            "UPDATE environments SET "
            "status='outcome_unknown',reason_code='host_result_unknown',revision=revi"
            "sion+1,updated_at=? WHERE id=? AND status='provisioning'",
            (_iso(now), plan["environment_id"]),
        )
        self._event(connection, claim["id"], "outcome_unknown", now, evidence, actor="supervisor")

    def mark_unknown(self, token: str, value: UnknownInput | dict):
        value = UnknownInput.model_validate(value)
        now = self._now()
        with self.database.transaction() as connection:
            credential = self._authenticate(connection, token)
            claim = _row(connection, "provisioning_claims", value.claim_id)
            if (
                claim["provisioner_id"] != credential["id"]
                or claim["owner_id"] != str(value.owner_id)
                or claim["generation"] != value.generation
            ):
                raise ProvisioningError("provisioning_claim_fenced")
            effect = _row(connection, "provisioning_effects", value.effect_request_id)
            if effect["claim_id"] != claim["id"]:
                raise ProvisioningError("provisioning_claim_fenced")
            if claim["status"] == "outcome_unknown" and claim["recovery_evidence"] == str(
                value.client_request_id
            ):
                return self._claim_view(connection, claim)
            if claim["status"] != "dispatch_started" or effect["status"] != "dispatch_started":
                raise ProvisioningError("provisioning_operation_conflict")
            self._unknown(connection, claim, value.client_request_id, now)
            return self._claim_view(
                connection, _row(connection, "provisioning_claims", claim["id"])
            )

    def recover_expired(self, host_id: UUID, *, recovery_evidence: UUID):
        """Supervisor interno. Dispatch expirado conserva exclusividade indefinidamente."""
        host_id, recovery_evidence = UUID(str(host_id)), UUID(str(recovery_evidence))
        if not host_id.int or not recovery_evidence.int:
            raise ValueError("Identidade de recuperação inválida.")
        now = self._now()
        with self.database.transaction() as connection:
            claim_id = connection.execute(
                "SELECT id FROM provisioning_claims WHERE host_id=? AND status IN "
                "('claimed','dispatch_started','outcome_unknown')",
                (str(host_id),),
            ).get
            if claim_id is None:
                return None
            claim = _row(connection, "provisioning_claims", claim_id)
            if claim["status"] == "outcome_unknown":
                return self._claim_view(connection, claim)
            if now < claim["lease_expires_at"]:
                raise ProvisioningError("provisioning_owner_not_expired")
            if claim["status"] == "claimed":
                # Ainda não houve intent; a geração/estado impede o dono antigo de iniciar.
                connection.execute(
                    "UPDATE provisioning_claims SET status='aborted',revision=revision+1 "
                    "WHERE id=?",
                    (claim_id,),
                )
                self._event(
                    connection,
                    claim_id,
                    "claim_expired_without_dispatch",
                    now,
                    recovery_evidence,
                    actor="supervisor",
                )
            else:
                self._unknown(connection, claim, recovery_evidence, now)
            return self._claim_view(connection, _row(connection, "provisioning_claims", claim_id))

    def acknowledge_unknown(self, claim_id: UUID, value: AcknowledgeInput | dict):
        value = AcknowledgeInput.model_validate(value)
        now = self._now()
        with self.database.transaction() as connection:
            self._human(connection)
            claim = _row(connection, "provisioning_claims", claim_id)
            payload = value.model_dump(mode="json")
            if not self._command(
                connection, claim_id, value.client_request_id, "acknowledge_unknown", payload, now
            ):
                if claim["revision"] != value.expected_revision:
                    raise RevisionConflict("Journal de provisionamento mudou.")
                if claim["status"] != "outcome_unknown":
                    raise ProvisioningError("provisioning_ack_conflict")
                connection.execute(
                    "UPDATE provisioning_claims SET acknowledged_at=?,revision=revision+1 "
                    "WHERE id=?",
                    (now, str(claim_id)),
                )
                self._command(
                    connection,
                    claim_id,
                    value.client_request_id,
                    "acknowledge_unknown",
                    payload,
                    now,
                    save=True,
                )
                self._event(
                    connection, claim_id, "unknown_acknowledged", now, value.client_request_id
                )
            return self._claim_view(connection, _row(connection, "provisioning_claims", claim_id))
