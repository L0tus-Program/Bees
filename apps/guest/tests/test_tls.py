import hashlib
import socket
import ssl
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from uuid import uuid4

import pytest
from tls_fixture import make_tls_fixture

from bees_guest import security
from bees_guest.errors import GuestError
from bees_guest.journal import Journal
from bees_guest.protocol import read_frame, write_frame
from bees_guest.runtime import GuestClient, connect
from bees_guest.security import verify_relay

STATUS = {"desktop_session": False, "chromium": True, "writer": True, "workspace": True}


@pytest.fixture
def tls(tmp_path, monkeypatch):
    monkeypatch.setattr(security, "check_private", lambda *_args, **_kwargs: None)
    return make_tls_fixture(tmp_path / "tls")


def exchange(tls, server_handler, client_handler, *, context=None):
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    listener.settimeout(3)

    def server():
        with listener:
            stream, _ = listener.accept()
            stream.settimeout(3)
            with tls.server.wrap_socket(stream, server_side=True) as secure:
                assert hashlib.sha256(secure.getpeercert(True)).hexdigest() == tls.guest_sha256
                write_frame(secure, {"test_ready": True})
                return server_handler(secure)

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(server)
        with socket.create_connection(listener.getsockname(), timeout=3) as stream:
            with (context or tls.client).wrap_socket(stream, server_hostname=None) as secure:
                assert read_frame(secure) == {"test_ready": True}
                result = client_handler(secure)
        return result, future.result(timeout=5)


def server_handshake(tls, secure, *, changed=None):
    challenge = tls.identity.binding.fields() | {
        "protocol": 1,
        "type": "challenge",
        "nonce": str(uuid4()),
    }
    write_frame(secure, challenge | (changed or {}))
    hello = read_frame(secure)
    assert hello["type"] == "hello" and hello["nonce"] == challenge["nonce"]
    write_frame(secure, hello | {"type": "accepted"})
    return challenge


def test_real_mtls_handshake_health_and_root_private_journal(tls):
    journal = Journal.initialize(tls.path / "diagnostic.sqlite3", tls.identity)

    def server(secure):
        challenge = server_handshake(tls, secure)
        request = tls.identity.binding.fields() | {
            "protocol": 1,
            "type": "health_request",
            "guest_id": tls.identity.guest_id,
            "request_id": str(uuid4()),
            "nonce": str(uuid4()),
            "session_nonce": challenge["nonce"],
        }
        write_frame(secure, request)
        result = read_frame(secure)
        assert result == request | {
            "type": "health",
            "sequence": 1,
            "status": STATUS,
            "cached": False,
        }
        return result

    try:
        healthy, result = exchange(
            tls,
            server,
            lambda secure: GuestClient(tls.identity, journal, probe=lambda: STATUS).run(
                secure, once=True
            ),
        )
        assert healthy and result["sequence"] == 1
    finally:
        journal.close()


@pytest.mark.parametrize("changed", [{"relay_cert_sha256": "0" * 64}, {"guest_id": str(uuid4())}])
def test_authenticated_cert_pin_and_uri_still_require_exact_configured_identity(tls, changed):
    def client(secure):
        with pytest.raises(GuestError, match="peer_identity_invalid"):
            verify_relay(secure, replace(tls.identity, **changed))

    exchange(tls, lambda _: None, client)


def test_plain_socket_and_unverified_context_never_accept_frames(tls):
    left, right = socket.socketpair()
    try:
        with pytest.raises(GuestError, match="peer_identity_invalid"):
            verify_relay(left, tls.identity)
    finally:
        left.close()
        right.close()
    unsafe = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    unsafe.check_hostname = False
    unsafe.verify_mode = ssl.CERT_NONE
    with pytest.raises(GuestError, match="tls_configuration_invalid"):
        connect(tls.identity, unsafe)


def test_private_tls_loader_enforces_verify_and_protocol(tls):
    context = security.tls_context(tls.path)
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.minimum_version == context.maximum_version == ssl.TLSVersion.TLSv1_3


def test_untrusted_ca_and_missing_client_certificate_are_rejected(tls, tmp_path):
    foreign = make_tls_fixture(tmp_path / "foreign")
    for context in (foreign.client, ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)):
        if context is not foreign.client:
            context.check_hostname = False
            context.minimum_version = context.maximum_version = ssl.TLSVersion.TLSv1_3
            context.load_verify_locations(cafile=tls.path / "ca.crt")
        with pytest.raises((ssl.SSLError, GuestError)):
            exchange(
                tls,
                lambda secure: read_frame(secure),
                lambda secure: read_frame(secure),
                context=context,
            )


def test_binding_mismatch_after_mtls_does_not_activate_or_probe(tls):
    journal = Journal.initialize(tls.path / "diagnostic.sqlite3", tls.identity)

    def server(secure):
        write_frame(
            secure,
            tls.identity.binding.fields()
            | {
                "protocol": 1,
                "type": "challenge",
                "nonce": str(uuid4()),
                "generation": 2,
            },
        )

    def client(secure):
        with pytest.raises(GuestError, match="binding_mismatch"):
            GuestClient(tls.identity, journal, probe=lambda: pytest.fail("mismatch probed")).run(
                secure, once=True
            )

    try:
        exchange(tls, server, client)
        assert journal.connection.execute("SELECT count(*) FROM sessions").fetchone() == (0,)
    finally:
        journal.close()
