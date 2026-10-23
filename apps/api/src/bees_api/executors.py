"""Gateway de executores: pareamento humano, computadores e canal WebSocket.

Rotas humanas exigem sessão (mutações com Origin/CSRF). O canal do executor
usa somente a credencial ``be_`` no cabeçalho Authorization; cookies nunca
autenticam executores e executores nunca acessam rotas humanas.
"""

import asyncio
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Request, WebSocket, WebSocketDisconnect

from bees_api.auth import require_session
from bees_core.executors import (
    CommandQueue,
    ComputerInput,
    ControlInput,
    ExchangeInput,
    ExecutorError,
    ExecutorService,
    GrantInput,
    InviteInput,
    RevisionInput,
)
from bees_core.security.identity import Session

_SESSION = Annotated[Session, Depends(require_session)]
router = APIRouter(prefix="/api/v1", tags=["executors"])
DISPATCH_SECONDS = 0.25
HEARTBEAT_SECONDS = 10


def _service(request) -> ExecutorService:
    return ExecutorService(request.app.state.database)


def _write_link(directory: Path, api_url: str, token: str, expires_at: str) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / "invite.json"
    temporary = directory / ".invite.tmp"
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o640)
    with os.fdopen(descriptor, "w") as handle:
        json.dump({"token": token, "expires_at": expires_at, "api": api_url}, handle)
    os.replace(temporary, target)


@router.post("/executors/invites")
def create_invite(body: InviteInput, request: Request, session: _SESSION) -> dict:
    invite = _service(request).invite(body)
    link_dir = request.app.state.settings.computer_link_dir
    delivered = False
    if body.kind == "computer_runtime" and link_dir is not None:
        # Entrega privada ao runtime local pelo volume compartilhado; sem terminal.
        _write_link(Path(link_dir), "", invite["token"], invite["expires_at"])
        delivered = True
    return invite | {"delivered_locally": delivered}


@router.get("/executors")
def list_executors(request: Request, session: _SESSION, kind: str | None = None) -> dict:
    return {"executors": _service(request).list(kind)}


@router.post("/executors/{executor_id}/revoke")
def revoke_executor(executor_id: UUID, body: RevisionInput, request: Request, session: _SESSION):
    return _service(request).revoke(executor_id, body)


@router.get("/executors/{executor_id}/grants")
def list_grants(executor_id: UUID, request: Request, session: _SESSION) -> dict:
    return {"grants": _service(request).grants(executor_id)}


@router.put("/executors/{executor_id}/grants/{agent_id}")
def set_grant(
    executor_id: UUID, agent_id: UUID, body: GrantInput, request: Request, session: _SESSION
) -> dict:
    return _service(request).set_grant(executor_id, agent_id, body)


@router.post("/executors/exchange")
def exchange(body: ExchangeInput, request: Request) -> dict:
    # O convite de alta entropia e uso único autentica esta troca.
    result = _service(request).exchange(body)
    link_dir = request.app.state.settings.computer_link_dir
    if link_dir is not None:
        (Path(link_dir) / "invite.json").unlink(missing_ok=True)
    return result


@router.get("/agents/{agent_id}/computers")
def list_computers(agent_id: UUID, request: Request, session: _SESSION) -> dict:
    service = _service(request)
    runtimes = service.list("computer_runtime")
    return {
        "computers": service.computers(agent_id),
        "runtime_online": any(item["online"] for item in runtimes),
        "runtime_paired": any(item["status"] == "active" for item in runtimes),
        "local_link_available": request.app.state.settings.computer_link_dir is not None,
    }


@router.post("/agents/{agent_id}/computers")
def create_computer(agent_id: UUID, body: ComputerInput, request: Request, session: _SESSION):
    return _service(request).create_computer(agent_id, body)


@router.post("/agents/{agent_id}/computers/{computer_id}/remove")
def remove_computer(
    agent_id: UUID, computer_id: UUID, body: RevisionInput, request: Request, session: _SESSION
):
    return _service(request).remove_computer(agent_id, computer_id, body)


@router.post("/agents/{agent_id}/computers/{computer_id}/control")
def control_computer(
    agent_id: UUID, computer_id: UUID, body: ControlInput, request: Request, session: _SESSION
):
    return _service(request).set_control(agent_id, computer_id, body)


def _bearer(websocket: WebSocket) -> str | None:
    values = websocket.headers.getlist("authorization")
    if len(values) != 1 or not values[0].startswith("Bearer be_"):
        return None
    return values[0][7:]


def _computers_message(service, executor_id):
    return [
        {"id": row["id"], "status": row["status"], "agent_id": row["agent_id"]}
        for row in service.runtime_computers(executor_id)
    ]


@router.websocket("/executors/connect")
async def connect(websocket: WebSocket) -> None:
    database = websocket.app.state.database
    service = ExecutorService(database)
    queue = CommandQueue(database)
    credential = _bearer(websocket)
    try:
        executor = await asyncio.to_thread(service.authenticate, credential or "")
    except ExecutorError:
        await websocket.close(code=4401)
        return
    await websocket.accept()
    executor_id = executor["id"]
    connection_id = str(uuid4())
    try:
        hello = json.loads(await asyncio.wait_for(websocket.receive_text(), timeout=20))
        if hello.get("type") != "hello" or hello.get("kind") != executor["kind"]:
            await websocket.close(code=4400)
            return
        metadata = {
            "version": str(hello.get("version", ""))[:40],
            "platform": str(hello.get("platform", ""))[:80],
            "capabilities": [str(item)[:40] for item in hello.get("capabilities", [])][:20],
            "roots": [str(item)[:60] for item in hello.get("roots", [])][:20],
        }
        await asyncio.to_thread(service.touch, executor_id, connection_id, metadata)
        computers = await asyncio.to_thread(_computers_message, service, executor_id)
        await websocket.send_text(
            json.dumps({"type": "welcome", "executor_id": executor_id, "computers": computers})
        )
    except TimeoutError, ValueError, WebSocketDisconnect:
        await websocket.close(code=4400)
        return

    async def receiver():
        while True:
            message = json.loads(await websocket.receive_text())
            kind = message.get("type")
            if kind == "result":
                try:
                    await asyncio.to_thread(
                        queue.report,
                        executor_id,
                        str(message.get("id")),
                        message.get("status"),
                        message.get("result"),
                        message.get("code") or message.get("detail"),
                    )
                except ExecutorError:
                    pass
                await websocket.send_text(json.dumps({"type": "ack", "id": message.get("id")}))
            elif kind == "computer_status":
                try:
                    await asyncio.to_thread(
                        service.report_computer,
                        executor_id,
                        str(message.get("id")),
                        message.get("status"),
                        message.get("reason"),
                    )
                except ExecutorError, Exception:
                    pass
            elif kind == "pong":
                await asyncio.to_thread(service.touch, executor_id, connection_id)
            elif kind == "view_failed":
                hub = getattr(websocket.app.state, "view_hub", None)
                if hub is not None:
                    hub.failed(str(message.get("session")))

    async def dispatcher():
        last_heartbeat = 0.0
        last_computers = None
        loop = asyncio.get_running_loop()
        while True:
            now = loop.time()
            if now - last_heartbeat >= HEARTBEAT_SECONDS:
                alive = await asyncio.to_thread(service.touch, executor_id, connection_id)
                if not alive:
                    await websocket.close(code=4401)
                    return
                await websocket.send_text(json.dumps({"type": "ping"}))
                last_heartbeat = now
            if executor["kind"] == "computer_runtime":
                computers = await asyncio.to_thread(_computers_message, service, executor_id)
                if computers != last_computers:
                    await websocket.send_text(
                        json.dumps({"type": "computers", "computers": computers})
                    )
                    last_computers = computers
            commands = await asyncio.to_thread(queue.claim, executor_id, connection_id)
            for command in commands:
                payload = json.loads(command["arguments_json"])
                deadline = datetime.fromisoformat(command["deadline_at"]).astimezone(UTC)
                await websocket.send_text(
                    json.dumps(
                        {
                            "type": "command",
                            "id": command["id"],
                            "tool": command["tool_name"],
                            "computer_id": command["computer_id"],
                            "arguments": payload.get("arguments", {}),
                            "scope": payload.get("scope"),
                            "deadline_ms": int(deadline.timestamp() * 1000),
                        },
                        ensure_ascii=False,
                    )
                )
            hub = getattr(websocket.app.state, "view_hub", None)
            if hub is not None:
                for request in hub.pending(executor_id):
                    await websocket.send_text(json.dumps(request))
            await asyncio.sleep(DISPATCH_SECONDS)

    tasks = [asyncio.create_task(receiver()), asyncio.create_task(dispatcher())]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for task in tasks:
            task.cancel()
        # Recolhe desconexões/erros de ambas as tarefas; a conexão é encerrada abaixo.
        await asyncio.gather(*tasks, return_exceptions=True)
        await asyncio.to_thread(service.disconnect, executor_id, connection_id)
