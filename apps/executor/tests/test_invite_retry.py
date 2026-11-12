"""Recuperação do executor enquanto aguarda pareamento."""

import json
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.error
from types import SimpleNamespace

import pytest
from starlette.responses import JSONResponse
from test_executor_gateway import Server, create_app

from bees_core.executors import ExecutorService
from bees_executor.cli import _invite
from bees_executor.client import CredentialRejected, exchange


@pytest.mark.parametrize("status", [400, 401, 403, 409, 429, 500, 502, 503])
def test_exchange_distinguishes_temporary_failure_from_rejection(tmp_path, monkeypatch, status):
    def unavailable(*args, **kwargs):
        raise urllib.error.HTTPError("http://localhost", status, "Unavailable", {}, None)

    monkeypatch.setattr("urllib.request.OpenerDirector.open", unavailable)
    expected = ConnectionError if status == 429 or status >= 500 else CredentialRejected
    with pytest.raises(expected, match=f"exchange_.*_{status}"):
        exchange("http://localhost", "test-invite", tmp_path)
    assert not (tmp_path / "credential.json").exists()


@pytest.mark.parametrize(
    "content",
    [None, b"\xff", b"{", b"null", b"[]", b"{}", b'{"token": 123}', b'{"token": " "}'],
)
def test_invite_ignores_invalid_file_and_prefers_cli(tmp_path, content):
    invite_file = tmp_path / "invite.json"
    if content is not None:
        invite_file.write_bytes(content)
    args = SimpleNamespace(invite=None, invite_file=invite_file)
    assert _invite(args) is None
    args.invite = "-" + "A" * 42
    assert _invite(args) == args.invite


@pytest.mark.skipif(sys.platform == "win32", reason="Processo POSIX simples.")
@pytest.mark.parametrize("from_file", [False, True], ids=["cli", "volume"])
@pytest.mark.parametrize("rate_limited", [False, True], ids=["normal", "http429"])
def test_executor_pairs_once_the_api_comes_up(tmp_path, monkeypatch, from_file, rate_limited):
    # Porta reservada (bind sem listen): conexões são recusadas e outro teste paralelo
    # não a ocupa enquanto a API ainda não subiu.
    reserved = socket.socket()
    reserved.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    reserved.bind(("127.0.0.1", 0))
    port = reserved.getsockname()[1]
    monkeypatch.setattr("test_executor_gateway.free_port", lambda: port)
    # O convite é emitido num banco preparado antes; a API só sobe depois do executor.
    from bees_core.storage.database import Database

    (tmp_path / "state").mkdir()
    database = Database(tmp_path / "state" / "bees.sqlite3")
    database.initialize()
    # Um convite base64url pode começar com hífen, que o argparse trata como opção.
    kind = "computer_runtime" if from_file else "personal"
    with monkeypatch.context() as invite_patch:
        invite_patch.setattr("bees_core.executors.secrets.token_urlsafe", lambda _: "-" + "A" * 42)
        invite = ExecutorService(database).invite({"kind": kind, "name": "Computador de teste"})
    invite_file = tmp_path / "invite.json"
    if from_file:
        invite_file.write_text("[]", encoding="utf-8")
        arguments = [
            "--invite-file",
            str(invite_file),
            "--computers",
            str(tmp_path / "computers"),
            "--mode",
            "single",
        ]
    else:
        arguments = [f"--invite={invite['token']}", "--root", f"Docs={tmp_path}"]
    log = open(tmp_path / "executor.log", "wb")
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "bees_executor.cli",
            "computer" if from_file else "personal",
            "--api",
            f"http://127.0.0.1:{port}",
            "--state",
            str(tmp_path / "executor"),
            *arguments,
        ],
        stdout=log,
        stderr=subprocess.STDOUT,
        env=os.environ
        | {
            "BEES_EXECUTOR_INVITE": "",
            "PYTHONPATH": os.pathsep.join(p for p in sys.path if p),
        },
    )
    try:
        # Observe a espera antes de liberar a API ou substituir o arquivo.
        expected = "Aguardando convite" if from_file else "API indisponível ao parear"
        deadline = time.monotonic() + 15
        log_path = tmp_path / "executor.log"
        while process.poll() is None and time.monotonic() < deadline:
            if expected in log_path.read_text(encoding="utf-8"):
                break
            time.sleep(0.1)
        assert process.poll() is None, log_path.read_text(encoding="utf-8")
        assert expected in log_path.read_text(encoding="utf-8")
        if from_file:
            temporary = invite_file.with_suffix(".tmp")
            temporary.write_text(json.dumps(invite), encoding="utf-8")
            temporary.replace(invite_file)
        attempts = []

        def limited_app(settings):
            app = create_app(settings)

            @app.middleware("http")
            async def limit_exchange(request, call_next):
                if request.url.path == "/api/v1/executors/exchange":
                    attempts.append((await request.json())["token"])
                    if rate_limited and len(attempts) == 1:
                        return JSONResponse({"code": "rate_limited"}, status_code=429)
                return await call_next(request)

            return app

        monkeypatch.setattr("test_executor_gateway.create_app", limited_app)
        reserved.close()
        server = Server(tmp_path)
        try:
            service = ExecutorService(server.database)
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline and not any(
                item["online"] for item in service.list(kind)
            ):
                time.sleep(0.3)
            assert any(item["online"] for item in service.list(kind)), (
                tmp_path / "executor.log"
            ).read_text(encoding="utf-8")
            assert len(service.list(kind)) == 1
            assert attempts == [invite["token"]] * (2 if rate_limited else 1)
            # O limite temporário não enfraquece o consumo único do convite.
            with pytest.raises(CredentialRejected, match="exchange_failed_401"):
                exchange(server.url, invite["token"], tmp_path / "replay")
        finally:
            server.stop()
    finally:
        reserved.close()
        if process.poll() is None:
            process.send_signal(signal.SIGKILL)
        process.wait(timeout=10)
        log.close()
