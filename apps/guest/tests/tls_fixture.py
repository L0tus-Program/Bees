"""Certificados efêmeros exclusivamente de teste; não são seed do guest/kit."""

import hashlib
import json
import ssl
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from bees_guest.protocol import Binding, certificate_uri
from bees_guest.security import Identity


def make_tls_fixture(path):
    path = Path(path)
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    binding = Binding(*(str(uuid4()) for _ in range(4)), generation=1)
    guest_id = str(uuid4())
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Bees disposable fixture CA")])
    now = datetime.now(UTC)

    def base(subject, public_key):
        return (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(name)
            .public_key(public_key)
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=1))
            .not_valid_after(now + timedelta(days=1))
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(public_key), critical=False)
            .add_extension(
                x509.AuthorityKeyIdentifier.from_issuer_public_key(key.public_key()), False
            )
        )

    ca = (
        base(name, key.public_key())
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.KeyUsage(False, False, False, False, False, True, True, False, False), True
        )
        .sign(key, hashes.SHA256())
    )
    path.joinpath("ca.crt").write_bytes(ca.public_bytes(serialization.Encoding.PEM))
    certificates = {}
    for role, filename, purpose in (
        ("host", "server", ExtendedKeyUsageOID.SERVER_AUTH),
        ("guest", "client", ExtendedKeyUsageOID.CLIENT_AUTH),
    ):
        leaf_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Bees fixture " + role)])
        leaf = (
            base(subject, leaf_key.public_key())
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), True)
            .add_extension(
                x509.KeyUsage(True, False, True, False, False, False, False, False, False), True
            )
            .add_extension(
                x509.SubjectAlternativeName(
                    [x509.UniformResourceIdentifier(certificate_uri(binding, guest_id, role))]
                ),
                False,
            )
            .add_extension(x509.ExtendedKeyUsage([purpose]), False)
            .sign(key, hashes.SHA256())
        )
        path.joinpath(filename + ".crt").write_bytes(leaf.public_bytes(serialization.Encoding.PEM))
        path.joinpath(filename + ".key").write_bytes(
            leaf_key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        )
        certificates[filename] = hashlib.sha256(
            leaf.public_bytes(serialization.Encoding.DER)
        ).hexdigest()
    path.joinpath("identity.json").write_text(
        json.dumps(
            binding.fields()
            | {
                "format": 1,
                "guest_id": guest_id,
                "relay_cert_sha256": certificates["server"],
                "vsock_port": 2761,
            }
        ),
        encoding="utf-8",
    )
    for entry in path.iterdir():
        entry.chmod(0o600)
    server = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server.minimum_version = server.maximum_version = ssl.TLSVersion.TLSv1_3
    server.verify_mode = ssl.CERT_REQUIRED
    server.verify_flags |= ssl.VERIFY_X509_STRICT
    server.load_verify_locations(cafile=path / "ca.crt")
    server.load_cert_chain(path / "server.crt", path / "server.key")
    client = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    client.minimum_version = client.maximum_version = ssl.TLSVersion.TLSv1_3
    client.check_hostname = False
    client.verify_mode = ssl.CERT_REQUIRED
    client.verify_flags |= ssl.VERIFY_X509_STRICT
    client.load_verify_locations(cafile=path / "ca.crt")
    client.load_cert_chain(path / "client.crt", path / "client.key")
    identity = Identity(binding, guest_id, certificates["server"], 2761)
    return SimpleNamespace(
        identity=identity,
        server=server,
        client=client,
        path=path,
        guest_sha256=certificates["client"],
    )
