"""Executores remotos pareados: runtime de computadores e conector pessoal.

Fronteiras:
- O executor só recebe comandos já autorizados pelo core (``dispatch_started``
  confirmado). Ele não lê o banco nem recebe credenciais de modelos.
- Credencial própria ``be_`` (256 bits), guardada somente como SHA-256. Convite de
  uso único e curto cria o executor; revogação encerra novos comandos.
- Comandos são duráveis (``executor_commands``). Um comando que não foi enviado
  até o prazo expira sem efeito; um comando enviado sem resposta vira desconhecido.
"""

import asyncio
import hashlib
import json
import secrets
import time
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID, uuid4, uuid5

from pydantic import BaseModel, ConfigDict, Field, field_validator

from bees_core.agent_tools.catalog import ToolSpec
from bees_core.agent_tools.runners import Outcome
from bees_core.models import utc_now
from bees_core.storage.database import Database
from bees_core.storage.store import NotFoundError, RevisionConflict, StateStore
from bees_core.tools import ToolError

ONLINE_SECONDS = 45
INVITE_SECONDS = 900
CREDENTIAL_PREFIX = "be_"
Kind = Literal["computer_runtime", "personal"]
DESKTOP_ACTIONS = ("browser.",)


class ExecutorError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _now() -> datetime:
    return utc_now()


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="microseconds")


def _parse(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


class _Input(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)


class InviteInput(_Input):
    kind: Kind
    name: str = Field(min_length=1, max_length=100)

    @field_validator("name")
    @classmethod
    def clean(cls, value):
        value = value.strip()
        if not value or any(ord(c) < 32 for c in value):
            raise ValueError("Nome inválido.")
        return value


class ExchangeInput(_Input):
    token: str = Field(min_length=20, max_length=200)
    version: str = Field(default="1", max_length=40)
    platform: str = Field(default="", max_length=80)


class ComputerInput(_Input):
    client_request_id: UUID
    name: str = Field(default="Computador da abelha", min_length=1, max_length=100)


class ControlInput(_Input):
    expected_revision: int = Field(ge=1, strict=True)
    holder: Literal["agent", "human"]


class RevisionInput(_Input):
    expected_revision: int = Field(ge=1, strict=True)


class GrantInput(_Input):
    expected_revision: int = Field(ge=0, strict=True)
    enabled: bool
    roots: tuple[str, ...] = Field(default=(), max_length=20)
    write: bool = False
    shell: bool = False
    browser: bool = False

    @field_validator("roots")
    @classmethod
    def clean_roots(cls, value):
        for root in value:
            if not root or len(root) > 1024 or "\x00" in root:
                raise ValueError("Raiz inválida.")
        return value


def _row(cursor, columns):
    row = cursor.fetchone()
    return dict(zip(columns, row, strict=True)) if row else None


EXECUTOR_COLUMNS = (
    "id",
    "created_at",
    "updated_at",
    "revision",
    "metadata_json",
    "kind",
    "name",
    "status",
    "credential_hash",
    "last_seen_at",
    "connection_id",
)
COMPUTER_COLUMNS = (
    "id",
    "created_at",
    "updated_at",
    "revision",
    "metadata_json",
    "agent_id",
    "executor_id",
    "name",
    "status",
    "control_holder",
    "control_revision",
    "reason_code",
)
COMMAND_COLUMNS = (
    "id",
    "created_at",
    "updated_at",
    "executor_id",
    "computer_id",
    "agent_id",
    "tool_name",
    "arguments_json",
    "deadline_at",
    "status",
    "result_json",
    "error_code",
    "sent_at",
    "finished_at",
)


def _connection(unit):
    return unit.agents._context.connection


def _select(unit, table, columns, where, params):
    cursor = _connection(unit).execute(
        f"SELECT {','.join(columns)} FROM {table} WHERE {where}", params
    )
    return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]


def online(executor: dict, now: datetime | None = None) -> bool:
    seen = _parse(executor.get("last_seen_at"))
    return (
        executor["status"] == "active"
        and executor.get("connection_id") is not None
        and seen is not None
        and (now or _now()) - seen <= timedelta(seconds=ONLINE_SECONDS)
    )


def executor_view(executor: dict, now: datetime | None = None) -> dict:
    metadata = json.loads(executor["metadata_json"])
    return {
        "id": executor["id"],
        "kind": executor["kind"],
        "name": executor["name"],
        "status": executor["status"],
        "revision": executor["revision"],
        "online": online(executor, now),
        "last_seen_at": executor["last_seen_at"],
        "version": metadata.get("version"),
        "platform": metadata.get("platform"),
        "capabilities": metadata.get("capabilities", []),
        "roots": metadata.get("roots", []),
        "created_at": executor["created_at"],
    }


def computer_view(
    computer: dict, executor: dict | None = None, now: datetime | None = None
) -> dict:
    return {
        "id": computer["id"],
        "agent_id": computer["agent_id"],
        "executor_id": computer["executor_id"],
        "name": computer["name"],
        "status": computer["status"],
        "revision": computer["revision"],
        "control_holder": computer["control_holder"],
        "control_revision": computer["control_revision"],
        "reason_code": computer["reason_code"],
        "online": bool(executor and online(executor, now)),
        "created_at": computer["created_at"],
        "updated_at": computer["updated_at"],
    }


class ExecutorService:
    def __init__(self, database: Database, *, clock=_now):
        self.store = StateStore(database)
        self.clock = clock

    # --------------------------------------------------------- pareamento

    def invite(self, value: InviteInput | dict) -> dict:
        value = InviteInput.model_validate(value)
        token = secrets.token_urlsafe(32)
        now = self.clock()
        invite_id = str(uuid4())
        expires = now + timedelta(seconds=INVITE_SECONDS)
        with self.store.transaction(actor="user", source="executor_invite") as unit:
            _connection(unit).execute(
                "INSERT INTO executor_invites(id,created_at,kind,name,token_hash,expires_at) "
                "VALUES(?,?,?,?,?,?)",
                (invite_id, _iso(now), value.kind, value.name, _hash(token), _iso(expires)),
            )
        return {"invite_id": invite_id, "token": token, "expires_at": _iso(expires)}

    def exchange(self, value: ExchangeInput | dict) -> dict:
        value = ExchangeInput.model_validate(value)
        now = self.clock()
        credential = CREDENTIAL_PREFIX + secrets.token_urlsafe(32)
        with self.store.transaction(actor="executor", source="executor_exchange") as unit:
            invite = _row(
                _connection(unit).execute(
                    "SELECT id,kind,name,expires_at,used_at FROM executor_invites "
                    "WHERE token_hash=?",
                    (_hash(value.token),),
                ),
                ("id", "kind", "name", "expires_at", "used_at"),
            )
            if invite is None or invite["used_at"] or _parse(invite["expires_at"]) <= now:
                raise ExecutorError("executor_invite_invalid")
            executor_id = str(uuid4())
            metadata = {"version": value.version, "platform": value.platform}
            _connection(unit).execute(
                "INSERT INTO executors(id,created_at,updated_at,metadata_json,kind,name,status,"
                "credential_hash) VALUES(?,?,?,?,?,?,'active',?)",
                (
                    executor_id,
                    _iso(now),
                    _iso(now),
                    json.dumps(metadata),
                    invite["kind"],
                    invite["name"],
                    _hash(credential),
                ),
            )
            _connection(unit).execute(
                "UPDATE executor_invites SET used_at=?,executor_id=? WHERE id=?",
                (_iso(now), executor_id, invite["id"]),
            )
        return {"executor_id": executor_id, "credential": credential, "kind": invite["kind"]}

    def authenticate(self, credential: str) -> dict:
        if not isinstance(credential, str) or not credential.startswith(CREDENTIAL_PREFIX):
            raise ExecutorError("executor_credentials_invalid")
        with self.store.transaction(write=False) as unit:
            executor = _row(
                _connection(unit).execute(
                    f"SELECT {','.join(EXECUTOR_COLUMNS)} FROM executors WHERE credential_hash=?",
                    (_hash(credential),),
                ),
                EXECUTOR_COLUMNS,
            )
        if executor is None or executor["status"] != "active":
            raise ExecutorError("executor_credentials_invalid")
        return executor

    def touch(self, executor_id: str, connection_id: str, metadata: dict | None = None) -> bool:
        """Heartbeat do dono atual da conexão; outra conexão substitui a anterior."""
        with self.store.transaction(actor="executor", source="executor_heartbeat") as unit:
            executor = self._executor(unit, executor_id)
            if executor["status"] != "active":
                return False
            current = json.loads(executor["metadata_json"])
            if metadata:
                current |= {
                    key: metadata[key]
                    for key in ("version", "platform", "capabilities", "roots")
                    if key in metadata
                }
            _connection(unit).execute(
                "UPDATE executors SET last_seen_at=?,connection_id=?,metadata_json=?,"
                "updated_at=? WHERE id=?",
                (
                    _iso(self.clock()),
                    connection_id,
                    json.dumps(current),
                    _iso(self.clock()),
                    executor_id,
                ),
            )
            return True

    def owns_connection(self, executor_id: str, connection_id: str) -> bool:
        with self.store.transaction(write=False) as unit:
            executor = self._executor(unit, executor_id)
            return executor["status"] == "active" and executor["connection_id"] == connection_id

    def disconnect(self, executor_id: str, connection_id: str) -> None:
        with self.store.transaction(actor="executor", source="executor_disconnect") as unit:
            _connection(unit).execute(
                "UPDATE executors SET connection_id=NULL WHERE id=? AND connection_id=?",
                (executor_id, connection_id),
            )

    @staticmethod
    def _executor(unit, executor_id) -> dict:
        found = _select(unit, "executors", EXECUTOR_COLUMNS, "id=?", (str(executor_id),))
        if not found:
            raise NotFoundError("Executor não encontrado.")
        return found[0]

    def list(self, kind: str | None = None) -> list[dict]:
        with self.store.transaction(write=False) as unit:
            rows = _select(
                unit,
                "executors",
                EXECUTOR_COLUMNS,
                "(? IS NULL OR kind=?) ORDER BY created_at DESC",
                (kind, kind),
            )
        return [executor_view(row, self.clock()) for row in rows]

    def revoke(self, executor_id: UUID, value: RevisionInput | dict) -> dict:
        value = RevisionInput.model_validate(value)
        with self.store.transaction(actor="user", source="executor_revoke") as unit:
            executor = self._executor(unit, executor_id)
            if executor["revision"] != value.expected_revision:
                raise RevisionConflict("Executor mudou; atualize a tela.")
            _connection(unit).execute(
                "UPDATE executors SET status='revoked',connection_id=NULL,revision=revision+1,"
                "updated_at=? WHERE id=?",
                (_iso(self.clock()), str(executor_id)),
            )
            # Comandos ainda não enviados nunca chegam a um executor revogado.
            _connection(unit).execute(
                "UPDATE executor_commands SET status='expired',error_code='executor_revoked',"
                "updated_at=?,finished_at=? WHERE executor_id=? AND status='queued'",
                (_iso(self.clock()), _iso(self.clock()), str(executor_id)),
            )
            if executor["kind"] == "computer_runtime":
                _connection(unit).execute(
                    "UPDATE computers SET status='failed',reason_code='executor_revoked',"
                    "revision=revision+1,updated_at=? WHERE executor_id=? "
                    "AND status IN ('provisioning','ready','removing')",
                    (_iso(self.clock()), str(executor_id)),
                )
            return executor_view(self._executor(unit, executor_id))

    # --------------------------------------------------------- computadores

    @staticmethod
    def _computer(unit, computer_id) -> dict:
        found = _select(unit, "computers", COMPUTER_COLUMNS, "id=?", (str(computer_id),))
        if not found:
            raise NotFoundError("Computador não encontrado.")
        return found[0]

    def _owned_computer(self, unit, agent_id, computer_id) -> dict:
        computer = self._computer(unit, computer_id)
        if computer["agent_id"] != str(agent_id):
            raise NotFoundError("Computador não encontrado nesta abelha.")
        return computer

    def computers(self, agent_id: UUID) -> list[dict]:
        with self.store.transaction(write=False) as unit:
            if unit.agents.get(agent_id) is None:
                raise NotFoundError("Abelha não encontrada.")
            rows = _select(
                unit,
                "computers",
                COMPUTER_COLUMNS,
                "agent_id=? ORDER BY created_at DESC LIMIT 20",
                (str(agent_id),),
            )
            return [
                computer_view(row, self._executor(unit, row["executor_id"]), self.clock())
                for row in rows
            ]

    def create_computer(self, agent_id: UUID, value: ComputerInput | dict) -> dict:
        value = ComputerInput.model_validate(value)
        now = self.clock()
        with self.store.transaction(actor="user", source="computer_create") as unit:
            agent = unit.agents.get(agent_id)
            if agent is None:
                raise NotFoundError("Abelha não encontrada.")
            prior = _select(
                unit, "computers", COMPUTER_COLUMNS, "id=?", (str(value.client_request_id),)
            )
            if prior:
                if prior[0]["agent_id"] != str(agent_id):
                    raise ExecutorError("idempotency_conflict")
                return computer_view(prior[0], self._executor(unit, prior[0]["executor_id"]))
            active = _select(
                unit,
                "computers",
                COMPUTER_COLUMNS,
                "agent_id=? AND status!='removed'",
                (str(agent_id),),
            )
            if active:
                raise ExecutorError("computer_exists")
            runtimes = [
                row
                for row in _select(
                    unit,
                    "executors",
                    EXECUTOR_COLUMNS,
                    "kind='computer_runtime' AND status='active' ORDER BY last_seen_at DESC",
                    (),
                )
                if online(row, now)
            ]
            if not runtimes:
                raise ExecutorError("computer_runtime_offline")
            _connection(unit).execute(
                "INSERT INTO computers(id,created_at,updated_at,agent_id,executor_id,name,status) "
                "VALUES(?,?,?,?,?,?,'provisioning')",
                (
                    str(value.client_request_id),
                    _iso(now),
                    _iso(now),
                    str(agent_id),
                    runtimes[0]["id"],
                    value.name,
                ),
            )
            created = self._computer(unit, value.client_request_id)
            return computer_view(created, runtimes[0])

    def remove_computer(self, agent_id: UUID, computer_id: UUID, value: RevisionInput | dict):
        value = RevisionInput.model_validate(value)
        with self.store.transaction(actor="user", source="computer_remove") as unit:
            computer = self._owned_computer(unit, agent_id, computer_id)
            if computer["revision"] != value.expected_revision:
                raise RevisionConflict("Computador mudou; atualize a tela.")
            status = "removed" if computer["status"] == "failed" else "removing"
            if computer["status"] in ("removing", "removed"):
                return computer_view(computer)
            _connection(unit).execute(
                "UPDATE computers SET status=?,revision=revision+1,updated_at=? WHERE id=?",
                (status, _iso(self.clock()), str(computer_id)),
            )
            return computer_view(self._computer(unit, computer_id))

    def set_control(self, agent_id: UUID, computer_id: UUID, value: ControlInput | dict) -> dict:
        """Controle humano exclusivo suspende ações visuais do agente até a devolução."""
        value = ControlInput.model_validate(value)
        with self.store.transaction(actor="user", source="computer_control") as unit:
            computer = self._owned_computer(unit, agent_id, computer_id)
            if computer["control_revision"] != value.expected_revision:
                raise RevisionConflict("Controle mudou; atualize a tela.")
            if computer["status"] != "ready":
                raise ExecutorError("computer_unavailable")
            if computer["control_holder"] != value.holder:
                _connection(unit).execute(
                    "UPDATE computers SET control_holder=?,control_revision=control_revision+1,"
                    "revision=revision+1,updated_at=? WHERE id=?",
                    (value.holder, _iso(self.clock()), str(computer_id)),
                )
            return computer_view(
                self._computer(unit, computer_id),
                self._executor(unit, computer["executor_id"]),
            )

    def runtime_computers(self, executor_id: str) -> list[dict]:
        with self.store.transaction(write=False) as unit:
            return _select(
                unit,
                "computers",
                COMPUTER_COLUMNS,
                "executor_id=? AND status IN ('provisioning','ready','removing')",
                (executor_id,),
            )

    def report_computer(self, executor_id: str, computer_id: str, status: str, reason=None):
        if status not in ("ready", "failed", "removed"):
            raise ExecutorError("executor_protocol_invalid")
        with self.store.transaction(actor="executor", source="computer_report") as unit:
            computer = self._computer(unit, computer_id)
            if computer["executor_id"] != executor_id:
                raise ExecutorError("executor_protocol_invalid")
            allowed = {
                "provisioning": ("ready", "failed"),
                "ready": ("failed",),
                "removing": ("removed",),
            }.get(computer["status"], ())
            if status not in allowed:
                return computer_view(computer)
            _connection(unit).execute(
                "UPDATE computers SET status=?,reason_code=?,revision=revision+1,updated_at=? "
                "WHERE id=?",
                (status, reason, _iso(self.clock()), computer_id),
            )
            return computer_view(self._computer(unit, computer_id))

    # --------------------------------------------------------- concessões pessoais

    def grants(self, executor_id: UUID) -> list[dict]:
        with self.store.transaction(write=False) as unit:
            rows = (
                _connection(unit)
                .execute(
                    "SELECT id,agent_id,enabled,scope_json,revision FROM executor_grants "
                    "WHERE executor_id=?",
                    (str(executor_id),),
                )
                .fetchall()
            )
        return [
            {
                "id": row[0],
                "agent_id": row[1],
                "enabled": bool(row[2]),
                "scope": json.loads(row[3]),
                "revision": row[4],
            }
            for row in rows
        ]

    def set_grant(self, executor_id: UUID, agent_id: UUID, value: GrantInput | dict) -> dict:
        value = GrantInput.model_validate(value)
        now = _iso(self.clock())
        scope = {
            "roots": list(value.roots),
            "write": value.write,
            "shell": value.shell,
            "browser": value.browser,
        }
        with self.store.transaction(actor="user", source="executor_grant") as unit:
            executor = self._executor(unit, executor_id)
            if executor["kind"] != "personal" or executor["status"] != "active":
                raise ExecutorError("executor_unavailable")
            if unit.agents.get(agent_id) is None:
                raise NotFoundError("Abelha não encontrada.")
            row = (
                _connection(unit)
                .execute(
                    "SELECT id,revision FROM executor_grants WHERE executor_id=? AND agent_id=?",
                    (str(executor_id), str(agent_id)),
                )
                .fetchone()
            )
            if row is None:
                if value.expected_revision != 0:
                    raise RevisionConflict("Concessão inexistente exige revisão zero.")
                grant_id = str(uuid4())
                _connection(unit).execute(
                    "INSERT INTO executor_grants(id,created_at,updated_at,executor_id,agent_id,"
                    "enabled,scope_json) VALUES(?,?,?,?,?,?,?)",
                    (
                        grant_id,
                        now,
                        now,
                        str(executor_id),
                        str(agent_id),
                        int(value.enabled),
                        json.dumps(scope),
                    ),
                )
            else:
                if row[1] != value.expected_revision:
                    raise RevisionConflict("Concessão mudou; atualize a tela.")
                grant_id = row[0]
                _connection(unit).execute(
                    "UPDATE executor_grants SET enabled=?,scope_json=?,revision=revision+1,"
                    "updated_at=? WHERE id=?",
                    (int(value.enabled), json.dumps(scope), now, grant_id),
                )
            return next(item for item in self._grants(unit, executor_id) if item["id"] == grant_id)

    @staticmethod
    def _grants(unit, executor_id):
        return [
            {
                "id": row[0],
                "agent_id": row[1],
                "enabled": bool(row[2]),
                "scope": json.loads(row[3]),
                "revision": row[4],
            }
            for row in _connection(unit)
            .execute(
                "SELECT id,agent_id,enabled,scope_json,revision FROM executor_grants "
                "WHERE executor_id=?",
                (str(executor_id),),
            )
            .fetchall()
        ]


# ------------------------------------------------------------- alvos das ferramentas


def _personal_grant(unit, agent_id):
    rows = (
        _connection(unit)
        .execute(
            "SELECT g.id,g.executor_id,g.scope_json,g.revision FROM executor_grants g "
            "JOIN executors e ON e.id=g.executor_id WHERE g.agent_id=? AND g.enabled=1 "
            "AND e.status='active' AND e.kind='personal' ORDER BY g.created_at LIMIT 1",
            (str(agent_id),),
        )
        .fetchone()
    )
    return rows


PERSONAL_SCOPE_REQUIREMENTS = {
    "write": ("files.write", "files.delete", "office.convert"),
    "shell": ("shell.run",),
    "browser": ("browser.",),
}


def resolve_target(unit, agent_id, spec: ToolSpec):
    from bees_core.agent_tools.service import ToolTarget

    if spec.target == "computer":
        rows = _select(
            unit,
            "computers",
            COMPUTER_COLUMNS,
            "agent_id=? AND status='ready'",
            (str(agent_id),),
        )
        if not rows:
            raise ToolError("environment_unavailable", "Computador próprio indisponível.")
        computer = rows[0]
        return ToolTarget(
            "computer",
            computer["id"],
            f"computer:{computer['id']}",
            "bee_account",
            computer["executor_id"],
        )
    if spec.target == "personal":
        row = _personal_grant(unit, agent_id)
        if row is None:
            raise ToolError("environment_unavailable", "Máquina pessoal não concedida.")
        grant_id, executor_id, scope_json, _ = row
        scope = json.loads(scope_json)
        suffix = spec.name.removeprefix("personal.")
        for requirement, prefixes in PERSONAL_SCOPE_REQUIREMENTS.items():
            if any(suffix.startswith(prefix) for prefix in prefixes) and not scope.get(requirement):
                raise ToolError("environment_unavailable", "Recurso pessoal não concedido.")
        if suffix.startswith("files.") and not scope.get("roots"):
            raise ToolError("environment_unavailable", "Nenhuma pasta pessoal concedida.")
        return ToolTarget(
            "personal",
            executor_id,
            f"personal:{executor_id}:{grant_id}",
            "user_account",
            executor_id,
        )
    raise ToolError("environment_unavailable", "Alvo desconhecido.")


def require_online(unit, agent_id, spec: ToolSpec, target) -> None:
    executor = _select(unit, "executors", EXECUTOR_COLUMNS, "id=?", (target.executor_id,))
    if not executor or not online(executor[0]):
        raise ToolError("executor_offline", "Executor desconectado.")
    if spec.target == "computer":
        computer = _select(unit, "computers", COMPUTER_COLUMNS, "id=?", (target.environment_id,))
        if not computer or computer[0]["status"] != "ready":
            raise ToolError("environment_unavailable", "Computador indisponível.")
        if computer[0]["control_holder"] == "human" and spec.name.startswith("computer.browser."):
            # Durante controle humano (ex.: login privado) o agente não observa nem age na tela.
            raise ToolError("human_control", "Usuário está no controle da tela.")


def personal_scope(unit, agent_id) -> dict | None:
    row = _personal_grant(unit, agent_id)
    return json.loads(row[2]) if row else None


# ------------------------------------------------------------- fila de comandos


class CommandQueue:
    def __init__(self, database: Database, *, clock=_now):
        self.store = StateStore(database)
        self.clock = clock

    def enqueue(self, unit, effect, spec: ToolSpec, arguments, target) -> None:
        now = self.clock()
        payload = arguments.model_dump(mode="json")
        agent_id = effect.metadata["intent"]["agent_id"]
        if spec.target == "personal":
            payload = {"arguments": payload, "scope": personal_scope(unit, agent_id) or {}}
        else:
            payload = {"arguments": payload}
        _connection(unit).execute(
            "INSERT INTO executor_commands(id,created_at,updated_at,executor_id,computer_id,"
            "agent_id,tool_name,arguments_json,deadline_at,status) "
            "VALUES(?,?,?,?,?,?,?,?,?,'queued')",
            (
                str(effect.id),
                _iso(now),
                _iso(now),
                target.executor_id,
                target.environment_id if spec.target == "computer" else None,
                agent_id,
                spec.name,
                json.dumps(payload, ensure_ascii=False),
                _iso(now + timedelta(seconds=spec.timeout_seconds)),
            ),
        )

    def claim(self, executor_id: str, connection_id: str, limit: int = 8) -> list[dict]:
        """Somente a conexão atual recebe comandos; prazo vencido expira sem envio."""
        now = self.clock()
        with self.store.transaction(actor="executor", source="executor_dispatch") as unit:
            executor = _select(unit, "executors", EXECUTOR_COLUMNS, "id=?", (executor_id,))
            if (
                not executor
                or executor[0]["status"] != "active"
                or executor[0]["connection_id"] != connection_id
            ):
                return []
            _connection(unit).execute(
                "UPDATE executor_commands SET status='expired',error_code='executor_deadline',"
                "updated_at=?,finished_at=? WHERE executor_id=? AND status='queued' "
                "AND deadline_at<=?",
                (_iso(now), _iso(now), executor_id, _iso(now)),
            )
            rows = _select(
                unit,
                "executor_commands",
                COMMAND_COLUMNS,
                "executor_id=? AND status='queued' ORDER BY created_at LIMIT ?",
                (executor_id, limit),
            )
            for row in rows:
                _connection(unit).execute(
                    "UPDATE executor_commands SET status='sent',sent_at=?,updated_at=? "
                    "WHERE id=? AND status='queued'",
                    (_iso(now), _iso(now), row["id"]),
                )
            return rows

    def report(self, executor_id: str, command_id: str, status: str, result=None, code=None):
        if status not in ("confirmed", "failed", "unknown"):
            raise ExecutorError("executor_protocol_invalid")
        now = self.clock()
        with self.store.transaction(actor="executor", source="executor_result") as unit:
            rows = _select(unit, "executor_commands", COMMAND_COLUMNS, "id=?", (command_id,))
            if not rows or rows[0]["executor_id"] != executor_id:
                raise ExecutorError("executor_protocol_invalid")
            if rows[0]["status"] != "sent":
                return False
            _connection(unit).execute(
                "UPDATE executor_commands SET status=?,result_json=?,error_code=?,updated_at=?,"
                "finished_at=? WHERE id=? AND status='sent'",
                (
                    status,
                    json.dumps(result, ensure_ascii=False) if result is not None else None,
                    code,
                    _iso(now),
                    _iso(now),
                    command_id,
                ),
            )
            return True

    def get(self, command_id) -> dict | None:
        with self.store.transaction(write=False) as unit:
            rows = _select(unit, "executor_commands", COMMAND_COLUMNS, "id=?", (str(command_id),))
            return rows[0] if rows else None

    def settle_timeout(self, command_id) -> dict | None:
        """Prazo do worker: queued expira sem efeito; sent vira desconhecido."""
        now = self.clock()
        with self.store.transaction(actor="worker", source="executor_timeout") as unit:
            rows = _select(unit, "executor_commands", COMMAND_COLUMNS, "id=?", (str(command_id),))
            if not rows:
                return None
            row = rows[0]
            if row["status"] == "queued":
                _connection(unit).execute(
                    "UPDATE executor_commands SET status='expired',error_code='executor_offline',"
                    "updated_at=?,finished_at=? WHERE id=?",
                    (_iso(now), _iso(now), row["id"]),
                )
            elif row["status"] == "sent":
                _connection(unit).execute(
                    "UPDATE executor_commands SET status='unknown',error_code='executor_timeout',"
                    "updated_at=?,finished_at=? WHERE id=?",
                    (_iso(now), _iso(now), row["id"]),
                )
            return _select(unit, "executor_commands", COMMAND_COLUMNS, "id=?", (row["id"],))[0]

    async def wait(self, command_id, timeout: float, poll: float = 0.2) -> dict:
        deadline = time.monotonic() + timeout
        while True:
            row = self.get(command_id)
            if row is None:
                raise ExecutorError("executor_command_missing")
            if row["status"] in ("confirmed", "failed", "unknown", "expired"):
                return row
            if time.monotonic() >= deadline:
                return self.settle_timeout(command_id)
            await asyncio.sleep(poll)


def outcome(row: dict) -> Outcome:
    result = json.loads(row["result_json"]) if row.get("result_json") else {}
    if row["status"] == "confirmed":
        return Outcome("confirmed", result if isinstance(result, dict) else {"value": result})
    if row["status"] in ("failed", "expired"):
        detail = result.get("detail") if isinstance(result, dict) else None
        return Outcome("failed", code=row["error_code"] or "tool_failed", detail=detail)
    return Outcome("unknown", code=row["error_code"] or "executor_result_unknown")


class GatewayRunner:
    """Executor remoto: comando nasce no commit do início e o worker aguarda o recibo."""

    def __init__(self, database: Database):
        self.database = database
        self.queue = CommandQueue(database)

    def on_begin(self, unit, effect, spec, arguments, target):
        self.queue.enqueue(unit, effect, spec, arguments, target)

    async def run(self, spec, arguments, context, target):
        row = await self.queue.wait(context.action_id, spec.timeout_seconds + 10)
        result = outcome(row)
        if result.status == "confirmed" and spec.name.endswith(".files.publish"):
            return publish_remote_file(spec, arguments, context, result)
        return result


def publish_remote_file(spec, arguments, context, result: Outcome) -> Outcome:
    import base64

    from bees_core.artifacts import ArtifactError

    data = result.result
    try:
        content = base64.b64decode(data["content_b64"], validate=True)
        media_type = data["media_type"]
        name = arguments.name or data["name"]
    except KeyError, ValueError, TypeError:
        return Outcome("failed", code="executor_result_invalid")
    from bees_core.artifacts import BINARY_MEDIA_TYPES, MEDIA_TYPES

    if media_type not in MEDIA_TYPES and media_type not in BINARY_MEDIA_TYPES:
        return Outcome("failed", code="artifact_media_type_unsupported")
    if context.artifacts is None:
        return Outcome("failed", code="artifact_storage_unavailable")
    try:
        artifact = context.artifacts.create(
            context.agent_id,
            context.task_id,
            name=name,
            media_type=media_type,
            content=content,
            run_id=context.run_id,
            artifact_id=uuid5(context.action_id, "bees.agent-tool.artifact/v1"),
            metadata={
                "agent_tool_action_id": str(context.action_id),
                "source_path": data.get("path"),
            },
        )
    except ArtifactError as error:
        return Outcome("failed", code=error.code)
    return Outcome(
        "confirmed",
        {
            "artifact_id": str(artifact.id),
            "name": artifact.name,
            "media_type": artifact.media_type,
            "size_bytes": artifact.size_bytes,
            "sha256": artifact.sha256,
        },
    )
