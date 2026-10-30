"""Vínculos de canal externo e o worker que recebe/encaminha mensagens.

Fronteiras:
- O token do bot fica no cofre; o banco guarda só a referência e o hash do código
  de pareamento (uso único, 30 minutos). Só o chat que enviou o código conversa.
- Cada entrada externa é registrada uma vez (``channel_events``) antes de qualquer
  efeito; reprocessar após reinício não gera nova geração.
- Saída: respostas da abelha na conversa vinculada (chat, agente, rotinas) e avisos
  de decisões pendentes. Decisões continuam somente na interface do Bees.
- A entrega de saída é "pelo menos uma vez": uma queda entre o envio e o registro
  pode repetir a última mensagem, nunca uma geração.
"""

import asyncio
import hashlib
import json
import logging
import secrets
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID, uuid5

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from bees_core.channels.adapters import ChannelAdapter, ChannelError, Identity, Inbound, adapters
from bees_core.models import utc_now
from bees_core.storage.database import Database
from bees_core.storage.store import NotFoundError, RevisionConflict, StateStore

log = logging.getLogger(__name__)
PAIRING_SECONDS = 1800
CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
Kind = Literal["telegram", "discord"]
Mode = Literal["chat", "agent", "research"]
MODE_WORDS = {
    "chat": "chat",
    "conversa": "chat",
    "agente": "agent",
    "agent": "agent",
    "pesquisa": "research",
    "research": "research",
}
HELP = (
    "Comandos:\n"
    "/modo conversa — respostas diretas do modelo\n"
    "/modo agente — a abelha usa ferramentas (web, computador)\n"
    "/modo pesquisa — pesquisa com fontes\n"
    "/status — modo atual\n"
    "Decisões sobre ações sensíveis são tomadas na interface do Bees."
)
ERRORS = {
    "budget_exhausted": "O limite de consumo desta abelha foi atingido.",
    "global_stop": "O Bees está em parada global; nada foi executado.",
    "agent_inactive": "Esta abelha está inativa.",
    "policy_denied": "Uma regra de autonomia bloqueou esta ação.",
    "policy_approval_required": "Esta ação precisa de aprovação na interface do Bees.",
    "unsupported_capability": "O modelo desta abelha não suporta ferramentas; use /modo conversa.",
    "rate_limited": "O provedor do modelo pediu para aguardar. Tente em instantes.",
}


class ChannelServiceError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="microseconds")


def normalize_code(text: str) -> str | None:
    words = text.strip().split()
    if not words:
        return None
    if words[0].lower().split("@")[0] == "/start" and len(words) == 2:
        candidate = words[1]
    elif len(words) == 1:
        candidate = words[0]
    else:
        return None
    candidate = candidate.upper().replace("-", "")
    return candidate if len(candidate) == 8 and all(c in CODE_ALPHABET for c in candidate) else None


class _Input(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)


class CreateChannel(_Input):
    client_request_id: UUID
    kind: Kind
    name: str = Field(default="", max_length=100)
    token: SecretStr = Field(min_length=20, max_length=200, repr=False)
    target: str | None = Field(default=None, max_length=64)
    mode: Mode = "chat"
    conversation_id: UUID | None = None

    @field_validator("target")
    @classmethod
    def digits(cls, value):
        if value is not None and (not value.isdigit() or len(value) > 25):
            raise ValueError("Canal Discord deve ser um ID numérico.")
        return value

    @field_validator("token")
    @classmethod
    def printable(cls, value):
        raw = value.get_secret_value()
        if raw != raw.strip() or any(ord(c) <= 32 or ord(c) > 126 for c in raw):
            raise ValueError("Token inválido.")
        return value


class UpdateChannel(_Input):
    expected_revision: int = Field(ge=1)
    mode: Mode | None = None
    paused: bool | None = None


class RevisionInput(_Input):
    expected_revision: int = Field(ge=1)


COLUMNS = (
    "id,created_at,updated_at,revision,metadata_json,agent_id,conversation_id,kind,name,"
    "status,mode,secret_ref,target,pairing_hash,pairing_expires_at,chat_id,peer_name,"
    "inbound_cursor,outbound_after,reason_code"
)


def _row(values) -> dict:
    row = dict(zip(COLUMNS.split(","), values, strict=True))
    row["metadata"] = json.loads(row.pop("metadata_json"))
    return row


def view(row: dict) -> dict:
    meta = row["metadata"]
    return {
        "id": row["id"],
        "agent_id": row["agent_id"],
        "conversation_id": row["conversation_id"],
        "kind": row["kind"],
        "name": row["name"],
        "status": row["status"],
        "mode": row["mode"],
        "target": row["target"],
        "paired": row["chat_id"] is not None,
        "peer_name": row["peer_name"],
        "bot_username": meta.get("bot_username"),
        "pairing_expires_at": row["pairing_expires_at"],
        "reason_code": row["reason_code"],
        "last_activity_at": meta.get("last_activity_at"),
        "revision": row["revision"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


class ChannelService:
    def __init__(self, database: Database, clock: Callable[[], datetime] = utc_now) -> None:
        self.database = database
        self.store = StateStore(database)
        self.clock = clock

    # Leitura -----------------------------------------------------------------
    def _get(self, connection, link_id, agent_id=None) -> dict:
        values = connection.execute(
            f"SELECT {COLUMNS} FROM channel_links WHERE id=?", (str(link_id),)
        ).fetchone()
        if values is None:
            raise NotFoundError("Canal não encontrado.")
        row = _row(values)
        if agent_id is not None and row["agent_id"] != str(agent_id):
            raise NotFoundError("Canal não encontrado.")
        return row

    def get(self, link_id, agent_id=None) -> dict:
        with self.database.transaction(write=False) as connection:
            return self._get(connection, link_id, agent_id)

    def list(self, agent_id) -> list[dict]:
        with self.database.transaction(write=False) as connection:
            rows = connection.execute(
                f"SELECT {COLUMNS} FROM channel_links WHERE agent_id=? AND status!='revoked' "
                "ORDER BY created_at DESC LIMIT 50",
                (str(agent_id),),
            ).fetchall()
        return [_row(values) for values in rows]

    def runnable(self) -> list[dict]:
        with self.database.transaction(write=False) as connection:
            rows = connection.execute(
                f"SELECT {COLUMNS} FROM channel_links WHERE status IN ('pending','active')"
            ).fetchall()
        return [_row(values) for values in rows]

    # Escrita humana ----------------------------------------------------------
    def find_request(self, client_request_id: UUID) -> dict | None:
        with self.database.transaction(write=False) as connection:
            values = connection.execute(
                f"SELECT {COLUMNS} FROM channel_links "
                "WHERE json_extract(metadata_json,'$.client_request_id')=?",
                (str(client_request_id),),
            ).fetchone()
        return _row(values) if values else None

    def create(
        self, agent_id, value: CreateChannel, *, secret_ref: str, identity: Identity
    ) -> tuple[dict, str]:
        if value.kind == "discord" and not value.target:
            raise ChannelServiceError("channel_target_invalid")
        code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(8))
        now = self.clock()
        with self.store.transaction(source="channel_create") as unit:
            agent = unit.agents.get(agent_id)
            if agent is None:
                raise NotFoundError("Abelha não encontrada.")
            if value.conversation_id is not None:
                conversation = unit.conversations.get(value.conversation_id)
                if conversation is None or conversation.agent_id != agent.id:
                    raise NotFoundError("Conversa não encontrada.")
            else:
                conversations = [
                    item
                    for item in unit.conversations.list(agent_id=agent.id, limit=50)
                    if not item.metadata.get("execution_kind")
                ]
                if not conversations:
                    raise ChannelServiceError("channel_conversation_missing")
                conversation = conversations[0]
            connection = unit.agents._context.connection
            link_id = uuid5(value.client_request_id, "bees.channel-link/v1")
            connection.execute(
                f"INSERT INTO channel_links({COLUMNS}) "
                "VALUES(?,?,?,1,?,?,?,?,?,'pending',?,?,?,?,?,NULL,NULL,'0',?,NULL)",
                (
                    str(link_id),
                    _iso(now),
                    _iso(now),
                    json.dumps(
                        {
                            "client_request_id": str(value.client_request_id),
                            "bot_id": identity.id,
                            "bot_username": identity.username,
                        }
                    ),
                    str(agent.id),
                    str(conversation.id),
                    value.kind,
                    (value.name.strip() or identity.name or value.kind)[:100],
                    value.mode,
                    secret_ref,
                    value.target,
                    _hash(code),
                    _iso(now + timedelta(seconds=PAIRING_SECONDS)),
                    _iso(now),
                ),
            )
            return self._get(connection, link_id), code

    def _mutate(self, agent_id, link_id, expected_revision, source, change) -> dict:
        with self.store.transaction(source=source) as unit:
            connection = unit.agents._context.connection
            row = self._get(connection, link_id, agent_id)
            if row["revision"] != expected_revision:
                raise RevisionConflict("Canal alterado; atualize a tela.")
            if row["status"] == "revoked":
                raise ChannelServiceError("channel_revoked")
            fields = change(row)
            fields["updated_at"] = _iso(self.clock())
            assignments = ",".join(f"{key}=?" for key in fields)
            connection.execute(
                f"UPDATE channel_links SET {assignments},revision=revision+1 "
                "WHERE id=? AND revision=?",
                (*fields.values(), row["id"], expected_revision),
            )
            return self._get(connection, link_id)

    def update(self, agent_id, link_id, value: UpdateChannel) -> dict:
        def change(row):
            fields = {}
            if value.mode is not None:
                fields["mode"] = value.mode
            if value.paused is True and row["status"] in ("pending", "active"):
                fields["status"] = "paused"
            if value.paused is False and row["status"] == "paused":
                fields["status"] = "active" if row["chat_id"] else "pending"
            return fields

        return self._mutate(agent_id, link_id, value.expected_revision, "channel_update", change)

    def new_code(self, agent_id, link_id, value: RevisionInput) -> tuple[dict, str]:
        code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(8))

        def change(row):
            if row["status"] == "failed":
                raise ChannelServiceError("channel_failed")
            return {
                "pairing_hash": _hash(code),
                "pairing_expires_at": _iso(self.clock() + timedelta(seconds=PAIRING_SECONDS)),
            }

        return (
            self._mutate(agent_id, link_id, value.expected_revision, "channel_code", change),
            code,
        )

    def revoke(self, agent_id, link_id, value: RevisionInput) -> dict:
        return self._mutate(
            agent_id,
            link_id,
            value.expected_revision,
            "channel_revoke",
            lambda row: {"status": "revoked", "pairing_hash": None, "pairing_expires_at": None},
        )

    # Operações do worker -----------------------------------------------------
    def _system_update(self, link_id, **fields) -> None:
        fields["updated_at"] = _iso(self.clock())
        assignments = ",".join(f"{key}=?" for key in fields)
        with self.database.transaction() as connection:
            connection.execute(
                f"UPDATE channel_links SET {assignments} WHERE id=? AND status!='revoked'",
                (*fields.values(), str(link_id)),
            )

    def set_cursor(self, link_id, cursor: str) -> None:
        self._system_update(link_id, inbound_cursor=cursor)

    def note(self, link_id, reason: str | None) -> None:
        self._system_update(link_id, reason_code=reason)

    def fail(self, link_id, reason: str) -> None:
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE channel_links SET status='failed',reason_code=?,updated_at=?,"
                "revision=revision+1 WHERE id=? AND status IN ('pending','active')",
                (reason, _iso(self.clock()), str(link_id)),
            )

    def begin_inbound(self, link_id, external_id: str) -> bool:
        with self.database.transaction() as connection:
            if connection.execute(
                "SELECT 1 FROM channel_events WHERE link_id=? AND direction='in' AND external_id=?",
                (str(link_id), external_id),
            ).fetchone():
                return False
            connection.execute(
                "INSERT INTO channel_events(link_id,direction,external_id,created_at,status) "
                "VALUES(?,'in',?,?,'received')",
                (str(link_id), external_id, _iso(self.clock())),
            )
            return True

    def finish_inbound(self, link_id, external_id, status, message_id=None) -> None:
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE channel_events SET status=?,message_id=? "
                "WHERE link_id=? AND direction='in' AND external_id=?",
                (status, message_id, str(link_id), external_id),
            )
            connection.execute(
                "UPDATE channel_links SET metadata_json=json_set(metadata_json,"
                "'$.last_activity_at',?) WHERE id=?",
                (_iso(self.clock()), str(link_id)),
            )

    def pair(self, link_id, code: str, chat_id: str, peer_name: str) -> bool:
        now = self.clock()
        with self.database.transaction() as connection:
            row = self._get(connection, link_id)
            if (
                row["status"] not in ("pending", "active")
                or row["pairing_hash"] is None
                or row["pairing_expires_at"] is None
                or datetime.fromisoformat(row["pairing_expires_at"]) <= now
                or not secrets.compare_digest(row["pairing_hash"], _hash(code))
            ):
                return False
            connection.execute(
                "UPDATE channel_links SET status='active',chat_id=?,peer_name=?,pairing_hash=NULL,"
                "pairing_expires_at=NULL,reason_code=NULL,outbound_after=?,updated_at=?,"
                "revision=revision+1 WHERE id=?",
                (chat_id, peer_name[:200] or None, _iso(now), _iso(now), row["id"]),
            )
            return True

    def set_mode(self, link_id, mode: str) -> None:
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE channel_links SET mode=?,updated_at=?,revision=revision+1 "
                "WHERE id=? AND status='active'",
                (mode, _iso(self.clock()), str(link_id)),
            )

    def outbound(self, link: dict, limit: int = 10) -> list[tuple[str, str, str]]:
        if not link["outbound_after"]:
            return []
        with self.database.transaction(write=False) as connection:
            return connection.execute(
                "SELECT m.id,m.content,m.created_at FROM messages m WHERE m.conversation_id=? "
                "AND m.role='assistant' AND m.content!='' AND m.created_at>=? AND NOT EXISTS("
                "SELECT 1 FROM channel_events e WHERE e.link_id=? AND e.direction='out' "
                "AND e.external_id=m.id) ORDER BY m.created_at,m.id LIMIT ?",
                (link["conversation_id"], link["outbound_after"], link["id"], limit),
            ).fetchall()

    def notified(self, link_id, key: str) -> bool:
        with self.database.transaction(write=False) as connection:
            return (
                connection.execute(
                    "SELECT 1 FROM channel_events WHERE link_id=? AND direction='out' "
                    "AND external_id=?",
                    (str(link_id), key),
                ).fetchone()
                is not None
            )

    def begin_outbound(self, link_id, key: str) -> bool:
        """Registra a saída antes do envio; queda no meio nunca reenvia."""
        with self.database.transaction() as connection:
            if connection.execute(
                "SELECT 1 FROM channel_events WHERE link_id=? AND direction='out' "
                "AND external_id=?",
                (str(link_id), key),
            ).fetchone():
                return False
            connection.execute(
                "INSERT INTO channel_events(link_id,direction,external_id,created_at,status) "
                "VALUES(?,'out',?,?,'sending')",
                (str(link_id), key, _iso(self.clock())),
            )
            return True

    def abandon_outbound(self, link_id, key: str, *, uncertain: bool) -> None:
        """Falha antes da entrega libera nova tentativa; falha incerta fica registrada."""
        with self.database.transaction() as connection:
            if uncertain:
                connection.execute(
                    "UPDATE channel_events SET status='unknown' WHERE link_id=? "
                    "AND direction='out' AND external_id=? AND status='sending'",
                    (str(link_id), key),
                )
            else:
                connection.execute(
                    "DELETE FROM channel_events WHERE link_id=? AND direction='out' "
                    "AND external_id=? AND status='sending'",
                    (str(link_id), key),
                )

    def mark_sent(self, link_id, key: str, *, after: str | None = None) -> None:
        with self.database.transaction() as connection:
            connection.execute(
                "INSERT INTO channel_events(link_id,direction,external_id,created_at,status) "
                "VALUES(?,'out',?,?,'sent') ON CONFLICT(link_id,direction,external_id) "
                "DO UPDATE SET status='sent'",
                (str(link_id), key, _iso(self.clock())),
            )
            if after is not None:
                connection.execute(
                    "UPDATE channel_links SET outbound_after=max(coalesce(outbound_after,''),?) "
                    "WHERE id=?",
                    (after, str(link_id)),
                )


class ChannelWorker:
    """Uma tarefa por vínculo ativo/pendente: polling de entrada e envio de saída."""

    def __init__(
        self,
        database: Database,
        resolver,
        *,
        adapters_by_kind: dict[str, ChannelAdapter] | None = None,
        provider_transport=None,
        clock: Callable[[], datetime] = utc_now,
        app_url: str | None = None,
        reconcile_seconds: float = 3.0,
        flush_seconds: float = 2.0,
    ) -> None:
        self.database = database
        self.resolver = resolver
        self.service = ChannelService(database, clock)
        self.adapters = adapters_by_kind or adapters()
        self.provider_transport = provider_transport
        self.app_url = app_url
        self.reconcile_seconds = reconcile_seconds
        self.flush_seconds = flush_seconds
        self._tasks: dict[str, asyncio.Task] = {}

    def _token(self, link) -> str:
        return self.resolver.resolve(link["secret_ref"]).get_secret_value()

    async def run_forever(self) -> None:
        try:
            while True:
                self.reconcile()
                await asyncio.sleep(self.reconcile_seconds)
        finally:
            for task in self._tasks.values():
                task.cancel()
            await asyncio.gather(*self._tasks.values(), return_exceptions=True)

    def reconcile(self) -> None:
        wanted = {row["id"] for row in self.service.runnable()}
        for link_id, task in list(self._tasks.items()):
            if task.done() or link_id not in wanted:
                task.cancel()
                del self._tasks[link_id]
        for link_id in wanted - set(self._tasks):
            self._tasks[link_id] = asyncio.get_running_loop().create_task(self._run_link(link_id))

    async def _run_link(self, link_id: str) -> None:
        async with asyncio.TaskGroup() as group:
            group.create_task(self._inbound_loop(link_id))
            group.create_task(self._outbound_loop(link_id))

    async def _guard(self, link_id: str, step) -> float | None:
        """Executa um passo; devolve o atraso até o próximo ou None para encerrar."""
        try:
            await step()
            return 0.0
        except ChannelError as error:
            if error.code in ("channel_token_invalid", "channel_target_invalid"):
                self.service.fail(link_id, error.code)
                return None
            self.service.note(link_id, error.code)
            return error.retry_after or 10.0
        except NotFoundError:
            return None

    async def _inbound_loop(self, link_id: str) -> None:
        while True:
            delay = await self._guard(link_id, lambda: self.poll_once(link_id))
            if delay is None:
                return
            if not delay:
                try:
                    delay = self.adapters[self.service.get(link_id)["kind"]].idle_seconds
                except NotFoundError:
                    return
            await asyncio.sleep(delay)

    async def _outbound_loop(self, link_id: str) -> None:
        while True:
            delay = await self._guard(link_id, lambda: self.flush_once(link_id))
            if delay is None:
                return
            await asyncio.sleep(max(delay, self.flush_seconds))

    async def poll_once(self, link_id: str) -> int:
        link = self.service.get(link_id)
        if link["status"] not in ("pending", "active"):
            raise NotFoundError("Canal inativo.")
        adapter = self.adapters[link["kind"]]
        token = self._token(link)
        messages, cursor = await adapter.poll(token, link["target"], link["inbound_cursor"])
        handled = 0
        for message in messages:
            if self.service.begin_inbound(link_id, message.external_id):
                await self._handle(link_id, adapter, token, message)
                handled += 1
        if cursor != link["inbound_cursor"]:
            self.service.set_cursor(link_id, cursor)
        if link["reason_code"] in (
            "channel_unreachable",
            "channel_rate_limited",
            "channel_conflict",
        ):
            self.service.note(link_id, None)
        return handled

    async def _reply(self, adapter, token, link, chat_id, text) -> None:
        await adapter.send(token, link["target"], chat_id, text)

    async def _handle(self, link_id, adapter, token, message: Inbound) -> None:
        link = self.service.get(link_id)
        text = (message.text or "").strip()
        code = normalize_code(text) if text else None
        if code and self.service.pair(link_id, code, message.sender_id, message.sender_name):
            await self._reply(
                adapter,
                token,
                link,
                message.sender_id,
                f"Conectado ao Bees. Agora você conversa com esta abelha aqui.\n\n{HELP}",
            )
            self.service.finish_inbound(link_id, message.external_id, "processed")
            return
        if link["status"] != "active" or link["chat_id"] != message.sender_id:
            # Desconhecidos não recebem resposta: o bot não confirma que existe.
            self.service.finish_inbound(link_id, message.external_id, "ignored")
            return
        if not text:
            await self._reply(
                adapter, token, link, message.sender_id, "Por enquanto só mensagens de texto."
            )
            self.service.finish_inbound(link_id, message.external_id, "ignored")
            return
        lowered = text.lower()
        if lowered.split("@")[0] in ("/ajuda", "/help", "/start"):
            await self._reply(adapter, token, link, message.sender_id, HELP)
            self.service.finish_inbound(link_id, message.external_id, "processed")
            return
        if lowered.split("@")[0] == "/status":
            await self._reply(
                adapter, token, link, message.sender_id, f"Modo atual: {link['mode']}."
            )
            self.service.finish_inbound(link_id, message.external_id, "processed")
            return
        if lowered.startswith("/modo") or lowered.startswith("/mode"):
            words = lowered.split()
            mode = MODE_WORDS.get(words[1]) if len(words) == 2 else None
            if mode is None:
                answer = "Use /modo conversa, /modo agente ou /modo pesquisa."
            else:
                self.service.set_mode(link_id, mode)
                answer = f"Modo alterado para {words[1]}."
            await self._reply(adapter, token, link, message.sender_id, answer)
            self.service.finish_inbound(link_id, message.external_id, "processed")
            return
        await self._dispatch(link, adapter, token, message, text)

    async def _dispatch(self, link, adapter, token, message, text) -> None:
        from bees_core.agent_tools.chat import submit_agent_message
        from bees_core.providers.errors import ProviderError
        from bees_core.providers.service import ProviderService

        request_id = uuid5(UUID(link["id"]), f"bees.channel-in/{message.external_id}")
        try:
            if link["mode"] == "chat":
                # A resposta persistida na conversa segue pelo laço de saída.
                await ProviderService(
                    self.database, self.resolver, transport=self.provider_transport
                ).chat(
                    link["agent_id"],
                    link["conversation_id"],
                    text,
                    input_metadata={"channel": {"link_id": link["id"], "kind": link["kind"]}},
                )
                message_id = None
            else:
                store = StateStore(self.database)
                with store.transaction(source="channel_agent_message") as unit:
                    receipt = submit_agent_message(
                        self.database,
                        unit,
                        link["agent_id"],
                        link["conversation_id"],
                        text,
                        request_id,
                        mode=link["mode"],
                        message_metadata={"channel": {"link_id": link["id"], "kind": link["kind"]}},
                    )
                message_id = receipt["message_id"]
                await self._reply(
                    adapter,
                    token,
                    link,
                    message.sender_id,
                    "Trabalhando nisso… aviso aqui quando terminar.",
                )
            self.service.finish_inbound(link["id"], message.external_id, "processed", message_id)
        except ProviderError as error:
            self.service.finish_inbound(link["id"], message.external_id, "failed")
            await self._reply(
                adapter,
                token,
                link,
                message.sender_id,
                ERRORS.get(error.code, f"Não foi possível responder agora ({error.code})."),
            )
        except ChannelError:
            raise
        except Exception:
            log.exception("channel dispatch failed")
            self.service.finish_inbound(link["id"], message.external_id, "failed")
            await self._reply(
                adapter, token, link, message.sender_id, "Não foi possível responder agora."
            )

    def _stopped(self) -> bool:
        with self.database.transaction(write=False) as connection:
            row = connection.execute("SELECT status FROM safety_control WHERE id=1").fetchone()
        return row is not None and row[0] != "running"

    async def _deliver(self, link, adapter, token, key: str, content: str, after=None) -> bool:
        if not self.service.begin_outbound(link["id"], key):
            return False
        try:
            await adapter.send(token, link["target"], link["chat_id"], content)
        except ChannelError as error:
            self.service.abandon_outbound(
                link["id"], key, uncertain=error.code == "channel_outcome_unknown"
            )
            raise
        except BaseException:
            # Cancelamento ou queda durante o envio: pode ter chegado; não repetir.
            self.service.abandon_outbound(link["id"], key, uncertain=True)
            raise
        self.service.mark_sent(link["id"], key, after=after)
        return True

    async def flush_once(self, link_id: str) -> int:
        link = self.service.get(link_id)
        if link["status"] != "active" or link["chat_id"] is None:
            return 0
        if self._stopped():
            # Parada global: nada sai para canais externos até a retomada.
            return 0
        adapter = self.adapters[link["kind"]]
        token = None
        sent = 0
        for message_id, content, created_at in self.service.outbound(link):
            token = token or self._token(link)
            if await self._deliver(link, adapter, token, message_id, content, created_at):
                sent += 1
        sent += await self._notify_approvals(link, adapter, token)
        return sent

    async def _notify_approvals(self, link, adapter, token) -> int:
        from bees_core.agent_tools.service import AgentToolService

        try:
            pending = AgentToolService(self.database).list(
                link["agent_id"], status="pending", limit=20
            )
        except Exception:
            return 0
        with self.database.transaction(write=False) as connection:
            preference = connection.execute(
                "SELECT enabled FROM attention_preferences WHERE agent_id=? "
                "AND kind='approval_required'",
                (link["agent_id"],),
            ).fetchone()
        if preference is not None and not preference[0]:
            # Preferência de atenção desativada também silencia o aviso no canal.
            return 0
        sent = 0
        where = f" em {self.app_url}" if self.app_url else " na interface do Bees"
        for approval in pending:
            key = f"approval:{approval['id']}"
            if not approval.get("valid", True) or self.service.notified(link["id"], key):
                continue
            token = token or self._token(link)
            text = (
                "⚠️ A abelha precisa da sua decisão: "
                f"{approval.get('summary') or approval.get('tool_name')}. Decida{where}."
            )
            if await self._deliver(link, adapter, token, key, text):
                sent += 1
        return sent
