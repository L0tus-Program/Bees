"""TLS TCP é fixture de protocolo, nunca comprovação de isolamento/AF_HYPERV real."""

import hashlib
import json
import queue
import shutil
import socket
import ssl
import tempfile
import threading
import time
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from bees_host.guest_bridge.protocol import (
    Binding,
    BridgeError,
    certificate_uri,
    frame,
    receive_frame,
    send_frame,
)
from bees_host.guest_bridge.session import HostSession, SessionFence
from bees_host.guest_bridge.tls import VSOCK_PORT, HostIdentity
from bees_host.security import check_private
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID


def make_bridge_material(directory: Path, binding=None, guest_id=None):
    """Fixture descartável interoperável, sem estado/identidade da instalação Bees."""
    binding = binding or Binding(*(str(uuid4()) for _ in range(4)), generation=1)
    guest_id = guest_id or str(uuid4())
    directory.mkdir()
    check_private(directory, directory=True, protect=True)
    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Bees fixture CA")])
    now = datetime.now(UTC)
    ca = (
        x509.CertificateBuilder()
        .subject_name(ca_name)
        .issuer_name(ca_name)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()), False)
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), False
        )
        .add_extension(
            x509.KeyUsage(False, False, False, False, False, True, True, False, False),
            critical=True,
        )
        .sign(ca_key, hashes.SHA256())
    )
    certs = {}
    for role, name in (("host", "server"), ("guest", "client")):
        key = ec.generate_private_key(ec.SECP256R1())
        subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Bees fixture " + role)])
        cert = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(ca_name)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=5))
            .not_valid_after(now + timedelta(days=1))
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), False)
            .add_extension(
                x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), False
            )
            .add_extension(
                x509.KeyUsage(True, False, False, False, False, False, False, False, False),
                critical=True,
            )
            .add_extension(
                x509.SubjectAlternativeName(
                    [x509.UniformResourceIdentifier(certificate_uri(binding, guest_id, role))]
                ),
                critical=False,
            )
            .add_extension(
                x509.ExtendedKeyUsage(
                    [
                        ExtendedKeyUsageOID.SERVER_AUTH
                        if role == "host"
                        else ExtendedKeyUsageOID.CLIENT_AUTH
                    ]
                ),
                critical=False,
            )
            .sign(ca_key, hashes.SHA256())
        )
        _write(directory / (name + ".crt"), cert.public_bytes(serialization.Encoding.PEM))
        _write(
            directory / (name + ".key"),
            key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            ),
        )
        certs[role] = cert
    _write(directory / "ca.crt", ca.public_bytes(serialization.Encoding.PEM))
    pins = {
        role: hashlib.sha256(cert.public_bytes(serialization.Encoding.DER)).hexdigest()
        for role, cert in certs.items()
    }
    config = {
        "format": 1,
        **binding.fields(),
        "guest_id": guest_id,
        "guest_cert_sha256": pins["guest"],
        "vsock_port": VSOCK_PORT,
    }
    _write(directory / "identity.json", json.dumps(config).encode())
    identity = HostIdentity.load(directory)
    client = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    client.minimum_version = client.maximum_version = ssl.TLSVersion.TLSv1_3
    client.check_hostname = False
    client.verify_mode = ssl.CERT_REQUIRED
    client.verify_flags |= ssl.VERIFY_X509_STRICT
    client.load_verify_locations(directory / "ca.crt")
    client.load_cert_chain(directory / "client.crt", directory / "client.key")
    return identity, client, pins


def _write(path, content):
    path.write_bytes(content)
    check_private(path, protect=True)


@contextmanager
def private_bridge_directory():
    """Temp global pode ser compartilhado; fixture própria sob home com ancestry guard."""
    from bees_host.guest_bridge.private import check_ancestors

    home = Path.home().resolve()
    directory = Path(tempfile.mkdtemp(prefix=".bees-bridge-test-", dir=home))
    check_private(directory, directory=True, protect=True)
    try:
        check_ancestors(directory)
        yield directory
    finally:
        if directory.parent.resolve() == home and directory.name.startswith(".bees-bridge-test-"):
            shutil.rmtree(directory)


@pytest.fixture
def material():
    with private_bridge_directory() as directory:
        yield make_bridge_material(directory / "private identity")


def exchange(material, guest, *, identity=None, fence=None):
    """Executa servidor real TLS em thread e guest fixture na thread principal."""
    source, client_context, _ = material
    identity = identity or source
    fence = fence or SessionFence(identity.binding, identity.guest_id)
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    listener.settimeout(5)
    result = queue.Queue()

    def serve():
        connection = None
        try:
            connection, _ = listener.accept()
            connection.settimeout(3)
            connection = identity.context.wrap_socket(connection, server_side=True)
            session = HostSession.handshake(connection, identity, fence, timeout=1)
            result.put(session.health())
        except Exception as error:
            result.put(error)
        finally:
            if connection is not None:
                connection.close()
            listener.close()

    worker = threading.Thread(target=serve)
    worker.start()
    connection = None
    try:
        connection = socket.create_connection(listener.getsockname(), timeout=3)
        connection = client_context.wrap_socket(connection, server_hostname=None)
        guest(connection, identity)
    finally:
        if connection is not None:
            connection.close()
        worker.join(timeout=6)
    assert not worker.is_alive()
    return result.get(timeout=1)


def hello(stream, identity, **updates):
    challenge = receive_frame(stream)
    response = frame(
        identity.binding,
        "hello",
        guest_id=identity.guest_id,
        request_id=str(uuid4()),
        nonce=challenge["nonce"],
    )
    send_frame(stream, response | updates)
    return receive_frame(stream)


def health_reply(stream, identity, **updates):
    request = receive_frame(stream)
    response = request | {
        "type": "health",
        "sequence": 1,
        "cached": False,
        "status": {"desktop_session": True, "chromium": True, "writer": True, "workspace": True},
    }
    send_frame(stream, response | updates)


def test_real_mtls_handshake_health(material):
    def guest(stream, identity):
        accepted = hello(stream, identity)
        assert accepted["type"] == "accepted"
        health_reply(stream, identity)

    result = exchange(material, guest)
    assert result.sequence == 1 and result.chromium and result.writer
    assert result.observed_monotonic is not None and not result.cached
    assert not hasattr(result, "usable") and not hasattr(result, "provisionable")


def test_cached_receipt_never_becomes_fresh(material):
    def guest(stream, identity):
        hello(stream, identity)
        health_reply(stream, identity, cached=True)

    result = exchange(material, guest)
    assert result.cached and result.observed_monotonic is None


@pytest.mark.parametrize(
    "updates,code",
    [
        ({"nonce": str(uuid4())}, "bridge_response_mismatch"),
        ({"request_id": str(uuid4())}, "bridge_response_mismatch"),
        ({"session_nonce": str(uuid4())}, "bridge_response_mismatch"),
        ({"generation": 2}, "bridge_binding_mismatch"),
        ({"sequence": True}, "bridge_sequence_invalid"),
        ({"sequence": 0}, "bridge_sequence_invalid"),
        ({"cached": "false"}, "bridge_protocol_invalid"),
        ({"status": {"shell": True}}, "bridge_protocol_invalid"),
        (
            {"status": {"desktop_session": True, "chromium": 1, "writer": True, "workspace": True}},
            "bridge_protocol_invalid",
        ),
        ({"command": "secret command"}, "bridge_protocol_invalid"),
    ],
)
def test_health_negative_correlation_schema_and_fencing(material, updates, code):
    def guest(stream, identity):
        hello(stream, identity)
        health_reply(stream, identity, **updates)

    result = exchange(material, guest)
    assert isinstance(result, BridgeError) and str(result) == code
    assert "secret" not in str(result)


def test_nonce_handshake_replay_rejected(material):
    def guest(stream, identity):
        challenge = receive_frame(stream)
        send_frame(
            stream,
            frame(
                identity.binding,
                "hello",
                guest_id=identity.guest_id,
                request_id=str(uuid4()),
                nonce=str(uuid4()),
            ),
        )
        assert challenge["nonce"]

    assert str(exchange(material, guest)) == "bridge_handshake_mismatch"


def test_valid_ca_wrong_guest_pin_rejected(material):
    source, _, _ = material
    identity = replace(source, guest_cert_sha256="0" * 64)
    assert str(exchange(material, lambda stream, identity: None, identity=identity)) == (
        "bridge_certificate_pin_mismatch"
    )


def test_certificate_snapshot_not_json_is_required(material):
    source, _, _ = material
    binding = Binding(
        source.binding.installation_id,
        str(uuid4()),
        source.binding.job_id,
        source.binding.vm_id,
        source.binding.generation,
    )
    identity = replace(source, binding=binding)
    assert str(exchange(material, lambda stream, identity: None, identity=identity)) == (
        "bridge_certificate_binding_invalid"
    )


def test_fence_old_connection_and_revocation_are_terminal(material):
    identity, _, _ = material
    fence = SessionFence(identity.binding, identity.guest_id)
    first, second = socket.socket(), socket.socket()
    try:
        fence.activate("old", first)
        fence.activate("new", second)
        assert first.fileno() == -1
        with pytest.raises(BridgeError, match="bridge_session_fenced"):
            fence.observe("old", 1, False)
        assert fence.observe("new", 1, False) is not None
        with pytest.raises(BridgeError, match="bridge_sequence_invalid"):
            fence.observe("new", 1, False)
        assert fence.observe("new", 1, True) is None
        fence.revoke()
        assert second.fileno() == -1
        with socket.socket() as third:
            with pytest.raises(BridgeError, match="bridge_session_revoked"):
                fence.activate("next", third)
    finally:
        first.close()
        second.close()


def test_plaintext_socket_rejected_before_frames(material):
    identity, _, _ = material
    stream = socket.socket()
    with pytest.raises(BridgeError, match="bridge_tls_required"):
        HostSession.handshake(stream, identity, SessionFence(identity.binding, identity.guest_id))
    assert stream.fileno() == -1


def test_health_timeout_is_not_a_fresh_observation(material):
    def guest(stream, identity):
        hello(stream, identity)
        receive_frame(stream)
        time.sleep(1.1)

    assert str(exchange(material, guest)) == "bridge_timeout"


@pytest.mark.parametrize("code", ["request_unknown", "request_conflict", "session_fenced"])
def test_guest_error_remains_fixed_without_retry(material, code):
    def guest(stream, identity):
        hello(stream, identity)
        request = receive_frame(stream)
        send_frame(stream, request | {"type": "error", "code": code})

    assert str(exchange(material, guest)) == "bridge_" + code


def test_invalid_error_value_is_sanitized(material):
    def guest(stream, identity):
        hello(stream, identity)
        request = receive_frame(stream)
        send_frame(stream, request | {"type": "error", "code": {"secret": "private"}})

    assert str(exchange(material, guest)) == "bridge_protocol_invalid"


def test_expired_local_certificate_profile_blocks_even_valid_peer(material):
    identity = replace(material[0], server_not_after=datetime.now(UTC) - timedelta(seconds=1))
    assert str(exchange(material, lambda stream, identity: None, identity=identity)) == (
        "bridge_certificate_expired"
    )


@pytest.mark.parametrize("mode", ["missing_client", "other_ca", "tls12"])
def test_real_tls_rejects_unauthenticated_ca_and_old_version(material, mode):
    host, context, _ = material
    other = None
    if mode == "missing_client":
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.check_hostname = False
        authority_der = material[1].get_ca_certs(binary_form=True)[0]
        context.load_verify_locations(cadata=ssl.DER_cert_to_PEM_cert(authority_der))
    elif mode == "tls12":
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        context.minimum_version = context.maximum_version = ssl.TLSVersion.TLSv1_2
    else:
        other = private_bridge_directory()
        base = other.__enter__()
        context = make_bridge_material(base / "other identity")[1]
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    listener.settimeout(2)
    result = queue.Queue()

    def serve():
        connection = None
        try:
            connection, _ = listener.accept()
            connection.settimeout(2)
            connection = host.context.wrap_socket(connection, server_side=True)
            result.put(None)
        except (ssl.SSLError, ConnectionError) as error:
            result.put(type(error))
        finally:
            if connection is not None:
                connection.close()
            listener.close()

    worker = threading.Thread(target=serve)
    worker.start()
    client = None
    try:
        client = socket.create_connection(listener.getsockname(), timeout=2)
        try:
            client = context.wrap_socket(client, server_hostname=None)
            client.send(b"fixture")
            client.recv(1)
        except ssl.SSLError, ConnectionError:
            pass
    finally:
        if client is not None:
            client.close()
        worker.join(timeout=5)
        if other is not None:
            other.__exit__(None, None, None)
    assert not worker.is_alive()
    assert issubclass(result.get(timeout=1), (ssl.SSLError, ConnectionError))
