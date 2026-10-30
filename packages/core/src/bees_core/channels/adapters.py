"""Clientes mínimos de Telegram Bot API e Discord REST, por polling (sem URL pública).

Os adaptadores não conhecem abelhas nem banco: recebem o token já resolvido do cofre,
devolvem mensagens normalizadas e enviam texto. Nenhum corpo de erro do serviço é
propagado; somente códigos estáveis.
"""

from dataclasses import dataclass
from typing import Protocol

import httpx

TELEGRAM_API = "https://api.telegram.org"
DISCORD_API = "https://discord.com/api/v10"


class ChannelError(RuntimeError):
    """Códigos: channel_token_invalid, channel_conflict, channel_target_invalid,
    channel_rate_limited, channel_unreachable (nada enviado), channel_outcome_unknown
    (pedido pode ter chegado), channel_invalid_response."""

    def __init__(self, code: str, retry_after: float | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.retry_after = retry_after


@dataclass(frozen=True)
class Inbound:
    external_id: str
    sender_id: str
    sender_name: str
    text: str | None


@dataclass(frozen=True)
class Identity:
    id: str
    name: str
    username: str | None = None


class ChannelAdapter(Protocol):
    kind: str
    poll_timeout: int
    idle_seconds: float
    max_text: int

    async def identity(self, token: str, target: str | None) -> Identity: ...

    async def poll(
        self, token: str, target: str | None, cursor: str
    ) -> tuple[list[Inbound], str]: ...

    async def send(self, token: str, target: str | None, chat_id: str, text: str) -> None: ...


def split_text(text: str, limit: int) -> list[str]:
    """Divide em partes ≤ limite, preferindo quebras de parágrafo/linha."""
    text = text.strip() or "…"
    parts = []
    while len(text) > limit:
        cut = text.rfind("\n\n", 0, limit)
        if cut < limit // 2:
            cut = text.rfind("\n", 0, limit)
        if cut < limit // 2:
            cut = text.rfind(" ", 0, limit)
        if cut < limit // 2:
            cut = limit
        parts.append(text[:cut].rstrip())
        text = text[cut:].lstrip()
    parts.append(text)
    return parts


class _Http:
    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._transport = transport

    def client(self, base: str, timeout: float, headers: dict | None = None) -> httpx.AsyncClient:
        # trust_env permite o proxy de saída do operador (HTTPS_PROXY).
        return httpx.AsyncClient(
            base_url=base,
            transport=self._transport,
            timeout=httpx.Timeout(timeout),
            follow_redirects=False,
            headers=headers,
        )


class TelegramAdapter(_Http):
    kind = "telegram"
    poll_timeout = 10
    idle_seconds = 0.5
    max_text = 4000

    async def _call(self, token: str, method: str, payload: dict, timeout: float = 15) -> dict:
        try:
            async with self.client(f"{TELEGRAM_API}/bot{token}/", timeout) as client:
                response = await client.post(method, json=payload)
        except httpx.ConnectError, httpx.ConnectTimeout:
            raise ChannelError("channel_unreachable") from None
        except httpx.HTTPError:
            raise ChannelError(
                "channel_outcome_unknown" if method == "sendMessage" else "channel_unreachable"
            ) from None
        if response.status_code in (401, 404):
            raise ChannelError("channel_token_invalid")
        if response.status_code == 409:
            raise ChannelError("channel_conflict")
        try:
            body = response.json()
        except ValueError:
            raise ChannelError("channel_invalid_response") from None
        if response.status_code == 429:
            retry = (
                (body.get("parameters") or {}).get("retry_after")
                if isinstance(body, dict)
                else None
            )
            raise ChannelError(
                "channel_rate_limited", float(retry) if isinstance(retry, int) else 5
            )
        if response.status_code == 400:
            raise ChannelError("channel_target_invalid")
        if response.status_code >= 400 or not isinstance(body, dict) or body.get("ok") is not True:
            raise ChannelError(
                "channel_unreachable" if response.status_code >= 500 else "channel_invalid_response"
            )
        return body

    async def identity(self, token: str, target: str | None) -> Identity:
        result = (await self._call(token, "getMe", {})).get("result")
        if not isinstance(result, dict) or not isinstance(result.get("id"), int):
            raise ChannelError("channel_invalid_response")
        return Identity(
            id=str(result["id"]),
            name=str(result.get("first_name") or result.get("username") or "bot")[:100],
            username=result.get("username") if isinstance(result.get("username"), str) else None,
        )

    async def poll(self, token: str, target: str | None, cursor: str) -> tuple[list[Inbound], str]:
        offset = int(cursor) + 1 if cursor.isdigit() and int(cursor) > 0 else 0
        body = await self._call(
            token,
            "getUpdates",
            {"offset": offset, "timeout": self.poll_timeout, "allowed_updates": ["message"]},
            timeout=self.poll_timeout + 10,
        )
        updates = body.get("result")
        if not isinstance(updates, list):
            raise ChannelError("channel_invalid_response")
        found = []
        last = int(cursor) if cursor.isdigit() else 0
        for update in updates[:100]:
            if not isinstance(update, dict) or not isinstance(update.get("update_id"), int):
                continue
            last = max(last, update["update_id"])
            message = update.get("message")
            if not isinstance(message, dict):
                continue
            chat = message.get("chat") or {}
            sender = message.get("from") or {}
            if not isinstance(chat.get("id"), int) or chat.get("type") != "private":
                continue  # Somente conversa privada com o bot.
            text = message.get("text")
            found.append(
                Inbound(
                    external_id=str(update["update_id"]),
                    sender_id=str(chat["id"]),
                    sender_name=str(sender.get("first_name") or sender.get("username") or "")[:200],
                    text=text[:12000] if isinstance(text, str) else None,
                )
            )
        return found, str(last)

    async def send(self, token: str, target: str | None, chat_id: str, text: str) -> None:
        for part in split_text(text, self.max_text):
            await self._call(token, "sendMessage", {"chat_id": int(chat_id), "text": part})


class DiscordAdapter(_Http):
    """Canal de texto/DM indicado pelo usuário (``target``); polling REST."""

    kind = "discord"
    poll_timeout = 0
    idle_seconds = 3.0
    max_text = 1900

    async def _call(self, token: str, method: str, path: str, **kwargs):
        try:
            async with self.client(
                DISCORD_API,
                15,
                headers={
                    "Authorization": f"Bot {token}",
                    "User-Agent": "Bees (https://github.com/L0tus-Program/Bees, 1)",
                },
            ) as client:
                response = await client.request(method, path, **kwargs)
        except httpx.ConnectError, httpx.ConnectTimeout:
            raise ChannelError("channel_unreachable") from None
        except httpx.HTTPError:
            raise ChannelError(
                "channel_outcome_unknown" if method == "POST" else "channel_unreachable"
            ) from None
        if response.status_code == 401:
            raise ChannelError("channel_token_invalid")
        if response.status_code in (403, 404):
            raise ChannelError("channel_target_invalid")
        if response.status_code == 429:
            try:
                retry = float(response.json().get("retry_after", 5))
            except ValueError, AttributeError, TypeError:
                retry = 5.0
            raise ChannelError("channel_rate_limited", retry)
        if response.status_code >= 400:
            raise ChannelError(
                "channel_unreachable" if response.status_code >= 500 else "channel_invalid_response"
            )
        try:
            return response.json()
        except ValueError:
            raise ChannelError("channel_invalid_response") from None

    async def identity(self, token: str, target: str | None) -> Identity:
        me = await self._call(token, "GET", "/users/@me")
        if not isinstance(me, dict) or not isinstance(me.get("id"), str):
            raise ChannelError("channel_invalid_response")
        if target:
            channel = await self._call(token, "GET", f"/channels/{target}")
            if not isinstance(channel, dict) or channel.get("id") != target:
                raise ChannelError("channel_target_invalid")
        return Identity(
            id=me["id"], name=str(me.get("username") or "bot")[:100], username=me.get("username")
        )

    async def poll(self, token: str, target: str | None, cursor: str) -> tuple[list[Inbound], str]:
        if not target:
            raise ChannelError("channel_target_invalid")
        if not cursor.isdigit() or cursor == "0":
            # Primeiro contato: começa depois da última mensagem existente.
            latest = await self._call(
                token, "GET", f"/channels/{target}/messages", params={"limit": 1}
            )
            if not isinstance(latest, list):
                raise ChannelError("channel_invalid_response")
            return [], latest[0]["id"] if latest and isinstance(latest[0].get("id"), str) else "1"
        items = await self._call(
            token, "GET", f"/channels/{target}/messages", params={"after": cursor, "limit": 50}
        )
        if not isinstance(items, list):
            raise ChannelError("channel_invalid_response")
        found = []
        last = int(cursor)
        for item in sorted(
            (i for i in items if isinstance(i, dict) and str(i.get("id", "")).isdigit()),
            key=lambda i: int(i["id"]),
        ):
            last = max(last, int(item["id"]))
            author = item.get("author") or {}
            if author.get("bot") or not isinstance(author.get("id"), str):
                continue
            text = item.get("content")
            found.append(
                Inbound(
                    external_id=item["id"],
                    sender_id=author["id"],
                    sender_name=str(author.get("global_name") or author.get("username") or "")[
                        :200
                    ],
                    text=text[:12000] if isinstance(text, str) and text else None,
                )
            )
        return found, str(last)

    async def send(self, token: str, target: str | None, chat_id: str, text: str) -> None:
        for part in split_text(text, self.max_text):
            await self._call(
                token,
                "POST",
                f"/channels/{target}/messages",
                json={"content": part, "allowed_mentions": {"parse": []}},
            )


def adapters(transport: httpx.AsyncBaseTransport | None = None) -> dict[str, ChannelAdapter]:
    return {"telegram": TelegramAdapter(transport), "discord": DiscordAdapter(transport)}
