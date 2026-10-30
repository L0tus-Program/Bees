"""Conexão de saída com o plano de controle: pareamento, comandos e recibos.

O executor nunca escuta portas para a API (exceto o canal de visualização,
aberto também como conexão de saída). Credencial própria ``be_`` fica em arquivo
privado do estado local; convites são de uso único.
"""

import asyncio
import json
import logging
import os
import platform
import ssl
import time
import urllib.error
import urllib.request
from pathlib import Path

from bees_executor import PROTOCOL, VERSION

log = logging.getLogger("bees_executor")


class CredentialRejected(RuntimeError):
    pass


def _write_private(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w") as handle:
        json.dump(payload, handle)
    os.replace(temporary, path)


def _ssl_context(url: str):
    return ssl.create_default_context() if url.startswith("https") else None


def exchange(api: str, token: str, state: Path) -> dict:
    body = json.dumps(
        {"token": token, "version": VERSION, "platform": platform.platform()[:80]}
    ).encode()
    request = urllib.request.Request(
        api.rstrip("/") + "/api/v1/executors/exchange",
        data=body,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=20, context=_ssl_context(api)) as response:
            payload = json.loads(response.read())
    except urllib.error.HTTPError as error:
        if error.code >= 500:
            # Falha do servidor (ex.: proxy sem a API ainda): transitória, o convite segue válido.
            raise ConnectionError(f"exchange_unavailable_{error.code}") from None
        raise CredentialRejected(f"exchange_failed_{error.code}") from None
    credential = {
        "executor_id": payload["executor_id"],
        "credential": payload["credential"],
        "kind": payload["kind"],
        "api": api,
    }
    _write_private(state / "credential.json", credential)
    return credential


def load_credential(state: Path) -> dict | None:
    path = state / "credential.json"
    return json.loads(path.read_text()) if path.exists() else None


class ExecutorClient:
    def __init__(self, api: str, state: Path, kind: str, handler, *, capabilities=(), roots=()):
        self.api = api.rstrip("/")
        self.state = state
        self.kind = kind
        self.handler = handler
        self.capabilities = list(capabilities)
        self.roots = list(roots)
        self.outbox: dict[str, dict] = {}
        self.websocket = None
        self.credential = None
        self.stopped = asyncio.Event()

    def ws_url(self) -> str:
        if self.api.startswith("https://"):
            return "wss://" + self.api[len("https://") :] + "/api/v1/executors/connect"
        return "ws://" + self.api.removeprefix("http://") + "/api/v1/executors/connect"

    async def send(self, message: dict) -> bool:
        websocket = self.websocket
        if websocket is None:
            return False
        try:
            await websocket.send(json.dumps(message, ensure_ascii=False))
            return True
        except Exception:
            return False

    async def deliver(self, command_id: str, receipt: dict) -> None:
        message = {"type": "result", "id": command_id, **receipt}
        self.outbox[command_id] = message
        if await self.send(message):
            # Reenvio após reconexão até a confirmação do gateway.
            pass

    async def run_forever(self, credential: dict) -> None:
        from websockets.asyncio.client import connect
        from websockets.exceptions import InvalidStatus

        delay = 1.0
        self.credential = credential["credential"]
        while not self.stopped.is_set():
            try:
                async with connect(
                    self.ws_url(),
                    additional_headers={"Authorization": f"Bearer {credential['credential']}"},
                    max_size=64 * 1024 * 1024,
                    ping_interval=20,
                    open_timeout=20,
                    ssl=_ssl_context(self.api),
                ) as websocket:
                    self.websocket = websocket
                    delay = 1.0
                    await websocket.send(
                        json.dumps(
                            {
                                "type": "hello",
                                "protocol": PROTOCOL,
                                "kind": self.kind,
                                "version": VERSION,
                                "platform": platform.platform()[:80],
                                "capabilities": self.capabilities,
                                "roots": self.roots,
                            }
                        )
                    )
                    for message in list(self.outbox.values()):
                        await websocket.send(json.dumps(message, ensure_ascii=False))
                    async for raw in websocket:
                        await self._receive(json.loads(raw))
            except InvalidStatus as error:
                if error.response.status_code in (401, 403):
                    raise CredentialRejected("executor_credentials_invalid") from None
                log.warning("Conexão recusada (%s).", error.response.status_code)
            except (TimeoutError, OSError, ValueError) as error:
                log.warning("Conexão indisponível: %s", type(error).__name__)
            except Exception as error:  # websockets.ConnectionClosed e similares
                log.warning("Conexão encerrada: %s", type(error).__name__)
            finally:
                self.websocket = None
            if self.stopped.is_set():
                break
            await asyncio.sleep(delay)
            delay = min(delay * 2, 30.0)

    async def _receive(self, message: dict) -> None:
        kind = message.get("type")
        if kind == "ack":
            self.outbox.pop(message.get("id"), None)
        elif kind == "ping":
            await self.send({"type": "pong", "at": time.time()})
        elif kind == "command":
            asyncio.create_task(self._command(message))
        else:
            asyncio.create_task(self.handler.control(self, message))

    async def _command(self, message: dict) -> None:
        command_id = message.get("id")
        if not isinstance(command_id, str):
            return
        deadline = message.get("deadline_ms")
        if isinstance(deadline, (int, float)) and time.time() * 1000 >= deadline:
            await self.deliver(command_id, {"status": "failed", "code": "executor_deadline"})
            return
        try:
            receipt = await self.handler.execute(message)
        except Exception as error:
            receipt = {
                "status": "unknown",
                "code": "executor_error",
                "detail": type(error).__name__,
            }
        await self.deliver(command_id, receipt)
