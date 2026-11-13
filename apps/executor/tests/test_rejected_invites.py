"""Alternar o arquivo não autoriza repetir um convite já recusado."""

import asyncio
import json
import os
import subprocess
import sys
import time
from http.server import BaseHTTPRequestHandler
from types import SimpleNamespace

import pytest
from test_exchange_redirect import endpoint
from test_exchange_response import VALID

from bees_executor.cli import _run


@pytest.mark.parametrize("kind", ["personal", "computer_runtime"])
@pytest.mark.parametrize("failure", ["invalid", "truncated", "wrong_kind", "rejected"])
def test_rejected_invites_are_not_retried_after_file_alternates(
    tmp_path, monkeypatch, caplog, kind, failure
):
    attempts = []
    first, second, third = (f"private-invite-{letter}" for letter in "ABC")
    invite = tmp_path / "invite.json"
    invite.write_text(json.dumps({"token": first}))
    state = tmp_path / "state"
    state.mkdir()
    journal = state / "journal.sqlite3"
    journal.write_bytes(b"private-journal")
    payload = VALID | {"kind": kind}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            token = json.loads(self.rfile.read(int(self.headers["Content-Length"])))["token"]
            attempts.append(token)
            if token == third:
                body = json.dumps(payload).encode()
            elif failure == "wrong_kind":
                other = "personal" if kind == "computer_runtime" else "computer_runtime"
                body = json.dumps(payload | {"kind": other}).encode()
            else:
                body = b'{"credential":"private-response-secret"'
            self.send_response(401 if token != third and failure == "rejected" else 200)
            truncated = token != third and failure == "truncated"
            self.send_header("Content-Length", str(len(body) + truncated))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    waits = []

    async def next_invite(_):
        waits.append(True)
        assert attempts == [first] + ([second] if len(waits) >= 2 else [])
        assert not (state / "credential.json").exists()
        assert not (state / "credential.tmp").exists()
        assert journal.read_bytes() == b"private-journal"
        # Recusa A, recusa B, reapresenta A/B e finalmente fornece C.
        sequence = [second, first, second, third]
        assert len(waits) <= len(sequence)
        invite.write_text(json.dumps({"token": sequence[len(waits) - 1]}))

    connected = []

    async def connect(self, credential):
        connected.append(credential)

    monkeypatch.setattr("bees_executor.cli.asyncio.sleep", next_invite)
    monkeypatch.setattr("bees_executor.cli.ExecutorClient.run_forever", connect)
    caplog.set_level("INFO", logger="bees_executor")
    with endpoint(Handler) as api:
        args = SimpleNamespace(api=api, state=state, invite=None, invite_file=invite)
        asyncio.run(_run(args, SimpleNamespace(capabilities=[]), kind))
    assert attempts == [first, second, third]
    assert len(waits) == 4
    assert connected == [payload | {"api": api}]
    assert json.loads((state / "credential.json").read_text()) == connected[0]
    assert journal.read_bytes() == b"private-journal"
    assert "Aguardando convite" in caplog.text
    assert "private-invite" not in caplog.text
    assert "private-response-secret" not in caplog.text
    assert payload["credential"] not in caplog.text


@pytest.mark.parametrize("kind", ["personal", "computer_runtime"])
@pytest.mark.parametrize("status", [429, 503])
def test_unavailable_invite_can_still_be_retried(tmp_path, monkeypatch, kind, status):
    attempts = []
    token = "private-retry-invite"

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            attempts.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            self.send_response(status if len(attempts) == 1 else 200)
            self.end_headers()
            self.wfile.write(json.dumps(VALID | {"kind": kind}).encode())

        def log_message(self, *args):
            pass

    async def wait(_):
        assert len(attempts) == 1
        assert not (tmp_path / "credential.json").exists()

    connected = []

    async def connect(self, credential):
        connected.append(credential)

    monkeypatch.setattr("bees_executor.cli.asyncio.sleep", wait)
    monkeypatch.setattr("bees_executor.cli.ExecutorClient.run_forever", connect)
    with endpoint(Handler) as api:
        args = SimpleNamespace(api=api, state=tmp_path, invite=token, invite_file=None)
        asyncio.run(_run(args, SimpleNamespace(capabilities=[]), kind))
    assert [item["token"] for item in attempts] == [token, token]
    assert len(connected) == 1


@pytest.mark.parametrize("kind", ["personal", "computer_runtime"])
def test_explicit_rejected_invite_keeps_precedence(tmp_path, monkeypatch, kind):
    from bees_executor.client import CredentialRejected

    invite = tmp_path / "invite.json"
    invite.write_text(json.dumps({"token": "private-file-invite"}))
    attempts = []

    def refuse(api, token, state, **kwargs):
        attempts.append(token)
        raise CredentialRejected("exchange_failed_401")

    class Finished(Exception):
        pass

    waits = []

    async def wait(_):
        waits.append(True)
        assert attempts == ["private-explicit-invite"]
        if len(waits) == 3:
            raise Finished
        invite.write_text(json.dumps({"token": f"private-file-invite-{len(waits)}"}))

    monkeypatch.setattr("bees_executor.cli.exchange", refuse)
    monkeypatch.setattr("bees_executor.cli.asyncio.sleep", wait)
    args = SimpleNamespace(
        api="http://localhost", state=tmp_path, invite="private-explicit-invite", invite_file=invite
    )
    with pytest.raises(Finished):
        asyncio.run(_run(args, SimpleNamespace(capabilities=[]), kind))
    assert not (tmp_path / "credential.json").exists()


@pytest.mark.skipif(sys.platform == "win32", reason="Subprocesso POSIX de integração.")
@pytest.mark.parametrize("kind", ["personal", "computer_runtime"])
def test_cli_with_real_api_does_not_resubmit_consumed_invites(tmp_path, monkeypatch, kind):
    from starlette.responses import Response
    from test_executor_gateway import Server, create_app

    from bees_core.executors import ExecutorService

    attempts = []

    def broken_app(settings):
        app = create_app(settings)

        @app.middleware("http")
        async def lose_credentials(request, call_next):
            if request.url.path != "/api/v1/executors/exchange":
                return await call_next(request)
            attempts.append((await request.json())["token"])
            response = await call_next(request)
            if len(attempts) <= 2:
                # Cadastro real persistido; a identidade se perde no transporte.
                return Response(b'{"credential":"private-response-secret"')
            return response

        return app

    monkeypatch.setattr("test_executor_gateway.create_app", broken_app)
    server = Server(tmp_path)
    process = None
    log_path = tmp_path / "executor.log"

    def wait_for(predicate):
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline and process.poll() is None:
            if predicate():
                return
            time.sleep(0.1)
        pytest.fail(log_path.read_text(encoding="utf-8"))

    try:
        service = ExecutorService(server.database)
        invites = [service.invite({"kind": kind, "name": name}) for name in "ABC"]
        tokens = [item["token"] for item in invites]
        invite_file = tmp_path / "invite.json"

        def publish(index):
            temporary = invite_file.with_suffix(".tmp")
            temporary.write_text(json.dumps(invites[index]), encoding="utf-8")
            temporary.replace(invite_file)

        publish(0)
        state = tmp_path / "executor"
        arguments = (
            ["--root", f"Docs={tmp_path / 'shared'}"]
            if kind == "personal"
            else ["--computers", str(tmp_path / "computers"), "--mode", "single"]
        )
        (tmp_path / "shared").mkdir()
        with log_path.open("wb") as log:
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "bees_executor.cli",
                    "personal" if kind == "personal" else "computer",
                    "--api",
                    server.url,
                    "--state",
                    str(state),
                    "--invite-file",
                    str(invite_file),
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
            wait_for(lambda: log_path.read_text().count("Convite recusado") == 1)
            publish(1)
            wait_for(lambda: log_path.read_text().count("Convite recusado") == 2)
            for index in (0, 1):
                previous = log_path.read_text().count("Aguardando convite")
                publish(index)
                wait_for(
                    lambda previous=previous: (
                        log_path.read_text().count("Aguardando convite") > previous
                    )
                )
                assert attempts == tokens[:2]
                assert not (state / "credential.json").exists()
                assert not (state / "credential.tmp").exists()
                assert len(service.list(kind)) == 2
                assert not any(item["online"] for item in service.list(kind))
            publish(2)
            wait_for(lambda: any(item["online"] for item in service.list(kind)))
            assert attempts == tokens
            assert len(service.list(kind)) == 3
            assert sum(item["online"] for item in service.list(kind)) == 1
            saved = json.loads((state / "credential.json").read_text())
            assert service.authenticate(saved["credential"])["id"] == saved["executor_id"]
            diagnostic = log_path.read_text(encoding="utf-8")
            assert all(token not in diagnostic for token in tokens)
            assert saved["credential"] not in diagnostic
            assert "private-response-secret" not in diagnostic
    finally:
        if process is not None:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=10)
        server.stop()
