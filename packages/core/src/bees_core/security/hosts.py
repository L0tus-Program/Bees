"""Pareamento humano de um runtime de diagnóstico com credencial própria.

Convites são emitidos somente pela instalação confiável. Esta fronteira não
despacha comandos, consulta o cofre ou consome pedidos de criação de computador.
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
from typing import Literal, Self
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator

from bees_core.storage.database import Database
from bees_core.storage.store import NotFoundError, RevisionConflict

_CODES = {
    "host_identity_required": "Configure sua identidade antes de conectar o hospedeiro.",
    "host_invite_already_issued": (
        "Convite já emitido; gere um novo convite para recuperar o pareamento."
    ),
    "host_invite_invalid": "Convite inválido, utilizado ou expirado.",
    "host_credentials_invalid": "Credencial do hospedeiro indisponível.",
    "host_pair_conflict": "Pareamento já registrado com outros dados.",
    "host_pair_expired": "Pareamento expirou; revogue este pedido e conecte novamente.",
    "host_fingerprint_mismatch": "O código não corresponde ao hospedeiro solicitado.",
    "host_active_exists": "Revogue o hospedeiro ativo antes de aprovar outro.",
    "host_not_pending": "Este pareamento não está aguardando confirmação.",
    "host_report_conflict": "Relatório obsoleto ou divergente; consulte a sessão novamente.",
}
_INVITE = re.compile(r"bi_[A-Za-z0-9_-]{43}\Z")
_CREDENTIAL = re.compile(r"bh_[A-Za-z0-9_-]{43}\Z")
_HOST_COLUMNS = (
    "host_id",
    "invite_id",
    "credential_hash",
    "fingerprint",
    "pair_request_id",
    "pair_request_hash",
    "status",
    "revision",
    "report_revision",
    "created_at",
    "pairing_expires_at",
    "paired_at",
    "revoked_at",
    "last_seen",
    "last_report_sequence",
    "last_report_id",
    "last_report_hash",
    "diagnostic_json",
)


class HostLinkError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code if code in _CODES else "host_credentials_invalid"
        super().__init__(_CODES[self.code])


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)


class HostPairInput(Contract):
    installation_id: UUID
    invite_token: SecretStr = Field(repr=False)
    host_id: UUID
    host_credential: SecretStr = Field(repr=False)
    client_request_id: UUID

    @field_validator("invite_token", "host_credential")
    @classmethod
    def valid_secret(cls, value: SecretStr, info) -> SecretStr:
        pattern = _INVITE if info.field_name == "invite_token" else _CREDENTIAL
        if pattern.fullmatch(value.get_secret_value()) is None:
            raise ValueError("Credencial fora do domínio de pareamento.")
        return value


class HostConfirmInput(Contract):
    expected_revision: int = Field(ge=1, strict=True)
    client_request_id: UUID
    fingerprint: str = Field(pattern=r"^[0-9A-F]{4}(?:-[0-9A-F]{4}){3}$")


class HostRevokeInput(Contract):
    expected_revision: int = Field(ge=1, strict=True)
    client_request_id: UUID


class HostReportInput(Contract):
    host_id: UUID
    sequence: int = Field(ge=1, le=2147483647, strict=True)
    expected_revision: int = Field(ge=0, le=2147483647, strict=True)
    client_request_id: UUID
    driver: Literal["hyperv", "libvirt"]
    probe: dict[str, bool] = Field(strict=True)

    @field_validator("probe", mode="before")
    @classmethod
    def strict_probe(cls, value):
        if (
            type(value) is not dict
            or len(value) != 3
            or any(type(v) is not bool for v in value.values())
        ):
            raise ValueError("Diagnóstico exige três verificações booleanas.")
        return value

    @model_validator(mode="after")
    def driver_probe(self) -> Self:
        keys = {"platform", "service", "module" if self.driver == "hyperv" else "kvm"}
        if self.probe.keys() != keys:
            raise ValueError("Verificações incompatíveis com o driver.")
        return self


@dataclass(frozen=True)
class IssuedHostInvite:
    installation_id: UUID
    invite_id: UUID
    invite_token: SecretStr = field(repr=False)
    expires_at: str


def _hash(domain: str, value: str) -> str:
    return hashlib.sha256(f"bees:host-link:{domain}:1:{value}".encode("ascii")).hexdigest()


def _digest(value: dict) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _iso(value: float | None) -> str | None:
    return datetime.fromtimestamp(value, UTC).isoformat() if value is not None else None


def pairing_fingerprint(
    installation_id: UUID, invite_id: UUID, host_id: UUID, credential: str
) -> str:
    if not isinstance(credential, str) or _CREDENTIAL.fullmatch(credential) is None:
        raise HostLinkError("host_credentials_invalid")
    raw = _hash(
        "fingerprint", f"{installation_id}:{invite_id}:{host_id}:{_hash('credential', credential)}"
    )[:16].upper()
    return "-".join(raw[i : i + 4] for i in range(0, 16, 4))


class HostService:
    INVITE_TTL = 300
    PAIR_TTL = 900
    REPORT_TTL = 60

    def __init__(self, database: Database, *, clock: Callable[[], float] = time.time) -> None:
        self.database = database
        self.clock = clock

    def _now(self) -> float:
        value = float(self.clock())
        if not math.isfinite(value) or value < 0:
            raise ValueError("Relógio inválido.")
        return value

    @staticmethod
    def _event(
        connection,
        entity_id: str,
        event_type: str,
        now: float,
        *,
        actor: str,
        correlation_id: str | None = None,
        payload: dict | None = None,
    ):
        connection.execute(
            "INSERT INTO "
            "domain_events(id,created_at,entity_type,entity_id,event_type,payload_json,"
            "actor,source,correlation_id) VALUES(?,?,?,?,?,?,?,?,?)",
            (
                str(uuid4()),
                _iso(now),
                "host_link",
                entity_id,
                "created"
                if event_type in ("host_invite_issued", "host_pair_requested")
                else "updated",
                json.dumps({"operation": event_type, **(payload or {})}, separators=(",", ":")),
                actor,
                "hosts",
                correlation_id,
            ),
        )

    @staticmethod
    def _installation(connection) -> str | None:
        return connection.execute(
            "SELECT installation_id FROM host_link_installation WHERE id=1"
        ).get

    @staticmethod
    def _row(connection, host_id: UUID | str) -> dict:
        row = connection.execute(
            f"SELECT {','.join(_HOST_COLUMNS)} FROM host_links WHERE host_id=?",
            (str(UUID(str(host_id))),),
        ).fetchone()
        if row is None:
            raise NotFoundError("Hospedeiro não encontrado.")
        return dict(zip(_HOST_COLUMNS, row, strict=True))

    def _view(self, connection, row: dict, now: float) -> dict:
        active = row["status"] == "active"
        online = (
            active
            and row["last_seen"] is not None
            and 0 <= now - row["last_seen"] < self.REPORT_TTL
        )
        return {
            "host_id": row["host_id"],
            "installation_id": self._installation(connection),
            "status": row["status"],
            "revision": row["revision"],
            "report_revision": row["report_revision"],
            "fingerprint": row["fingerprint"],
            "created_at": _iso(row["created_at"]),
            "paired_at": _iso(row["paired_at"]),
            "pairing_expires_at": _iso(row["pairing_expires_at"]),
            "last_seen": _iso(row["last_seen"]),
            "last_report_sequence": row["last_report_sequence"],
            "last_report_request_id": row["last_report_id"],
            "diagnostic": json.loads(row["diagnostic_json"])
            if online and row["diagnostic_json"]
            else None,
            "online": bool(online),
            "provisionable": False,
            "confirmable": row["status"] == "pending" and now < row["pairing_expires_at"],
        }

    def issue_invite(self, client_request_id: UUID) -> IssuedHostInvite:
        """Chamador é CLI do operador; nunca criar rota HTTP de emissão anônima."""
        request_id = str(UUID(str(client_request_id)))
        now = self._now()
        token = "bi_" + secrets.token_urlsafe(32)
        invite_id = uuid4()
        with self.database.transaction() as connection:
            if not connection.execute("SELECT EXISTS(SELECT 1 FROM identity_users)").get:
                raise HostLinkError("host_identity_required")
            if connection.execute(
                "SELECT EXISTS(SELECT 1 FROM host_link_invites WHERE client_request_id=?)",
                (request_id,),
            ).get:
                raise HostLinkError("host_invite_already_issued")
            installation_id = self._installation(connection)
            if installation_id is None:
                installation_id = str(uuid4())
                connection.execute(
                    "INSERT INTO host_link_installation VALUES(1,?,?)", (installation_id, now)
                )
            connection.execute(
                "UPDATE host_link_invites SET revoked_at=? WHERE revoked_at IS NULL "
                "AND consumed_at IS NULL",
                (now,),
            )
            connection.execute(
                "INSERT INTO "
                "host_link_invites(id,client_request_id,token_hash,created_at,expires_at) "
                "VALUES(?,?,?,?,?)",
                (str(invite_id), request_id, _hash("invite", token), now, now + self.INVITE_TTL),
            )
            self._event(
                connection,
                str(invite_id),
                "host_invite_issued",
                now,
                actor="operator",
                correlation_id=request_id,
            )
        return IssuedHostInvite(
            UUID(installation_id), invite_id, SecretStr(token), _iso(now + self.INVITE_TTL)
        )

    def pair(self, value: HostPairInput | dict) -> dict:
        value = HostPairInput.model_validate(value)
        now = self._now()
        credential_hash = _hash("credential", value.host_credential.get_secret_value())
        token_hash = _hash("invite", value.invite_token.get_secret_value())
        payload_hash = _digest(
            {
                "installation_id": str(value.installation_id),
                "host_id": str(value.host_id),
                "credential_hash": credential_hash,
                "invite_hash": token_hash,
                "request_id": str(value.client_request_id),
            }
        )
        with self.database.transaction() as connection:
            if str(value.installation_id) != self._installation(connection):
                raise HostLinkError("host_invite_invalid")
            invite = connection.execute(
                "SELECT id,expires_at,consumed_at,revoked_at FROM host_link_invites "
                "WHERE token_hash=?",
                (token_hash,),
            ).fetchone()
            if invite is None or invite[3] is not None:
                raise HostLinkError("host_invite_invalid")
            if invite[2] is not None:
                try:
                    row = self._row(connection, value.host_id)
                except NotFoundError:
                    raise HostLinkError("host_pair_conflict") from None
                if (
                    row["invite_id"] != invite[0]
                    or row["pair_request_hash"] != payload_hash
                    or row["status"] == "revoked"
                ):
                    raise HostLinkError("host_pair_conflict")
                if row["status"] == "pending" and now >= invite[1]:
                    raise HostLinkError("host_invite_invalid")
                return self._view(connection, row, now)
            if now >= invite[1]:
                raise HostLinkError("host_invite_invalid")
            if connection.execute(
                "SELECT EXISTS(SELECT 1 FROM host_links WHERE host_id=? OR "
                "credential_hash=? OR pair_request_id=?)",
                (str(value.host_id), credential_hash, str(value.client_request_id)),
            ).get:
                raise HostLinkError("host_pair_conflict")
            fingerprint = pairing_fingerprint(
                value.installation_id,
                UUID(invite[0]),
                value.host_id,
                value.host_credential.get_secret_value(),
            )
            connection.execute(
                "INSERT INTO "
                "host_links(host_id,invite_id,credential_hash,fingerprint,pair_request_id,"
                "pair_request_hash,status,created_at,pairing_expires_at) "
                "VALUES(?,?,?,?,?,?,'pending',?,?)",
                (
                    str(value.host_id),
                    invite[0],
                    credential_hash,
                    fingerprint,
                    str(value.client_request_id),
                    payload_hash,
                    now,
                    now + self.PAIR_TTL,
                ),
            )
            connection.execute(
                "UPDATE host_link_invites SET consumed_at=? WHERE id=? AND consumed_at IS NULL",
                (now, invite[0]),
            )
            self._event(
                connection,
                str(value.host_id),
                "host_pair_requested",
                now,
                actor="host",
                correlation_id=str(value.client_request_id),
                payload={"status": "pending"},
            )
            return self._view(connection, self._row(connection, value.host_id), now)

    def list(self, limit: int = 100, offset: int = 0) -> list[dict]:
        if (
            type(limit) is not int
            or not 1 <= limit <= 1000
            or type(offset) is not int
            or offset < 0
        ):
            raise ValueError("Paginação inválida.")
        now = self._now()
        with self.database.transaction(write=False) as connection:
            ids = list(
                connection.execute(
                    "SELECT host_id FROM host_links ORDER BY created_at DESC,host_id "
                    "DESC LIMIT ? OFFSET ?",
                    (limit, offset),
                )
            )
            return [self._view(connection, self._row(connection, row[0]), now) for row in ids]

    def get(self, host_id: UUID) -> dict:
        now = self._now()
        with self.database.transaction(write=False) as connection:
            return self._view(connection, self._row(connection, host_id), now)

    def _command(
        self, host_id: UUID, value: HostConfirmInput | HostRevokeInput, command: str
    ) -> dict:
        now = self._now()
        payload_hash = _digest({"command": command, **value.model_dump(mode="json")})
        with self.database.transaction() as connection:
            row = self._row(connection, host_id)
            receipt = connection.execute(
                "SELECT request_hash FROM host_link_commands WHERE host_id=? AND "
                "client_request_id=?",
                (str(host_id), str(value.client_request_id)),
            ).get
            if receipt is not None:
                if receipt != payload_hash:
                    raise HostLinkError("host_pair_conflict")
                return self._view(connection, row, now)
            if row["revision"] != value.expected_revision:
                raise RevisionConflict("Pareamento mudou; atualize antes de decidir.")
            if command == "confirm":
                if row["status"] != "pending":
                    raise HostLinkError("host_not_pending")
                if now >= row["pairing_expires_at"]:
                    raise HostLinkError("host_pair_expired")
                if not secrets.compare_digest(row["fingerprint"], value.fingerprint):
                    raise HostLinkError("host_fingerprint_mismatch")
                if connection.execute(
                    "SELECT EXISTS(SELECT 1 FROM host_links WHERE status='active')"
                ).get:
                    raise HostLinkError("host_active_exists")
                connection.execute(
                    "UPDATE host_links SET "
                    "status='active',revision=revision+1,paired_at=? "
                    "WHERE host_id=? AND revision=?",
                    (now, str(host_id), value.expected_revision),
                )
            else:
                if row["status"] == "revoked":
                    raise HostLinkError("host_credentials_invalid")
                connection.execute(
                    "UPDATE host_links SET "
                    "status='revoked',revision=revision+1,revoked_at=? "
                    "WHERE host_id=? AND revision=?",
                    (now, str(host_id), value.expected_revision),
                )
            if connection.changes() != 1:
                raise RevisionConflict("Pareamento mudou durante a decisão.")
            connection.execute(
                "INSERT INTO host_link_commands VALUES(?,?,?,?,?)",
                (str(host_id), str(value.client_request_id), command, payload_hash, now),
            )
            self._event(
                connection,
                str(host_id),
                f"host_{command}",
                now,
                actor="user",
                correlation_id=str(value.client_request_id),
                payload={"revision": row["revision"] + 1},
            )
            return self._view(connection, self._row(connection, host_id), now)

    def confirm(self, host_id: UUID, value: HostConfirmInput | dict) -> dict:
        return self._command(host_id, HostConfirmInput.model_validate(value), "confirm")

    def revoke(self, host_id: UUID, value: HostRevokeInput | dict) -> dict:
        return self._command(host_id, HostRevokeInput.model_validate(value), "revoke")

    def _authenticated(self, connection, token: str, now: float, active_only: bool = False) -> dict:
        if not isinstance(token, str) or _CREDENTIAL.fullmatch(token) is None:
            raise HostLinkError("host_credentials_invalid")
        host_id = connection.execute(
            "SELECT host_id FROM host_links WHERE credential_hash=?", (_hash("credential", token),)
        ).get
        if host_id is None:
            raise HostLinkError("host_credentials_invalid")
        row = self._row(connection, host_id)
        if (
            row["status"] == "revoked"
            or (active_only and row["status"] != "active")
            or (row["status"] == "pending" and now >= row["pairing_expires_at"])
        ):
            raise HostLinkError("host_credentials_invalid")
        return row

    def authenticate(self, token: str, active_only: bool = False) -> dict:
        now = self._now()
        with self.database.transaction(write=False) as connection:
            return self._view(
                connection, self._authenticated(connection, token, now, active_only), now
            )

    def session(self, token: str) -> dict:
        return self.authenticate(token)

    def status_for_host(self, token: str) -> dict:
        return self.authenticate(token)

    def report(self, token: str, value: HostReportInput | dict) -> dict:
        value = HostReportInput.model_validate(value)
        now = self._now()
        payload_hash = _digest(value.model_dump(mode="json"))
        with self.database.transaction() as connection:
            row = self._authenticated(connection, token, now, active_only=True)
            if row["host_id"] != str(value.host_id):
                raise HostLinkError("host_credentials_invalid")
            receipt = connection.execute(
                "SELECT request_hash FROM host_link_report_receipts "
                "WHERE host_id=? AND client_request_id=?",
                (row["host_id"], str(value.client_request_id)),
            ).get
            if receipt is not None:
                if receipt != payload_hash:
                    raise HostLinkError("host_report_conflict")
                return self._view(connection, row, now)
            if (
                value.sequence != row["last_report_sequence"] + 1
                or value.expected_revision != row["report_revision"]
            ):
                raise HostLinkError("host_report_conflict")
            diagnostic = {
                "driver": value.driver,
                "probe": value.probe,
                "status": "guest_bridge_required"
                if all(value.probe.values())
                else "driver_unavailable",
                "provisionable": False,
            }
            encoded = json.dumps(diagnostic, sort_keys=True, separators=(",", ":"))
            connection.execute(
                "UPDATE host_links SET "
                "report_revision=report_revision+1,last_seen=?,last_report_sequence=?,"
                "last_report_id=?,last_report_hash=?,diagnostic_json=? "
                "WHERE host_id=? AND status='active' AND report_revision=?",
                (
                    now,
                    value.sequence,
                    str(value.client_request_id),
                    payload_hash,
                    encoded,
                    row["host_id"],
                    value.expected_revision,
                ),
            )
            if connection.changes() != 1:
                raise HostLinkError("host_report_conflict")
            connection.execute(
                "INSERT INTO host_link_report_receipts VALUES(?,?,?,?,?)",
                (row["host_id"], str(value.client_request_id), payload_hash, value.sequence, now),
            )
            # Heartbeats iguais não tornam o ledger uma série temporal ilimitada.
            if row["diagnostic_json"] != encoded:
                self._event(
                    connection,
                    row["host_id"],
                    "host_diagnostic_changed",
                    now,
                    actor="host",
                    correlation_id=str(value.client_request_id),
                    payload=diagnostic,
                )
            return self._view(connection, self._row(connection, row["host_id"]), now)

    def paired_status(self) -> dict | None:
        now = self._now()
        with self.database.transaction(write=False) as connection:
            host_id = connection.execute("SELECT host_id FROM host_links WHERE status='active'").get
            if host_id is None:
                return None
            view = self._view(connection, self._row(connection, host_id), now)
            return view["diagnostic"] if view["online"] else None
