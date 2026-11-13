"""Conexão de saída com o plano de controle: pareamento, comandos e recibos.

O executor nunca escuta portas para a API (exceto o canal de visualização,
aberto também como conexão de saída). Credencial própria ``be_`` fica em arquivo
privado do estado local; convites são de uso único.
"""

import asyncio
import http.client
import json
import logging
import os
import platform
import re
import ssl
import time
import urllib.error
import urllib.request
from pathlib import Path
from uuid import UUID

from bees_executor import PROTOCOL, VERSION

log = logging.getLogger("bees_executor")


class CredentialRejected(RuntimeError):
    pass


class CredentialBindingError(RuntimeError):
    """Vínculo local inválido: preservar estado, sem tratar como revogação."""


class CredentialKindError(CredentialBindingError):
    """Modo local incompatível com a identidade salva: sem repareamento."""


class CredentialStateError(RuntimeError):
    """Estado local ilegível ou inválido: não autoriza uma nova identidade."""


class CredentialPersistenceError(RuntimeError):
    """Identidade recebida sem persistência confirmada: não repetir a troca."""


class CredentialPendingError(RuntimeError):
    """Temporário não reconciliado: não conectar nem sobrescrever evidência."""


def _check_pending_credential(state: Path) -> None:
    try:
        (state / "credential.tmp").lstat()
    except FileNotFoundError:
        return
    except OSError:
        raise CredentialPendingError("executor_credential_pending") from None
    # Não ler/adotar o conteúdo: mesmo vazio, parcial ou link quebrado pode ser
    # evidência de uma troca já consumida. A CLI mantém o state_lock neste ponto.
    raise CredentialPendingError("executor_credential_pending")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # O convite só pode ser trocado no endpoint escolhido pelo operador.
        return None


def _create_private(path: Path) -> int:
    if os.name != "nt":
        return os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)

    import ctypes
    import msvcrt
    from ctypes import wintypes

    # O CRT pode seguir um link quebrado mesmo com O_EXCL. CREATE_NEW deve
    # operar sobre o próprio reparse point, nunca sobre seu destino.
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    create.restype = wintypes.HANDLE
    close = kernel.CloseHandle
    close.argtypes = [wintypes.HANDLE]
    close.restype = wintypes.BOOL
    handle = create(
        str(path),
        0x40000000,  # GENERIC_WRITE
        0,  # Sem compartilhamento enquanto gravamos.
        None,
        1,  # CREATE_NEW
        0x00200000,  # FILE_FLAG_OPEN_REPARSE_POINT
        None,
    )
    if handle == wintypes.HANDLE(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        # O descritor assume a propriedade do HANDLE; os.fdopen o fechará.
        return msvcrt.open_osfhandle(handle, os.O_WRONLY | os.O_BINARY)
    except BaseException:
        close(handle)
        raise


def _write_private(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    # A pendência pode surgir durante o HTTP, depois do preflight. A própria
    # abertura deve recusar colisão, sem truncar arquivo nem seguir link.
    descriptor = _create_private(temporary)
    with os.fdopen(descriptor, "w") as handle:
        json.dump(payload, handle)
        # Detectar falha tardia antes de publicar a identidade e conectar.
        # Erro conserva o temporário para reconciliação, sem repetir o convite.
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def _ssl_context(url: str):
    return ssl.create_default_context() if url.startswith("https") else None


def exchange(api: str, token: str, state: Path, *, expected_kind: str | None = None) -> dict:
    _check_pending_credential(state)
    body = json.dumps(
        {"token": token, "version": VERSION, "platform": platform.platform()[:80]}
    ).encode()
    request = urllib.request.Request(
        api.rstrip("/") + "/api/v1/executors/exchange",
        data=body,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    opener = urllib.request.build_opener(
        _NoRedirect(), urllib.request.HTTPSHandler(context=_ssl_context(api))
    )
    try:
        with opener.open(request, timeout=20) as response:
            try:
                raw = response.read()
            except (http.client.IncompleteRead, OSError):
                # Após HTTP de sucesso, timeout/reset também podem ocorrer depois
                # do consumo. Não usar bytes parciais nem repetir este convite.
                # Falhas ao abrir a resposta mantêm a política de indisponibilidade.
                raise CredentialRejected("exchange_invalid_response") from None
    except urllib.error.HTTPError as error:
        if error.code == 429 or error.code >= 500:
            # Limitação temporária ou falha do servidor não confirma recusa do convite.
            raise ConnectionError(f"exchange_unavailable_{error.code}") from None
        raise CredentialRejected(f"exchange_failed_{error.code}") from None
    try:
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise ValueError
        executor_id = payload.get("executor_id")
        secret = payload.get("credential")
        if (
            not isinstance(executor_id, str)
            or str(UUID(executor_id)) != executor_id
            or not isinstance(secret, str)
            or re.fullmatch(r"be_[A-Za-z0-9_-]{43}", secret) is None
            or payload.get("kind") not in ("personal", "computer_runtime")
        ):
            raise ValueError
    except (ValueError, RecursionError):
        # O convite pode ter sido consumido: aguarda outro, sem repetir a troca
        # nem incluir o corpo (que pode conter segredos) no diagnóstico.
        raise CredentialRejected("exchange_invalid_response") from None
    if expected_kind is not None and payload["kind"] != expected_kind:
        # O modo é escolha local: não salva identidade de outro tipo nem tenta
        # conectá-la. O convite pode ter sido consumido, portanto aguarda outro.
        raise CredentialRejected("exchange_kind_mismatch")
    credential = {
        "executor_id": payload["executor_id"],
        "credential": payload["credential"],
        "kind": payload["kind"],
        "api": api,
    }
    try:
        _write_private(state / "credential.json", credential)
    except OSError:
        # A API já respondeu: não classificar disco/permissão como rede instável
        # nem repetir o convite consumido. Preservar também eventual temporário.
        raise CredentialPersistenceError("executor_credential_save_failed") from None
    return credential


def load_credential(state: Path) -> dict | None:
    _check_pending_credential(state)
    path = state / "credential.json"
    try:
        path.lstat()
    except FileNotFoundError:
        return None
    except OSError:
        raise CredentialStateError("executor_credential_invalid") from None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError
        executor_id = payload.get("executor_id")
        secret = payload.get("credential")
        if (
            not isinstance(executor_id, str)
            or str(UUID(executor_id)) != executor_id
            or not isinstance(secret, str)
            or re.fullmatch(r"be_[A-Za-z0-9_-]{43}", secret) is None
            or payload.get("kind") not in ("personal", "computer_runtime")
            or not isinstance(payload.get("api"), str)
            or not payload["api"].rstrip("/")
        ):
            raise ValueError
    except (ValueError, OSError, RecursionError):
        # Não confundir corrupção, perda durante leitura ou link quebrado com
        # primeira instalação; não incluir conteúdo/caminho no diagnóstico.
        raise CredentialStateError("executor_credential_invalid") from None
    return payload


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
        saved_api = credential.get("api")
        if (
            not isinstance(saved_api, str)
            or not saved_api.rstrip("/")
            or saved_api.rstrip("/") != self.api
        ):
            # Nunca envia o segredo persistido a outro destino escolhido no
            # reinício. Este erro não autoriza apagar a credencial ou reparear.
            raise CredentialBindingError("executor_api_mismatch")

        saved_kind = credential.get("kind")
        if saved_kind not in ("personal", "computer_runtime") or saved_kind != self.kind:
            # Tipo também pertence à identidade persistida. Trocar de modo não
            # autoriza enviar o segredo, apagar o estado ou consumir outro convite.
            raise CredentialKindError("executor_kind_mismatch")

        from websockets.exceptions import InvalidStatus

        from bees_executor.websocket import AuthenticatedConnect as connect

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
