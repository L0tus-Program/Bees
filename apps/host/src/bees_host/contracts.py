"""Contratos locais e respostas limitadas; nenhuma entrada contém comandos remotos."""

import hashlib
from datetime import UTC, datetime
from typing import Literal
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    StrictBool,
    field_validator,
    model_validator,
)


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)


def local_origin(value: str) -> str:
    parsed = urlsplit(value)
    authority = str(parsed.hostname)
    if parsed.port is not None:
        authority += ":" + str(parsed.port)
    if (
        parsed.scheme != "http"
        or parsed.hostname not in ("localhost", "127.0.0.1")
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in ("", "/")
        or any(c.isspace() or ord(c) < 32 for c in value)
        or parsed.netloc != authority
        or value not in ("http://" + authority, "http://" + authority + "/")
        or parsed.port is not None
        and not 1 <= parsed.port <= 65535
    ):
        raise ValueError("Origem local inválida.")
    return value.rstrip("/")


class Bootstrap(Contract):
    origin: str
    installation_id: UUID
    invite_id: UUID
    invite_token: SecretStr = Field(repr=False)
    expires_at: AwareDatetime

    @field_validator("origin")
    @classmethod
    def valid_origin(cls, value: str) -> str:
        return local_origin(value)

    @field_validator("invite_token")
    @classmethod
    def valid_token(cls, value: SecretStr) -> SecretStr:
        import re

        if re.fullmatch(r"bi_[A-Za-z0-9_-]{43}", value.get_secret_value()) is None:
            raise ValueError("Convite inválido.")
        return value

    def require_unexpired(self) -> None:
        if self.expires_at <= datetime.now(UTC):
            raise ValueError("Convite expirado.")


class Report(Contract):
    host_id: UUID
    sequence: int = Field(ge=1, strict=True)
    expected_revision: int = Field(ge=0, strict=True)
    client_request_id: UUID
    driver: Literal["hyperv", "libvirt"]
    probe: dict[str, StrictBool]

    @field_validator("probe", mode="before")
    @classmethod
    def strict_bools(cls, value: dict) -> dict:
        if any(type(v) is not bool for v in value.values()):
            raise ValueError("Diagnóstico inválido.")
        return value

    @model_validator(mode="after")
    def driver_probe(self):
        if set(self.probe) != {
            "platform",
            "service",
            "module" if self.driver == "hyperv" else "kvm",
        }:
            raise ValueError("Diagnóstico incompatível.")
        return self


class Diagnostic(Contract):
    driver: Literal["hyperv", "libvirt"]
    status: Literal["driver_unavailable", "guest_bridge_required"]
    probe: dict[str, StrictBool]
    provisionable: Literal[False]


class Session(Contract):
    host_id: UUID
    installation_id: UUID
    status: Literal["pending", "active", "revoked"]
    revision: int = Field(ge=1, strict=True)
    report_revision: int = Field(ge=0, strict=True)
    fingerprint: str = Field(pattern=r"^[A-F0-9]{4}(?:-[A-F0-9]{4}){3}$")
    created_at: AwareDatetime
    paired_at: AwareDatetime | None
    pairing_expires_at: AwareDatetime
    last_seen: AwareDatetime | None
    diagnostic: Diagnostic | None
    online: bool = Field(strict=True)
    provisionable: Literal[False]
    confirmable: bool = Field(strict=True)
    last_report_sequence: int = Field(ge=0, strict=True)
    last_report_request_id: UUID | None


class HostState(Contract):
    version: Literal[1] = 1
    origin: str
    installation_id: UUID
    host_id: UUID
    expected_fingerprint: str = Field(pattern=r"^[A-F0-9]{4}(?:-[A-F0-9]{4}){3}$")
    host_credential: SecretStr = Field(repr=False)
    pair_request_id: UUID
    invite_token: SecretStr | None = Field(default=None, repr=False)
    invite_expires_at: AwareDatetime | None = None
    registered: bool = False
    revoked: bool = False
    expired: bool = False
    confirmed: bool = False
    pairing_expires_at: AwareDatetime | None = None
    pending_report: Report | None = None

    @field_validator("origin")
    @classmethod
    def valid_origin(cls, value: str) -> str:
        return local_origin(value)

    @field_validator("host_credential")
    @classmethod
    def valid_token(cls, value: SecretStr) -> SecretStr:
        import re

        if re.fullmatch(r"bh_[A-Za-z0-9_-]{43}", value.get_secret_value()) is None:
            raise ValueError("Credencial inválida.")
        return value


def pairing_fingerprint(
    installation_id: UUID, invite_id: UUID, host_id: UUID, credential: str
) -> str:
    """Prova local independente, mesma versão/domínios do contrato do servidor."""
    hashed = hashlib.sha256(
        ("bees:host-link:credential:1:" + credential).encode("ascii")
    ).hexdigest()
    raw = (
        hashlib.sha256(
            (
                f"bees:host-link:fingerprint:1:{installation_id}:{invite_id}:{host_id}:{hashed}"
            ).encode("ascii")
        )
        .hexdigest()[:16]
        .upper()
    )
    return "-".join(raw[i : i + 4] for i in range(0, 16, 4))
