"""Nenhum canal autenticado deve reapresentar o segredo após um redirect."""

import asyncio
import json
import os
import subprocess
import sys
from uuid import uuid4

import pytest
from websockets.asyncio.server import serve
from websockets.datastructures import Headers
from websockets.http11 import Response

from bees_executor.client import CredentialRejected, ExecutorClient
from bees_executor.journal import Journal
from bees_executor.view import _bridge


@pytest.mark.parametrize("status", [300, 301, 302, 303, 307, 308])
@pytest.mark.parametrize("same_origin", [False, True], ids=["other-origin", "same-origin"])
@pytest.mark.parametrize("channel", ["personal", "computer_runtime", "view"])
def test_redirect_never_contacts_target(tmp_path, status, same_origin, channel):
    async def scenario():
        requests = []
        rejected = []
        client = ExecutorClient("", tmp_path, channel, None)
        secret = "be_" + "A" * 43
        credential = {"executor_id": str(uuid4()), "credential": secret, "kind": channel}
        client.credential = secret

        async def unused(websocket):
            raise AssertionError("Redirect não pode estabelecer WebSocket")

        async def trap(connection, request):
            requests.append(("target", request.path, request.headers.get("Authorization")))
            return Response(401, "Unauthorized", Headers(), b"")

        async def origin(connection, request):
            if request.path == "/trap":
                return await trap(connection, request)
            requests.append(("origin", request.path, request.headers.get("Authorization")))
            client.stopped.set()
            return Response(status, "Redirect", Headers({"Location": destination}), b"")

        async def vnc(reader, writer):
            try:
                await asyncio.wait_for(reader.read(), timeout=2)
            except TimeoutError:
                pass
            finally:
                writer.close()
                await writer.wait_closed()

        async with serve(unused, "127.0.0.1", 0, process_request=trap) as target:
            async with serve(unused, "127.0.0.1", 0, process_request=origin) as source:
                client.api = f"http://127.0.0.1:{source.sockets[0].getsockname()[1]}"
                destination = (
                    "/trap"
                    if same_origin
                    else f"ws://127.0.0.1:{target.sockets[0].getsockname()[1]}/trap"
                )
                credential["api"] = client.api
                path = tmp_path / "credential.json"
                path.write_text(json.dumps(credential))
                before = path.read_bytes()
                if channel == "view":
                    async with await asyncio.start_server(vnc, "127.0.0.1", 0) as desktop:
                        with pytest.raises(Exception) as failure:
                            await asyncio.wait_for(
                                _bridge(
                                    client,
                                    "private-view-token",
                                    desktop.sockets[0].getsockname()[1],
                                ),
                                timeout=5,
                            )
                        assert getattr(failure.value, "response", None) is not None
                        assert failure.value.response.status_code == status
                else:
                    try:
                        await asyncio.wait_for(client.run_forever(credential), timeout=5)
                    except CredentialRejected as error:
                        rejected.append(str(error))
                assert path.read_bytes() == before
        assert rejected == []
        assert requests == [
            (
                "origin",
                "/api/v1/executors/view/private-view-token"
                if channel == "view"
                else "/api/v1/executors/connect",
                f"Bearer {secret}",
            )
        ]

    asyncio.run(scenario())


@pytest.mark.parametrize("kind", ["personal", "computer_runtime"])
def test_reconnects_to_original_endpoint_after_redirect(tmp_path, kind, caplog):
    async def scenario():
        requests = []
        hello = []
        client = ExecutorClient("", tmp_path, kind, None)
        credential = {"executor_id": str(uuid4()), "credential": "be_" + "B" * 43, "kind": kind}

        async def handle(websocket):
            hello.append(json.loads(await websocket.recv()))
            client.stopped.set()

        async def origin(connection, request):
            requests.append((request.path, request.headers.get("Authorization")))
            if len(requests) == 1:
                return Response(302, "Redirect", Headers({"Location": "/private-trap"}), b"")
            if request.path != "/api/v1/executors/connect":
                return Response(401, "Unauthorized", Headers(), b"")

        async with serve(handle, "127.0.0.1", 0, process_request=origin) as server:
            client.api = f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}"
            credential["api"] = client.api
            path = tmp_path / "credential.json"
            path.write_text(json.dumps(credential))
            marker = tmp_path / "private-evidence"
            marker.write_bytes(b"journal-must-survive")
            before = path.read_bytes()
            await asyncio.wait_for(client.run_forever(credential), timeout=8)
            assert path.read_bytes() == before
            assert marker.read_bytes() == b"journal-must-survive"
        assert requests == [("/api/v1/executors/connect", "Bearer " + credential["credential"])] * 2
        assert len(hello) == 1
        assert hello[0]["kind"] == kind
        assert credential["credential"] not in caplog.text
        assert "private-trap" not in caplog.text

    asyncio.run(scenario())


def test_view_bridges_data_without_redirect(tmp_path):
    async def scenario():
        received = []
        authorization = []
        client = ExecutorClient("", tmp_path, "computer_runtime", None)
        client.credential = "be_" + "C" * 43

        async def handle(websocket):
            authorization.append(websocket.request.headers.get("Authorization"))
            received.append(await websocket.recv())
            await websocket.send(b"input")
            await websocket.wait_closed()

        async def vnc(reader, writer):
            try:
                writer.write(b"vnc-frame")
                await writer.drain()
                received.append(await reader.readexactly(5))
            finally:
                writer.close()
                await writer.wait_closed()

        async with serve(handle, "127.0.0.1", 0) as server:
            client.api = f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}"
            async with await asyncio.start_server(vnc, "127.0.0.1", 0) as desktop:
                await asyncio.wait_for(
                    _bridge(client, "view-token", desktop.sockets[0].getsockname()[1]), timeout=5
                )
        assert received == [b"vnc-frame", b"input"]
        assert authorization == ["Bearer " + client.credential]

    asyncio.run(scenario())


@pytest.mark.skipif(sys.platform == "win32", reason="Processo POSIX simples.")
@pytest.mark.parametrize("kind", ["personal", "computer_runtime"])
def test_cli_keeps_identity_and_recovers_after_redirect(tmp_path, kind):
    async def scenario():
        requests = []
        hellos = []
        ready = False
        connected = asyncio.Event()
        secret = "be_" + "D" * 43

        async def handle(websocket):
            hellos.append(json.loads(await websocket.recv()))
            connected.set()
            await websocket.wait_closed()

        async def origin(connection, request):
            requests.append((request.path, request.headers.get("Authorization")))
            if request.path == "/trap":
                return Response(401, "Unauthorized", Headers(), b"")
            if not ready:
                return Response(307, "Redirect", Headers({"Location": "/trap"}), b"")

        async with serve(handle, "127.0.0.1", 0, process_request=origin) as server:
            api = f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}"
            state = tmp_path / "executor"
            state.mkdir()
            path = state / "credential.json"
            path.write_text(
                json.dumps(
                    {"api": api, "executor_id": str(uuid4()), "credential": secret, "kind": kind}
                )
            )
            before = path.read_bytes()
            Journal(state / "journal.sqlite3").close()
            marker = state / "private-evidence"
            marker.write_bytes(b"keep-journal")
            arguments = (
                ["--root", f"Docs={tmp_path}"]
                if kind == "personal"
                else ["--computers", str(tmp_path / "computers"), "--mode", "single"]
            )
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "bees_executor.cli",
                    "personal" if kind == "personal" else "computer",
                    "--api",
                    api,
                    "--state",
                    str(state),
                    *arguments,
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                env=os.environ
                | {
                    "BEES_EXECUTOR_INVITE": "",
                    "BEES_EXECUTOR_INVITE_FILE": "",
                    "PYTHONPATH": os.pathsep.join(p for p in sys.path if p),
                },
            )
            try:

                async def wait_attempts():
                    while len(requests) < 2 and process.poll() is None:
                        await asyncio.sleep(0.05)

                await asyncio.wait_for(wait_attempts(), timeout=10)
                assert process.poll() is None
                assert requests == [("/api/v1/executors/connect", "Bearer " + secret)] * 2
                assert path.read_bytes() == before
                assert marker.read_bytes() == b"keep-journal"
                ready = True
                await asyncio.wait_for(connected.wait(), timeout=10)
                assert hellos[0]["kind"] == kind
                assert path.read_bytes() == before
                assert marker.read_bytes() == b"keep-journal"
            finally:
                if process.poll() is None:
                    process.kill()
                output, _ = await asyncio.to_thread(process.communicate, timeout=10)
                assert secret.encode() not in output
                assert b"/trap" not in output
                assert b"Credencial revogada" not in output

    asyncio.run(scenario())
