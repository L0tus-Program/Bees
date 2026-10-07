"""Smoke Python 3.13/root em laboratório isolado; somente biblioteca padrão.

Recebe certificados efêmeros já gerados no checkout via --root. Não emite
identidade, não inicializa produção e não configura serviços/hipervisor.
"""

import argparse
import hashlib
import json
import os
import socket
import ssl
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

from bees_guest.desktop import probe
from bees_guest.errors import GuestError
from bees_guest.journal import Journal
from bees_guest.protocol import certificate_uri, read_frame, write_frame
from bees_guest.runtime import GuestClient
from bees_guest.security import check_private, load_identity, tls_context


def request(identity, session_nonce):
    return identity.binding.fields() | {
        "protocol": 1,
        "type": "health_request",
        "guest_id": identity.guest_id,
        "request_id": str(uuid4()),
        "nonce": str(uuid4()),
        "session_nonce": session_nonce,
    }


def expect_error(code, callback):
    try:
        callback()
    except GuestError as error:
        assert error.code == code, error.code
    else:
        raise AssertionError("missing_fixed_error")


def never_probe():
    raise AssertionError("observation_repeated")


def run(root):
    identity = load_identity(root)
    client_context = tls_context(root)
    for name in ("server.crt", "server.key"):
        check_private(root / name)
    server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_context.minimum_version = server_context.maximum_version = ssl.TLSVersion.TLSv1_3
    server_context.verify_mode = ssl.CERT_REQUIRED
    server_context.verify_flags |= ssl.VERIFY_X509_STRICT
    server_context.load_verify_locations(cafile=root / "ca.crt")
    server_context.load_cert_chain(root / "server.crt", root / "server.key")
    expected_pin = hashlib.sha256(
        ssl.PEM_cert_to_DER_cert((root / "client.crt").read_text(encoding="ascii"))
    ).hexdigest()
    actual_status = probe()
    assert actual_status == {
        "desktop_session": False,
        "chromium": True,
        "writer": True,
        "workspace": True,
    }, "desktop_payload_missing"
    path = root / "diagnostic.sqlite3"
    journal = Journal.initialize(path, identity)
    assert journal.connection.execute("PRAGMA journal_mode").fetchone() == ("delete",)
    assert journal.connection.execute("PRAGMA synchronous").fetchone() == (2,)
    nonce = str(uuid4())
    operation = request(identity, nonce)
    challenge = identity.binding.fields() | {"protocol": 1, "type": "challenge", "nonce": nonce}

    def relay(listener):
        raw, _ = listener.accept()
        raw.settimeout(5)
        with server_context.wrap_socket(raw, server_side=True) as stream:
            assert hashlib.sha256(stream.getpeercert(binary_form=True)).hexdigest() == expected_pin
            assert stream.getpeercert()["subjectAltName"] == (
                (
                    "URI",
                    certificate_uri(identity.binding, identity.guest_id, "guest"),
                ),
            )
            write_frame(stream, challenge)
            hello = read_frame(stream)
            assert hello == challenge | {
                "type": "hello",
                "guest_id": identity.guest_id,
                "request_id": hello["request_id"],
            }
            write_frame(stream, hello | {"type": "accepted"})
            write_frame(stream, operation)
            return read_frame(stream)

    with socket.socket() as listener, ThreadPoolExecutor(max_workers=1) as pool:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        listener.settimeout(5)
        future = pool.submit(relay, listener)
        with socket.create_connection(listener.getsockname(), timeout=5) as raw:
            with client_context.wrap_socket(raw, server_hostname=None) as stream:
                assert GuestClient(identity, journal).run(stream, once=True)
        response = future.result(timeout=10)
    assert response["status"] == actual_status and response["cached"] is False
    assert response["sequence"] == 1
    replay = journal.health(operation, session_nonce=nonce, probe=never_probe)
    assert replay == response | {"cached": True}
    journal.close()
    journal = Journal(path, identity)
    assert journal.health(operation, session_nonce=nonce, probe=never_probe) == replay

    # O subprocesso morre depois do receipt prepared, antes da observação.
    crashing = request(identity, nonce)
    script = """
import json,os,sys
from pathlib import Path
from bees_guest.security import load_identity
from bees_guest.journal import Journal
root=Path(sys.argv[1])
journal=Journal(root/'diagnostic.sqlite3',load_identity(root))
request=json.loads(sys.argv[2])
journal.health(request,session_nonce=request['session_nonce'],probe=lambda:os._exit(23))
"""
    child = subprocess.run(
        [sys.executable, "-c", script, str(root), json.dumps(crashing)],
        capture_output=True,
        timeout=10,
        check=False,
    )
    assert child.returncode == 23, "crash_fixture_failed"
    journal.close()
    journal = Journal(path, identity)
    assert (
        journal.health(crashing, session_nonce=nonce, probe=never_probe)["code"]
        == "request_unknown"
    )
    journal.close()

    # Uma nova sessão de outro processo impede a sessão antiga de publicar.
    script = """
import sys
from pathlib import Path
from bees_guest.security import load_identity
from bees_guest.journal import Journal
root=Path(sys.argv[1])
journal=Journal(root/'diagnostic.sqlite3',load_identity(root))
journal.activate(sys.argv[2])
journal.close()
"""
    subprocess.run(
        [sys.executable, "-c", script, str(root), str(uuid4())],
        capture_output=True,
        timeout=10,
        check=True,
    )
    journal = Journal(path, identity)
    assert (
        journal.health(operation, session_nonce=nonce, probe=never_probe)["code"]
        == "session_fenced"
    )
    journal.close()

    # Perder DB ou marcador não autoriza criação implícita nem reset parcial.
    path.unlink()
    expect_error("journal_missing", lambda: Journal(path, identity))
    expect_error("journal_already_present", lambda: Journal.initialize(path, identity))
    assert not path.exists()
    marker_lost = root / "marker-lost.sqlite3"
    Journal.initialize(marker_lost, identity).close()
    Journal.marker_path(marker_lost).unlink()
    original = marker_lost.read_bytes()
    expect_error("journal_missing", lambda: Journal(marker_lost, identity))
    expect_error("journal_already_present", lambda: Journal.initialize(marker_lost, identity))
    assert marker_lost.read_bytes() == original
    corrupted = root / "corrupt.sqlite3"
    Journal.initialize(corrupted, identity).close()
    corrupted.write_bytes(b"disposable corrupt database")
    expect_error("journal_invalid", lambda: Journal(corrupted, identity))

    # A verificação CLI não reconstitui o DB removido e não precisa de VSOCK.
    child = subprocess.run(
        [sys.executable, "-m", "bees_guest.cli", "--identity-dir", str(root), "--check"],
        capture_output=True,
        timeout=10,
        check=True,
    )
    assert json.loads(child.stdout) == {"configured": True, "vm_ready": False}
    assert not path.exists()
    print(
        json.dumps(
            {
                "python": sys.version.split()[0],
                "tls": "TLSv1.3",
                "journal": "delete/full",
                "private_root": True,
                "desktop_payload": True,
                "crash_unknown": True,
                "replay_cached": True,
                "process_fencing": True,
                "lost_state_closed": True,
                "vm_ready": False,
            }
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    if sys.platform != "linux" or os.getuid() != 0:
        raise SystemExit("linux_root_lab_required")
    run(args.root)
