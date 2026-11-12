"""A troca do convite não pode obter credenciais de um destino redirecionado."""

import json
import threading
from contextlib import ExitStack, contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from bees_executor.client import CredentialRejected, exchange


@contextmanager
def endpoint(handler):
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
@pytest.mark.parametrize("same_origin", [False, True], ids=["other-origin", "same-origin"])
def test_exchange_refuses_redirect_without_contacting_target(tmp_path, status, same_origin):
    requests = []
    payload = {"executor_id": "unexpected", "credential": "be_unexpected", "kind": "personal"}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.respond()

        def do_GET(self):
            self.respond()

        def respond(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            requests.append((self.command, self.path, body))
            if self.path == "/api/v1/executors/exchange":
                self.send_response(status)
                self.send_header("Location", target + "/credential")
                self.end_headers()
            else:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(payload).encode())

        def log_message(self, *args):
            pass

    with ExitStack() as stack:
        origin = stack.enter_context(endpoint(Handler))
        target = origin if same_origin else stack.enter_context(endpoint(Handler))
        with pytest.raises(CredentialRejected, match=f"^exchange_failed_{status}$"):
            exchange(origin, "test-invite", tmp_path)
    assert len(requests) == 1
    assert requests[0][:2] == ("POST", "/api/v1/executors/exchange")
    assert json.loads(requests[0][2])["token"] == "test-invite"
    assert not (tmp_path / "credential.json").exists()
