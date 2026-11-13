"""Uma pendência publicada durante o HTTP não autoriza truncamento local."""

import json
import os
from http.server import BaseHTTPRequestHandler

import pytest
from test_exchange_redirect import endpoint
from test_exchange_response import VALID

from bees_executor import client


def test_private_creation_writes_and_closes_descriptor(tmp_path):
    path = tmp_path / "credential.json"
    client._write_private(path, VALID)
    assert json.loads(path.read_text()) == VALID
    assert not path.with_suffix(".tmp").exists()
    if os.name != "nt":
        assert path.stat().st_mode & 0o777 == 0o600
    # O replace anterior também exige que o HANDLE Windows tenha sido fechado.
    path.unlink()


@pytest.mark.skipif(os.name != "nt", reason="Transferência de HANDLE nativa Windows.")
def test_windows_descriptor_failure_releases_handle_and_preserves_file(tmp_path, monkeypatch):
    import msvcrt

    path = tmp_path / "credential.tmp"

    def fail(*args):
        raise OSError("descriptor unavailable")

    monkeypatch.setattr(msvcrt, "open_osfhandle", fail)
    with pytest.raises(OSError, match="descriptor unavailable"):
        client._create_private(path)
    assert path.read_bytes() == b""
    # Falharia com sharing violation se o HANDLE exclusivo continuasse aberto.
    path.unlink()


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
