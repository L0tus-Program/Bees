"""Identidade por VM fornecida por operador confiável; sem emissão ou bootstrap remoto."""

import hashlib
import json
import os
import re
import ssl
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives.serialization import Encoding
from cryptography.x509.oid import ExtendedKeyUsageOID

from bees_host.errors import HostError
from bees_host.guest_bridge.private import check_ancestors
from bees_host.guest_bridge.protocol import Binding, BridgeError, certificate_uri, uuid_text
from bees_host.security import check_private

VSOCK_PORT = 2761


def _private_file(path: Path) -> bytes:
    try:
        check_private(path)
        check_ancestors(path)
        if path.stat().st_nlink != 1:
            raise BridgeError("bridge_identity_private_required")
        if not 0 < path.stat().st_size <= 65536:
            raise BridgeError("bridge_identity_invalid")
        with path.open("rb") as source:
            info = os.fstat(source.fileno())
            if info.st_ino != path.stat().st_ino or info.st_nlink != 1:
                raise BridgeError("bridge_identity_private_required")
            value = source.read(65537)
        if not 0 < len(value) <= 65536:
            raise BridgeError("bridge_identity_invalid")
        return value
    except HostError, OSError:
        raise BridgeError("bridge_identity_private_required") from None


def check_certificate(der: bytes, expected_uri: str, *, guest: bool) -> None:
    try:
        cert = x509.load_der_x509_certificate(der)
        names = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        if list(names) != [x509.UniformResourceIdentifier(expected_uri)]:
            raise BridgeError("bridge_certificate_binding_invalid")
        if cert.extensions.get_extension_for_class(x509.BasicConstraints).value.ca:
            raise BridgeError("bridge_certificate_binding_invalid")
        usage = cert.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value
        role = ExtendedKeyUsageOID.CLIENT_AUTH if guest else ExtendedKeyUsageOID.SERVER_AUTH
        if list(usage) != [role]:
            raise BridgeError("bridge_certificate_binding_invalid")
    except ValueError, x509.ExtensionNotFound:
        raise BridgeError("bridge_certificate_binding_invalid") from None


@dataclass(frozen=True)
class HostIdentity:
    binding: Binding
    guest_id: str
    guest_cert_sha256: str
    context: ssl.SSLContext
    server_not_before: datetime
    server_not_after: datetime

    @classmethod
    def load(cls, directory: Path) -> HostIdentity:
        """Arquivos privados fixos; nenhum caminho ou configuração recebido do guest."""
        try:
            check_private(directory, directory=True)
            check_ancestors(directory)
            raw = _private_file(directory / "identity.json")
            config = json.loads(raw, object_pairs_hook=_unique_pairs)
            fields = {
                "format",
                "installation_id",
                "environment_id",
                "job_id",
                "vm_id",
                "generation",
                "guest_id",
                "guest_cert_sha256",
                "vsock_port",
            }
            if type(config) is not dict or set(config) != fields:
                raise BridgeError("bridge_identity_invalid")
            if type(config["format"]) is not int or config["format"] != 1:
                raise BridgeError("bridge_identity_invalid")
            if type(config["vsock_port"]) is not int or config["vsock_port"] != VSOCK_PORT:
                raise BridgeError("bridge_identity_invalid")
            binding = Binding(**{name: config[name] for name in Binding.__annotations__})
            guest_id = uuid_text(config["guest_id"])
            pin = config["guest_cert_sha256"]
            if type(pin) is not str or not re.fullmatch("[0-9a-f]{64}", pin):
                raise BridgeError("bridge_identity_invalid")
            ca = _private_file(directory / "ca.crt")
            cert = _private_file(directory / "server.crt")
            _private_file(directory / "server.key")
            ca_certs, server_certs = (
                x509.load_pem_x509_certificates(value) for value in (ca, cert)
            )
            if len(ca_certs) != 1 or len(server_certs) != 1:
                raise BridgeError("bridge_identity_invalid")
            parsed, authority = server_certs[0], ca_certs[0]
            if not authority.extensions.get_extension_for_class(x509.BasicConstraints).value.ca:
                raise BridgeError("bridge_identity_invalid")
            check_certificate(
                parsed.public_bytes(Encoding.DER),
                certificate_uri(binding, guest_id, "host"),
                guest=False,
            )
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.minimum_version = context.maximum_version = ssl.TLSVersion.TLSv1_3
            context.verify_mode = ssl.CERT_REQUIRED
            context.verify_flags |= ssl.VERIFY_X509_STRICT
            context.options |= ssl.OP_NO_COMPRESSION | ssl.OP_NO_TICKET
            context.num_tickets = 0
            context.load_verify_locations(cadata=ca.decode("ascii"))
            context.load_cert_chain(directory / "server.crt", directory / "server.key")
            return cls(
                binding,
                guest_id,
                pin,
                context,
                max(parsed.not_valid_before_utc, authority.not_valid_before_utc),
                min(parsed.not_valid_after_utc, authority.not_valid_after_utc),
            )
        except (
            HostError,
            OSError,
            ValueError,
            TypeError,
            KeyError,
            UnicodeError,
            RecursionError,
            x509.ExtensionNotFound,
        ):
            raise BridgeError("bridge_identity_invalid") from None

    def check_peer(self, stream: ssl.SSLSocket) -> None:
        try:
            now = datetime.now(UTC)
            if not self.server_not_before <= now < self.server_not_after:
                raise BridgeError("bridge_certificate_expired")
            der = stream.getpeercert(binary_form=True)
            if stream.version() != "TLSv1.3" or not der:
                raise BridgeError("bridge_tls_required")
            if hashlib.sha256(der).hexdigest() != self.guest_cert_sha256:
                raise BridgeError("bridge_certificate_pin_mismatch")
            check_certificate(
                der, certificate_uri(self.binding, self.guest_id, "guest"), guest=True
            )
            peer = x509.load_der_x509_certificate(der)
            if not peer.not_valid_before_utc <= now < peer.not_valid_after_utc:
                raise BridgeError("bridge_certificate_expired")
        except ssl.SSLError, ValueError:
            raise BridgeError("bridge_tls_failed") from None


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise BridgeError("bridge_identity_invalid")
        result[key] = value
    return result
