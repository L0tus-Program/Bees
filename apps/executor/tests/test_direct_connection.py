"""Convites e canais autenticados ignoram proxies descobertos pelo sistema."""

import asyncio
import json
import os
import subprocess
import sys
import time
import urllib.request
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler
from uuid import UUID, uuid4

import pytest
from test_exchange_redirect import endpoint
from websockets.asyncio.server import serve

from bees_executor.client import ExecutorClient, exchange
from bees_executor.view import _bridge


@contextmanager
def proxy_trap():
    requests = []

    class Trap(BaseHTTPRequestHandler):
        def do_POST(self):
            self.reject()

        def do_CONNECT(self):
            self.reject()

        def reject(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            requests.append((self.command, self.path, dict(self.headers), body))
            self.send_response(502)
            self.end_headers()

        def log_message(self, *args):
            pass

    with endpoint(Trap) as proxy:
        yield proxy, requests


def proxy_environment(proxy, inherited):
    # Remover também o bypass local herdado: o teste deve alcançar a armadilha
    # na base, mesmo que a máquina tenha no_proxy=127.0.0.1 por padrão.
    return {
        key: value for key, value in inherited.items() if not key.lower().endswith("_proxy")
    } | {
        "http_proxy": proxy,
        "https_proxy": proxy,
        "ws_proxy": proxy,
        "wss_proxy": proxy,
        "no_proxy": "",
    }


def install_proxy(monkeypatch, proxy, source):
    for key in list(os.environ):
        if key.lower().endswith("_proxy"):
            monkeypatch.delenv(key)
    # Bypass nativo pode excluir loopback no Windows/macOS. Tornar essa parte
    # determinística sem substituir o transporte HTTP/WebSocket real.
    monkeypatch.setattr(urllib.request, "proxy_bypass", lambda host: False)
    if source == "system":
        monkeypatch.setattr(urllib.request, "getproxies", lambda: {"http": proxy, "https": proxy})
    else:
        monkeypatch.setenv(source, proxy)
        monkeypatch.setenv("no_proxy", "")


@pytest.mark.parametrize("kind", ["personal", "computer_runtime"])
@pytest.mark.parametrize("source", ["http_proxy", "HTTP_PROXY", "system"])
def test_pairing_contacts_api_without_proxy(tmp_path, monkeypatch, kind, source, caplog):
    calls = []
    identity = {"executor_id": str(uuid4()), "credential": "be_" + "A" * 43, "kind": kind}
    token = "private-invite"

    class API(BaseHTTPRequestHandler):
        def do_POST(self):
            calls.append(
                (self.path, json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            )
            self.send_response(200)
            self.end_headers()
            self.wfile.write(json.dumps(identity).encode())

        def log_message(self, *args):
            pass

    with proxy_trap() as (proxy, intercepted), endpoint(API) as api:
        install_proxy(monkeypatch, proxy, source)
        failure = None
        try:
            saved = exchange(api, token, tmp_path, expected_kind=kind)
        except Exception as error:
            failure = error
        assert intercepted == []
        assert failure is None
    assert calls[0][0] == "/api/v1/executors/exchange"
    assert len(calls) == 1
    assert calls[0][1]["token"] == token
    assert saved == identity | {"api": api}
    assert json.loads((tmp_path / "credential.json").read_bytes()) == saved
    assert not (tmp_path / "credential.tmp").exists()
    assert token not in caplog.text
    assert identity["credential"] not in caplog.text


@pytest.mark.parametrize("channel", ["personal", "computer_runtime", "view"])
@pytest.mark.parametrize("source", ["ws_proxy", "https_proxy", "system"])
def test_authenticated_channels_contact_api_without_proxy(
    tmp_path, monkeypatch, channel, source, caplog
):
    async def scenario(proxy, intercepted):
        client = ExecutorClient("", tmp_path, channel, None)
        client.credential = "be_" + "B" * 43
        credential = {
            "executor_id": str(uuid4()),
            "credential": client.credential,
            "kind": channel,
        }
        requests, messages = [], []

        async def handle(websocket):
            requests.append((websocket.request.path, websocket.request.headers["Authorization"]))
            messages.append(await websocket.recv())
            client.stopped.set()
            if channel == "view":
                await websocket.send(b"input")
                await websocket.wait_closed()

        async def vnc(reader, writer):
            try:
                writer.write(b"frame")
                await writer.drain()
                messages.append(await reader.readexactly(5))
            finally:
                writer.close()
                await writer.wait_closed()

        async with serve(handle, "127.0.0.1", 0) as server:
            client.api = f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}"
            credential["api"] = client.api
            path = tmp_path / "credential.json"
            path.write_text(json.dumps(credential))
            before = path.read_bytes()
            install_proxy(monkeypatch, proxy, source)
            failure = None
            try:
                if channel == "view":
                    async with await asyncio.start_server(vnc, "127.0.0.1", 0) as desktop:
                        await asyncio.wait_for(
                            _bridge(
                                client, "private-view-token", desktop.sockets[0].getsockname()[1]
                            ),
                            timeout=3,
                        )
                else:
                    await asyncio.wait_for(client.run_forever(credential), timeout=3)
            except Exception as error:
                failure = error
            assert intercepted == []
            assert failure is None
            assert path.read_bytes() == before
        assert requests == [
            (
                "/api/v1/executors/view/private-view-token"
                if channel == "view"
                else "/api/v1/executors/connect",
                "Bearer " + client.credential,
            )
        ]
        if channel == "view":
            assert messages == [b"frame", b"input"]
        else:
            assert len(messages) == 1
            assert json.loads(messages[0])["kind"] == channel
        assert client.credential not in caplog.text
        assert "private-view-token" not in caplog.text

    with proxy_trap() as (proxy, intercepted):
        asyncio.run(scenario(proxy, intercepted))


@pytest.mark.skipif(sys.platform == "win32", reason="Fixture de API/subprocesso POSIX.")
@pytest.mark.parametrize("kind", ["personal", "computer_runtime"])
def test_cli_pairs_and_reconnects_directly_with_inherited_proxies(tmp_path, kind):
    from test_executor_gateway import Server
    from test_state_lock import environment, wait_for

    from bees_core.executors import ExecutorService

    server = Server(tmp_path)
    process = None
    outputs = []
    try:
        service = ExecutorService(server.database)
        invite = service.invite({"kind": kind, "name": "Conexão direta"})
        invite_file = tmp_path / "invite.json"
        invite_file.write_text(json.dumps({"token": invite["token"]}))
        state = tmp_path / "executor"
        arguments = (
            ["personal"]
            if kind == "personal"
            else ["computer", "--computers", str(tmp_path / "computers"), "--mode", "single"]
        )
        with proxy_trap() as (proxy, intercepted):
            env = proxy_environment(proxy, environment())
            before = None
            for restart in (False, True):
                process = subprocess.Popen(
                    [
                        sys.executable,
                        "-m",
                        "bees_executor.cli",
                        *arguments,
                        "--api",
                        server.url,
                        "--state",
                        str(state),
                        "--invite-file",
                        str(invite_file),
                    ],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    env=env,
                )
                wait_for(
                    lambda: bool(service.list(kind)) and service.list(kind)[0]["online"], process
                )
                identity = json.loads((state / "credential.json").read_bytes())
                assert len(service.list(kind)) == 1
                assert service.list(kind)[0]["id"] == identity["executor_id"]
                assert service.grants(UUID(identity["executor_id"])) == []
                assert intercepted == []
                if restart:
                    assert (state / "credential.json").read_bytes() == before
                else:
                    before = (state / "credential.json").read_bytes()
                process.kill()
                output, _ = process.communicate(timeout=10)
                outputs.append(output)
                process = None
                # Aguardar encerramento da conexão antes de medir a reconexão.
                deadline = time.monotonic() + 5
                while service.list(kind)[0]["online"] and time.monotonic() < deadline:
                    time.sleep(0.05)
                assert not service.list(kind)[0]["online"]
            assert invite_file.exists()
            assert intercepted == []
        for output in outputs:
            assert invite["token"].encode() not in output
            assert identity["credential"].encode() not in output
    finally:
        if process is not None:
            process.kill()
            process.communicate(timeout=10)
        server.stop()
