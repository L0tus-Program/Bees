"""Pedidos humanos de computador e journal durável, sem provisionador implícito.

A configuração do host pertence ao operador. O catálogo descreve um alvo, não
uma imagem instalada. Este checkpoint não inicia VMs nem divulga acesso ao host.
"""

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator

from bees_core.models import Environment, HostJob
from bees_core.security.hosts import HostService
from bees_core.storage.database import Database
from bees_core.storage.store import NotFoundError, RevisionConflict, StateStore


class EnvironmentError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)


class EnvironmentCreateInput(Contract):
    name: str = Field(min_length=1, max_length=100)
    template_id: Literal["linux-desktop-v1"] = "linux-desktop-v1"
    cpu_count: int = Field(default=2, ge=1, le=4, strict=True)
    memory_mib: int = Field(default=4096, ge=2048, le=8192, strict=True)
    disk_gib: int = Field(default=30, ge=20, le=100, strict=True)
    client_request_id: UUID

    @field_validator("name")
    @classmethod
    def useful_name(cls, value: str) -> str:
        if value != value.strip() or any(ord(c) < 32 or ord(c) == 127 for c in value):
            raise ValueError("Nome exige texto sem controles ou espaços externos.")
        return value


class EnvironmentCancelInput(Contract):
    expected_revision: int = Field(ge=1, strict=True)
    client_request_id: UUID


class HostConfiguration(Contract):
    """Somente instalação/operador: não aceitar como payload humano ou de LLM."""

    driver: Literal["none", "hyperv", "libvirt"] = "none"
    image_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    guest_bridge_ready: Literal[False] = False


def _read_command(argv: list[str], timeout: float = 5.0) -> str | None:
    """Comandos estáticos, sem shell, stderr privado e saída limitada na leitura."""
    try:
        with tempfile.TemporaryFile() as output:
            process = subprocess.Popen(argv, stdout=output, stderr=subprocess.DEVNULL, shell=False)
            try:
                process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
                return None
            if process.returncode != 0:
                return None
            output.seek(0)
            raw = output.read(8193)
            if len(raw) > 8192:
                return None
            return raw.decode("utf-8", errors="strict")
    except OSError, UnicodeError:
        return None


def preflight_host(configuration: HostConfiguration | None = None) -> dict:
    """Diagnóstico nativo somente leitura; nunca habilita serviço ou instala apps."""
    config = configuration or HostConfiguration()
    probe = None
    status = "host_setup_required"
    if config.driver == "hyperv":
        probe = {"platform": os.name == "nt", "module": False, "service": False}
        executable = shutil.which("powershell.exe") if probe["platform"] else None
        if executable:
            # Sem interpolação de nomes, caminhos ou entradas do usuário.
            result = _read_command(
                [
                    executable,
                    "-NoLogo",
                    "-NoProfile",
                    "-NonInteractive",
                    "-Command",
                    "$ErrorActionPreference='Stop';"
                    "$module=[bool](Get-Module -ListAvailable Hyper-V);"
                    "$service=Get-Service vmms -ErrorAction SilentlyContinue;"
                    "@{module=$module;service=[bool]($service -and $service.Status -eq 'Running')}"
                    "|ConvertTo-Json -Compress",
                ]
            )
            try:
                data = json.loads(result or "null")
                if isinstance(data, dict) and all(
                    type(data.get(k)) is bool for k in ("module", "service")
                ):
                    probe.update({k: data[k] for k in ("module", "service")})
            except ValueError:
                pass
        status = "guest_bridge_required" if all(probe.values()) else "driver_unavailable"
    elif config.driver == "libvirt":
        probe = {"platform": os.name == "posix", "kvm": False, "service": False}
        if probe["platform"]:
            probe["kvm"] = os.access("/dev/kvm", os.R_OK | os.W_OK)
            executable = shutil.which("virsh")
            if executable:
                probe["service"] = (
                    _read_command([executable, "--connect", "qemu:///system", "uri"])
                    == "qemu:///system\n"
                )
        status = "guest_bridge_required" if all(probe.values()) else "driver_unavailable"
    return {"driver": config.driver, "status": status, "probe": probe, "provisionable": False}


class EnvironmentService:
    def __init__(
        self,
        database: Database,
        host_config: HostConfiguration | None = None,
        *,
        host_service: HostService | None = None,
    ) -> None:
        self.store = StateStore(database)
        self.host_config = host_config or HostConfiguration()
        self.host_service = host_service or HostService(database)

    def host_status(self) -> dict:
        if self.host_config.driver == "none":
            paired = self.host_service.paired_status()
            if paired is not None:
                return paired
        return preflight_host(self.host_config)

    def catalog(self) -> dict:
        return {
            "templates": [
                {
                    "id": "linux-desktop-v1",
                    "name": "Computador Linux",
                    "version": 1,
                    "os": "linux",
                    "applications": ["chromium", "libreoffice"],
                    "status": "planned",
                    "provisionable": False,
                    "defaults": {"cpu_count": 2, "memory_mib": 4096, "disk_gib": 30},
                    "limits": {
                        "cpu_count": {"min": 1, "max": 4},
                        "memory_mib": {"min": 2048, "max": 8192},
                        "disk_gib": {"min": 20, "max": 100},
                    },
                }
            ],
            "host": self.host_status(),
        }

    @staticmethod
    def _agent(unit, agent_id: UUID):
        agent = unit.agents.get(agent_id)
        if agent is None:
            raise NotFoundError("Abelha não encontrada.")
        return agent

    @staticmethod
    def _owned(unit, agent_id: UUID, environment_id: UUID) -> Environment:
        EnvironmentService._agent(unit, agent_id)
        value = unit.environments.get(environment_id)
        if value is None or value.agent_id != agent_id:
            raise NotFoundError("Computador não encontrado para esta abelha.")
        return value

    def request(self, agent_id: UUID, value: EnvironmentCreateInput | dict) -> Environment:
        value = EnvironmentCreateInput.model_validate(value)
        payload = value.model_dump(mode="json")
        request_hash = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        reason = self.host_status()["status"]
        with self.store.transaction(
            source="environments", correlation_id=str(value.client_request_id)
        ) as unit:
            agent = self._agent(unit, agent_id)
            previous = unit.environments.request(agent_id, value.client_request_id)
            if previous is not None:
                if previous.request_hash != request_hash:
                    raise EnvironmentError(
                        "environment_conflict", "Pedido já registrado com outros dados."
                    )
                return previous
            if agent.status != "active":
                raise EnvironmentError(
                    "environment_agent_inactive", "Ative a abelha antes de solicitar um computador."
                )
            environment = unit.environments.create(
                Environment(
                    agent_id=agent_id, request_hash=request_hash, reason_code=reason, **payload
                )
            )
            unit.host_jobs.create(HostJob(environment_id=environment.id, correlation_id=uuid4()))
            return environment

    def list(self, agent_id: UUID, limit: int = 100, offset: int = 0) -> list[Environment]:
        with self.store.transaction(write=False) as unit:
            self._agent(unit, agent_id)
            return unit.environments.list(agent_id, limit, offset)

    def get(self, agent_id: UUID, environment_id: UUID) -> Environment:
        with self.store.transaction(write=False) as unit:
            return self._owned(unit, agent_id, environment_id)

    def cancel(
        self, agent_id: UUID, environment_id: UUID, value: EnvironmentCancelInput | dict
    ) -> Environment:
        value = EnvironmentCancelInput.model_validate(value)
        with self.store.transaction(
            source="environments", correlation_id=str(value.client_request_id)
        ) as unit:
            current = self._owned(unit, agent_id, environment_id)
            receipt = {
                "client_request_id": str(value.client_request_id),
                "expected_revision": value.expected_revision,
            }
            if current.metadata.get("cancel_receipt") == receipt:
                return current
            if current.metadata.get("cancel_receipt", {}).get("client_request_id") == str(
                value.client_request_id
            ):
                raise EnvironmentError(
                    "environment_conflict", "Cancelamento já registrado com outros dados."
                )
            if current.revision != value.expected_revision:
                raise RevisionConflict("O computador mudou; atualize antes de cancelar.")
            job = unit.host_jobs.for_environment(current.id)
            if current.status != "awaiting_host" or job is None or job.status != "awaiting_host":
                raise EnvironmentError(
                    "environment_not_cancellable",
                    "Só um pedido ainda não iniciado pode ser cancelado.",
                )
            unit.host_jobs.update(job.model_copy(update={"status": "cancelled"}), job.revision)
            return unit.environments.update(
                current.model_copy(
                    update={
                        "status": "cancelled",
                        "reason_code": "cancelled_by_user",
                        "metadata": current.metadata | {"cancel_receipt": receipt},
                    }
                ),
                current.revision,
            )

    def view(self, record: Environment) -> dict:
        with self.store.transaction(write=False) as unit:
            current = self._owned(unit, record.agent_id, record.id)
            job = unit.host_jobs.for_environment(current.id)
            return current.model_dump(
                mode="json", exclude={"metadata", "request_hash", "client_request_id"}
            ) | {
                "operation_id": str(job.id) if job else None,
                "operation_status": job.status if job else None,
                "usable": False,
            }

    def recover_interrupted(
        self, job_id: UUID, *, expected_revision: int, owner_id: UUID, recovery_evidence: UUID
    ) -> HostJob:
        """Supervisor confiável após interrupção comprovada; não exposto à API/modelo.

        O checkpoint não produz dispatch_started. Este contrato protege journals
        importados de coordenadores posteriores; não cria executor ou retry.
        """
        if type(expected_revision) is not int or expected_revision < 1:
            raise ValueError("Revisão de recuperação exige inteiro positivo.")
        owner_id, recovery_evidence = UUID(str(owner_id)), UUID(str(recovery_evidence))
        with self.store.transaction(
            actor="supervisor", source="environment_recovery", correlation_id=str(recovery_evidence)
        ) as unit:
            job = unit.host_jobs.get(job_id)
            if job is None:
                raise NotFoundError("Operação do host não encontrada.")
            if (
                job.status == "outcome_unknown"
                and job.owner_id == owner_id
                and job.recovery_evidence == recovery_evidence
            ):
                return job
            if job.revision != expected_revision:
                raise RevisionConflict("Operação do host mudou.")
            environment = unit.environments.get(job.environment_id)
            if (
                job.status != "dispatch_started"
                or job.owner_id != owner_id
                or environment.status != "provisioning"
            ):
                raise EnvironmentError(
                    "environment_recovery_conflict",
                    "Recuperação exige despacho do dono interrompido.",
                )
            recovered = unit.host_jobs.update(
                job.model_copy(
                    update={
                        "status": "outcome_unknown",
                        "recovery_evidence": recovery_evidence,
                    }
                ),
                job.revision,
            )
            unit.environments.update(
                environment.model_copy(
                    update={
                        "status": "outcome_unknown",
                        "reason_code": "host_result_unknown",
                    }
                ),
                environment.revision,
            )
            return recovered
