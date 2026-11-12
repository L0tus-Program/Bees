"""Uma resposta incompleta pode chegar depois do consumo do convite."""

import json
from http.server import BaseHTTPRequestHandler

import pytest
from test_exchange_redirect import endpoint
from test_exchange_response import VALID

from bees_executor.client import CredentialRejected, exchange


@pytest.mark.parametrize("framing", ["length", "chunked"])
@pytest.mark.parametrize("existing", [False, True])
def test_exchange_rejects_truncated_body_without_writing_state(tmp_path, framing, existing):
    requests = []
    path = tmp_path / "credential.json"
    if existing:
        path.write_text("existing-private-state", encoding="utf-8")
    # Mesmo JSON completo não pode ser aceito se o enquadramento HTTP estiver incompleto.
    body = json.dumps(VALID).encode()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            requests.append(self.rfile.read(int(self.headers["Content-Length"])))
            self.send_response(200)
            self.send_header("Connection", "close")
            if framing == "length":
                self.send_header("Content-Length", str(len(body) + 10))
            else:
                self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            if framing == "length":
                self.wfile.write(body)
            else:
                self.wfile.write(f"{len(body):x}\r\n".encode() + body + b"\r\n")
                # Sem chunk final: o corpo parece JSON válido, mas a resposta não terminou.
            self.close_connection = True

        def log_message(self, *args):
            pass

    with endpoint(Handler) as api:
        with pytest.raises(CredentialRejected, match="^exchange_invalid_response$"):
            exchange(api, "private-invite", tmp_path)
    assert len(requests) == 1
    if existing:
        assert path.read_text() == "existing-private-state"
    else:
        assert not path.exists()
    assert not path.with_suffix(".tmp").exists()
