"""Uma pendência publicada durante o HTTP não autoriza truncamento local."""

import json
from http.server import BaseHTTPRequestHandler

import pytest
from test_exchange_redirect import endpoint
from test_exchange_response import VALID

from bees_executor import client


@pytest.mark.parametrize("existing", [False, True])
@pytest.mark.parametrize(
    "obstacle", ["complete", "partial", "empty", "directory", "symlink", "dangling"]
)
def test_exchange_preserves_temporary_created_during_http(tmp_path, existing, obstacle):
    saved = tmp_path / "credential.json"
    temporary = tmp_path / "credential.tmp"
    target = tmp_path / "private-target"
    if existing:
        saved.write_bytes(b"original-private-identity")
    if obstacle == "symlink":
        target.write_bytes(b"original-private-target")
    if obstacle in ("symlink", "dangling"):
        try:
            temporary.symlink_to(target)
            temporary.unlink()
        except OSError:
            pytest.skip("Symlink indisponível nesta conta.")
    requests = []
    snapshots = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            requests.append(self.rfile.read(int(self.headers["Content-Length"])))
            # A chamada HTTP só chega aqui depois do preflight do executor.
            if obstacle == "directory":
                temporary.mkdir()
                (temporary / "preserve").write_bytes(b"private-evidence")
            elif obstacle in ("symlink", "dangling"):
                temporary.symlink_to(target)
            else:
                temporary.write_bytes(
                    {
                        "complete": json.dumps(VALID).encode(),
                        "partial": b'{"private":',
                        "empty": b"",
                    }[obstacle]
                )
            snapshots.append(temporary.lstat())
            self.send_response(200)
            self.end_headers()
            self.wfile.write(json.dumps(VALID).encode())

        def log_message(self, *args):
            pass

    with endpoint(Handler) as api:
        with pytest.raises(
            client.CredentialPersistenceError, match="^executor_credential_save_failed$"
        ) as caught:
            client.exchange(api, "private-invite", tmp_path)
    assert caught.value.__suppress_context__
    assert len(requests) == 1
    if existing:
        assert saved.read_bytes() == b"original-private-identity"
    else:
        assert not saved.exists()
    assert temporary.lstat() == snapshots[0]
    if obstacle == "symlink":
        assert target.read_bytes() == b"original-private-target"
    elif obstacle == "dangling":
        assert not target.exists()
    elif obstacle == "directory":
        assert (temporary / "preserve").read_bytes() == b"private-evidence"
    else:
        assert (
            temporary.read_bytes()
            == {"complete": json.dumps(VALID).encode(), "partial": b'{"private":', "empty": b""}[
                obstacle
            ]
        )
