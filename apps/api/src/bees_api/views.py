"""Visualização autenticada da tela do computador (VNC sobre WebSocket).

O navegador conecta com a sessão humana e Origin permitido. O runtime abre uma
conexão de saída própria (credencial ``be_``) para a sessão pedida; a API apenas
encaminha bytes entre as duas pontas. Fora do controle humano, o runtime serve a
tela somente para visualização (sem entrada de teclado/mouse).
"""

import asyncio
import secrets
import time
from dataclasses import dataclass, field
from uuid import UUID

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from bees_api.auth import COOKIE_NAME
from bees_core.executors import ExecutorError, ExecutorService

router = APIRouter(prefix="/api/v1", tags=["computer-view"])
ATTACH_SECONDS = 20


@dataclass
class _Session:
    executor_id: str
    request: dict
    attached: asyncio.Future
    created: float = field(default_factory=time.monotonic)
    sent: bool = False


class ViewHub:
    def __init__(self):
        self.sessions: dict[str, _Session] = {}

    def open(self, executor_id: str, computer_id: str, interactive: bool) -> tuple[str, _Session]:
        token = secrets.token_urlsafe(24)
        session = _Session(
            executor_id,
            {
                "type": "view_open",
                "session": token,
                "computer_id": computer_id,
                "interactive": interactive,
            },
            asyncio.get_running_loop().create_future(),
        )
        self.sessions[token] = session
        return token, session

    def pending(self, executor_id: str) -> list[dict]:
        requests = []
        for session in self.sessions.values():
            if session.executor_id == executor_id and not session.sent:
                session.sent = True
                requests.append(session.request)
        return requests

    def attach(self, token: str, executor_id: str, websocket: WebSocket) -> _Session | None:
        session = self.sessions.get(token)
        if session is None or session.executor_id != executor_id or session.attached.done():
            return None
        session.attached.set_result(websocket)
        return session

    def failed(self, token: str) -> None:
        session = self.sessions.get(token)
        if session is not None and not session.attached.done():
            session.attached.set_exception(ExecutorError("view_unavailable"))

    def close(self, token: str) -> None:
        self.sessions.pop(token, None)


async def _pump(source: WebSocket, target: WebSocket) -> None:
    while True:
        message = await source.receive()
        if message["type"] == "websocket.disconnect":
            return
        if message.get("bytes") is not None:
            await target.send_bytes(message["bytes"])
        elif message.get("text") is not None:
            await target.send_text(message["text"])


@router.websocket("/agents/{agent_id}/computers/{computer_id}/view")
async def human_view(websocket: WebSocket, agent_id: UUID, computer_id: UUID) -> None:
    settings = websocket.app.state.settings
    origin = websocket.headers.get("origin")
    session = websocket.app.state.identity.authenticate(websocket.cookies.get(COOKIE_NAME))
    if session is None or origin not in settings.allowed_origins:
        await websocket.close(code=4403)
        return
    service = ExecutorService(websocket.app.state.database)
    computers = await asyncio.to_thread(service.computers, agent_id)
    computer = next((item for item in computers if item["id"] == str(computer_id)), None)
    if computer is None or computer["status"] != "ready" or not computer["online"]:
        await websocket.close(code=4404)
        return
    hub: ViewHub = websocket.app.state.view_hub
    token, view = hub.open(
        computer["executor_id"], computer["id"], computer["control_holder"] == "human"
    )
    await websocket.accept(
        subprotocol="binary" if "binary" in websocket.scope.get("subprotocols", []) else None
    )
    try:
        runtime = await asyncio.wait_for(view.attached, timeout=ATTACH_SECONDS)
    except TimeoutError, ExecutorError:
        hub.close(token)
        await websocket.close(code=4504)
        return
    tasks = [
        asyncio.create_task(_pump(websocket, runtime)),
        asyncio.create_task(_pump(runtime, websocket)),
    ]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        hub.close(token)
        for socket in (runtime, websocket):
            try:
                await socket.close()
            except Exception:
                pass


@router.websocket("/executors/view/{token}")
async def runtime_view(websocket: WebSocket, token: str) -> None:
    values = websocket.headers.getlist("authorization")
    credential = values[0][7:] if len(values) == 1 and values[0].startswith("Bearer be_") else ""
    try:
        executor = await asyncio.to_thread(
            ExecutorService(websocket.app.state.database).authenticate, credential
        )
    except ExecutorError:
        await websocket.close(code=4401)
        return
    await websocket.accept()
    hub: ViewHub = websocket.app.state.view_hub
    session = hub.attach(token, executor["id"], websocket)
    if session is None:
        await websocket.close(code=4404)
        return
    # O encaminhamento acontece no handler humano; esta conexão vive até ele terminar.
    try:
        while token in hub.sessions:
            await asyncio.sleep(0.5)
    except WebSocketDisconnect:
        pass
