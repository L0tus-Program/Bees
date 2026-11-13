"""Após HTTP de sucesso, falha de leitura não prova convite disponível."""

import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler
from types import SimpleNamespace

import pytest
from test_exchange_redirect import endpoint
from test_exchange_response import VALID

from bees_executor.cli import _run
from bees_executor.client import CredentialRejected, exchange


@pytest.mark.parametrize("error_type", [TimeoutError, ConnectionResetError, OSError])
@pytest.mark.parametrize("existing", [False, True])
def test_failed_read_rejects_response_preserving_state(tmp_path, monkeypatch, error_type, existing):
    path = tmp_path / "credential.json"
    if existing:
        path.write_bytes(b"existing-private-state")
    journal = tmp_path / "journal.sqlite3"
    journal.write_bytes(b"existing-private-journal")
    closed = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            closed.append(True)

        def read(self):
            raise error_type("private-response-secret")

    monkeypatch.setattr("urllib.request.OpenerDirector.open", lambda *a, **kw: Response())
    with pytest.raises(CredentialRejected, match="^exchange_invalid_response$"):
        exchange("http://localhost", "private-invite", tmp_path)
    assert closed == [True]
    assert journal.read_bytes() == b"existing-private-journal"
    assert path.exists() == existing
    if existing:
        assert path.read_bytes() == b"existing-private-state"
    assert not path.with_suffix(".tmp").exists()


@pytest.mark.parametrize("error_type", [TimeoutError, ConnectionResetError, OSError])
def test_failure_before_response_still_reports_unavailability(tmp_path, monkeypatch, error_type):
    def unavailable(*args, **kwargs):
        raise error_type("controlled pre-response failure")

    monkeypatch.setattr("urllib.request.OpenerDirector.open", unavailable)
    with pytest.raises(error_type):
        exchange("http://localhost", "private-invite", tmp_path)
    assert not (tmp_path / "credential.json").exists()


@pytest.mark.parametrize("kind", ["personal", "computer_runtime"])
def test_real_body_timeout_waits_for_another_invite(tmp_path, monkeypatch, caplog, kind):
    """HTTP real com timeout; loop da CLI sem espera real de três segundos."""
    import urllib.request

    attempts = []
    release = threading.Event()
    invite = tmp_path / "invite.json"
    invite.write_text(json.dumps({"token": "first-private-invite"}))
    state = tmp_path / "state"
    state.mkdir()
    journal = state / "journal.sqlite3"
    journal.write_bytes(b"private-journal")
    payload = VALID | {"kind": kind}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            attempts.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            self.send_response(200)
            body = json.dumps(payload).encode()
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if len(attempts) == 1:
                self.wfile.write(body[:10])
                self.wfile.flush()
                release.wait(5)
            else:
                self.wfile.write(body)

        def log_message(self, *args):
            pass

    original_open = urllib.request.OpenerDirector.open

    def short_timeout(self, request, *, timeout):
        return original_open(self, request, timeout=0.2)

    monkeypatch.setattr(urllib.request.OpenerDirector, "open", short_timeout)
    waits = []

    async def next_invite(_):
        waits.append(True)
        assert len(attempts) == 1
        assert not (state / "credential.json").exists()
        assert not (state / "credential.tmp").exists()
        assert journal.read_bytes() == b"private-journal"
        # Primeiro sleep após recusa; segundo prova que o mesmo convite não é repetido.
        if len(waits) == 2:
            invite.write_text(json.dumps({"token": "second-private-invite"}))
            release.set()
        assert len(waits) <= 2

    connected = []

    async def connect(self, credential):
        connected.append(credential)

    monkeypatch.setattr("bees_executor.cli.asyncio.sleep", next_invite)
    monkeypatch.setattr("bees_executor.cli.ExecutorClient.run_forever", connect)
    caplog.set_level("INFO", logger="bees_executor")
    try:
        with endpoint(Handler) as api:
            args = SimpleNamespace(api=api, state=state, invite=None, invite_file=invite)
            asyncio.run(_run(args, SimpleNamespace(capabilities=[]), kind))
    finally:
        release.set()
    assert [item["token"] for item in attempts] == ["first-private-invite", "second-private-invite"]
    assert len(waits) == 2
    assert connected == [payload | {"api": api}]
    assert json.loads((state / "credential.json").read_text()) == connected[0]
    assert journal.read_bytes() == b"private-journal"
    assert "exchange_invalid_response" in caplog.text
    assert "Aguardando convite" in caplog.text
    assert "private-invite" not in caplog.text
    assert payload["credential"] not in caplog.text
