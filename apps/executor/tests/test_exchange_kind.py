"""O modo local precisa conferir o tipo da identidade antes de persistir."""

import json
from http.server import BaseHTTPRequestHandler

import pytest
from test_exchange_redirect import endpoint
from test_exchange_response import VALID

from bees_executor.client import CredentialRejected, exchange


@pytest.mark.parametrize("kind", ["personal", "computer_runtime"])
@pytest.mark.parametrize("matching", [False, True])
@pytest.mark.parametrize("existing", [False, True])
def test_exchange_checks_expected_kind_before_writing(tmp_path, kind, matching, existing):
    other_kind = "computer_runtime" if kind == "personal" else "personal"
    payload = VALID | {"kind": kind if matching else other_kind}
    requests = []
    path = tmp_path / "credential.json"
    if existing:
        path.write_bytes(b"existing-private-state")

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            requests.append(self.rfile.read(int(self.headers["Content-Length"])))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(json.dumps(payload).encode())

        def log_message(self, *args):
            pass

    with endpoint(Handler) as api:
        if matching:
            result = exchange(api, "private-invite", tmp_path, expected_kind=kind)
            assert result == payload | {"api": api}
            assert json.loads(path.read_text()) == result
        else:
            with pytest.raises(CredentialRejected, match="^exchange_kind_mismatch$"):
                exchange(api, "private-invite", tmp_path, expected_kind=kind)
            if existing:
                assert path.read_bytes() == b"existing-private-state"
            else:
                assert not path.exists()
    assert len(requests) == 1
    assert not path.with_suffix(".tmp").exists()
