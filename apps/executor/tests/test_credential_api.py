"""A credencial persistida só pode sair para a API que a emitiu."""

import asyncio
import json
import os
import subprocess
import sys
import time
from http.server import BaseHTTPRequestHandler
from uuid import uuid4

import pytest
from test_exchange_redirect import endpoint
from test_executor_gateway import Server

from bees_core.executors import ExecutorService
from bees_executor.client import ExecutorClient, exchange


@pytest.mark.parametrize("kind", ["personal", "computer_runtime"])
@pytest.mark.parametrize(
    "saved_api",
    [None, 42, "", "http://other.test", "https://chosen.test", "http://chosen.test/other"],
)
def test_client_never_connects_with_an_unbound_credential(tmp_path, monkeypatch, kind, saved_api):
    calls = []
    client = ExecutorClient("http://chosen.test", tmp_path, kind, None)

    def connect(*args, **kwargs):
        calls.append((args, kwargs))
        client.stopped.set()
        raise OSError("stop fixture")

    monkeypatch.setattr("bees_executor.websocket.AuthenticatedConnect", connect)
    credential = {"executor_id": str(uuid4()), "credential": "be_" + "A" * 43, "kind": kind}
    if saved_api is not None:
        credential["api"] = saved_api
    path = tmp_path / "credential.json"
    path.write_text(json.dumps(credential))
    before = path.read_bytes()
    with pytest.raises(RuntimeError, match="^executor_api_mismatch$"):
        asyncio.run(client.run_forever(credential))
    assert calls == []
    assert client.credential is None
    assert path.read_bytes() == before


@pytest.mark.parametrize("saved_api", ["http://chosen.test", "http://chosen.test/"])
@pytest.mark.parametrize("chosen_api", ["http://chosen.test", "http://chosen.test/"])
def test_client_keeps_trailing_slash_equivalence(tmp_path, monkeypatch, saved_api, chosen_api):
    calls = []
    client = ExecutorClient(chosen_api, tmp_path, "personal", None)

    def connect(*args, **kwargs):
        calls.append((args, kwargs))
        client.stopped.set()
        raise OSError("stop fixture")

    monkeypatch.setattr("bees_executor.websocket.AuthenticatedConnect", connect)
    credential = {"api": saved_api, "credential": "be_" + "A" * 43, "kind": "personal"}
    asyncio.run(client.run_forever(credential))
    assert len(calls) == 1
    assert calls[0][0] == ("ws://chosen.test/api/v1/executors/connect",)
    assert calls[0][1]["additional_headers"] == {
        "Authorization": "Bearer " + credential["credential"]
    }


@pytest.mark.skipif(sys.platform == "win32", reason="Processo POSIX simples.")
@pytest.mark.parametrize("kind", ["personal", "computer_runtime"])
def test_cli_preserves_identity_when_api_changes_and_restarts_with_original(tmp_path, kind):
    server = Server(tmp_path)
    process = None
    requests = []

    class Trap(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append((self.path, self.headers.get("Authorization")))
            self.send_response(401)
            self.end_headers()

        def do_POST(self):
            requests.append((self.path, self.headers.get("Authorization")))
            self.send_response(401)
            self.end_headers()

        def log_message(self, *args):
            pass

    try:
        service = ExecutorService(server.database)
        invite = service.invite({"kind": kind, "name": "Vínculo da API"})
        state = tmp_path / "executor"
        credential = exchange(server.url, invite["token"], state, expected_kind=kind)
        path = state / "credential.json"
        before = path.read_bytes()
        marker = state / "private-evidence"
        marker.write_bytes(b"preserve-private-evidence")
        invite_file = tmp_path / "invite.json"
        invite_file.write_text(json.dumps({"token": "unused-private-invite"}))
        arguments = (
            ["--root", f"Docs={tmp_path}"]
            if kind == "personal"
            else ["--computers", str(tmp_path / "computers"), "--mode", "single"]
        )

        def start(api):
            return subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "bees_executor.cli",
                    "personal" if kind == "personal" else "computer",
                    "--api",
                    api,
                    "--state",
                    str(state),
                    "--invite-file",
                    str(invite_file),
                    *arguments,
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                env=os.environ
                | {
                    "BEES_EXECUTOR_INVITE": "",
                    "PYTHONPATH": os.pathsep.join(p for p in sys.path if p),
                },
            )

        with endpoint(Trap) as wrong_api:
            process = start(wrong_api)
            output, _ = process.communicate(timeout=10)
            diagnostic = output.decode("utf-8")
            assert process.returncode != 0
            assert "executor_api_mismatch" in diagnostic
            assert "Traceback" not in diagnostic
            assert credential["credential"] not in diagnostic
            assert "unused-private-invite" not in diagnostic
            assert wrong_api not in diagnostic
            assert server.url not in diagnostic
            assert requests == []
        assert path.read_bytes() == before
        assert marker.read_bytes() == b"preserve-private-evidence"
        assert len(service.list(kind)) == 1
        process = start(server.url + "/")
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline and process.poll() is None:
            if service.list(kind)[0]["online"]:
                break
            time.sleep(0.1)
        assert process.poll() is None
        assert service.list(kind)[0]["online"]
        assert len(service.list(kind)) == 1
        assert service.list(kind)[0]["id"] == credential["executor_id"]
        assert path.read_bytes() == before
        assert marker.read_bytes() == b"preserve-private-evidence"
    finally:
        if process is not None:
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=10)
        server.stop()
