"""Ponte de visualização: x11vnc local (loopback filtrado) ↔ conexão de saída à API.

Cada sessão inicia um x11vnc próprio com o UID da abelha, ouvindo somente em
127.0.0.1 numa porta por tela; o filtro do entrypoint impede outras contas de
abelha de conectar nela. Fora do controle humano, ``-viewonly`` descarta teclado
e mouse do visualizador.
"""

import asyncio
import logging
import shutil
import subprocess

log = logging.getLogger("bees_executor")


async def open_view(client, computers, message: dict) -> None:
    token = message.get("session")
    computer_id = message.get("computer_id")
    display = computers.display(computer_id) if isinstance(computer_id, str) else None
    user = computers.user(computer_id) if display else None
    if not isinstance(token, str) or user is None or shutil.which("x11vnc") is None:
        await client.send({"type": "view_failed", "session": token})
        return
    uid, gid, _ = user
    home = computers.home(computer_id)
    port = 5900 + int(display.lstrip(":"))
    argv = [
        "x11vnc",
        "-display",
        display,
        "-localhost",
        "-noipv6",
        "-rfbport",
        str(port),
        "-once",
        "-nopw",
        "-quiet",
        "-noxdamage",
    ]
    if not message.get("interactive"):
        argv.append("-viewonly")
    process = subprocess.Popen(
        argv,
        user=uid,
        group=gid,
        extra_groups=[],
        env={"HOME": str(home), "DISPLAY": display, "PATH": "/usr/bin:/bin"},
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        await _bridge(client, token, port)
    except Exception as error:
        log.warning("Visualização encerrada: %s", type(error).__name__)
        await client.send({"type": "view_failed", "session": token})
    finally:
        process.kill()


async def _bridge(client, token: str, port: int) -> None:
    from websockets.asyncio.client import connect

    url = client.ws_url().replace("/executors/connect", f"/executors/view/{token}")
    for _ in range(60):
        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            break
        except OSError:
            await asyncio.sleep(0.1)
    else:
        raise ConnectionError("vnc_unavailable")
    async with connect(
        url,
        additional_headers={"Authorization": f"Bearer {client.credential}"},
        max_size=16 * 1024 * 1024,
    ) as websocket:

        async def upstream():
            while True:
                data = await reader.read(65536)
                if not data:
                    return
                await websocket.send(data)

        async def downstream():
            async for data in websocket:
                writer.write(data if isinstance(data, bytes) else data.encode())
                await writer.drain()

        tasks = [asyncio.create_task(upstream()), asyncio.create_task(downstream())]
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in tasks:
            task.cancel()
    writer.close()
