"""Pareamento limita leitura sem aceitar enquadramento HTTP incompleto."""

import io
import json
import threading
from http.server import BaseHTTPRequestHandler

import pytest
from test_exchange_redirect import endpoint
from test_exchange_response import VALID

from bees_executor.client import CredentialRejected, exchange

LIMIT = 65_536


@pytest.mark.parametrize("kind", ["personal", "computer_runtime"])
@pytest.mark.parametrize("size", [LIMIT - 1, LIMIT, LIMIT + 1])
@pytest.mark.parametrize("existing", [False, True])
def test_exchange_size_boundary_preserves_state(tmp_path, monkeypatch, kind, size, existing):
    payload = VALID | {"kind": kind}
    body = json.dumps(payload).encode()
    body += b" " * (size - len(body))
    reads = []

    class Response(io.BytesIO):
        def read(self, size=-1):
            reads.append(size)
            return super().read(size)

    response = Response(body)
    monkeypatch.setattr("urllib.request.OpenerDirector.open", lambda *a, **kw: response)
    credential = tmp_path / "credential.json"
    if existing:
        credential.write_bytes(b"private-existing-credential")
    journal = tmp_path / "journal.sqlite3"
    journal.write_bytes(b"private-existing-journal")
    if size > LIMIT:
        with pytest.raises(CredentialRejected, match="^exchange_invalid_response$"):
            exchange("http://localhost", "private-invite", tmp_path, expected_kind=kind)
        assert credential.exists() == existing
        if existing:
            assert credential.read_bytes() == b"private-existing-credential"
    else:
        assert exchange("http://localhost", "private-invite", tmp_path, expected_kind=kind) == (
            payload | {"api": "http://localhost"}
        )
    assert reads == [LIMIT + 1]
    assert response.closed
    assert journal.read_bytes() == b"private-existing-journal"
    assert not (tmp_path / "credential.tmp").exists()


@pytest.mark.parametrize("framing", ["length", "chunked", "eof"])
def test_real_oversized_response_closes_without_waiting_for_tail(tmp_path, framing):
    release = threading.Event()
    tail_finished = threading.Event()
    body = json.dumps(VALID).encode()
    body += b" " * (LIMIT + 1 - len(body))

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            self.send_response(200)
            if framing == "length":
                self.send_header("Content-Length", str(LIMIT * 100))
            elif framing == "chunked":
                self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            if framing == "chunked":
                self.wfile.write(f"{len(body):x}\r\n".encode() + body + b"\r\n")
            else:
                self.wfile.write(body)
            self.wfile.flush()
            # Sem EOF/chunk final nem restante de Content-Length. A leitura
            # limitada deve recusar antes de liberarmos a cauda da resposta.
            release.wait(5)
            tail_finished.set()
            self.close_connection = True

        def log_message(self, *args):
            pass

    try:
        with endpoint(Handler) as api:
            with pytest.raises(CredentialRejected, match="^exchange_invalid_response$"):
                exchange(api, "private-invite", tmp_path)
            assert not tail_finished.is_set()
            assert not (tmp_path / "credential.json").exists()
    finally:
        release.set()


@pytest.mark.parametrize("framing", ["length", "chunked", "eof"])
@pytest.mark.parametrize("size", [LIMIT - 1, LIMIT])
def test_real_valid_response_at_limit(tmp_path, framing, size):
    body = json.dumps(VALID).encode()
    body += b" " * (size - len(body))

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            self.send_response(200)
            if framing == "length":
                self.send_header("Content-Length", str(len(body)))
            elif framing == "chunked":
                self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            if framing == "chunked":
                self.wfile.write(f"{len(body):x}\r\n".encode() + body + b"\r\n0\r\n\r\n")
            else:
                self.wfile.write(body)
            self.close_connection = True

        def log_message(self, *args):
            pass

    with endpoint(Handler) as api:
        assert exchange(api, "private-invite", tmp_path) == VALID | {"api": api}
