"""Configuração root privada; certificados nunca vêm do canal de diagnóstico."""

import hashlib
import os
import re
import ssl
import stat
from dataclasses import dataclass
from pathlib import Path

from bees_guest.errors import GuestError
from bees_guest.protocol import VSOCK_PORT, Binding, canonical_uuid, certificate_uri, decode, exact


def check_private(path: Path, *, directory=False):
    if os.name != "posix" or not path.is_absolute():
        raise GuestError("private_path_required")
    try:
        entry = path.lstat()
        expected = stat.S_ISDIR if directory else stat.S_ISREG
        if (
            not expected(entry.st_mode)
            or entry.st_uid != 0
            or (entry.st_mode & 0o077)
            or (not directory and entry.st_nlink != 1)
        ):
            raise GuestError("private_path_required")
        for parent in path.parents:
            entry = parent.lstat()
            if not stat.S_ISDIR(entry.st_mode) or entry.st_uid != 0 or entry.st_mode & 0o022:
                raise GuestError("private_path_required")
    except OSError:
        raise GuestError("private_path_required") from None


@dataclass(frozen=True, repr=False)
class Identity:
    binding: Binding
    guest_id: str
    relay_cert_sha256: str
    vsock_port: int

    def __post_init__(self):
        canonical_uuid(self.guest_id)
        if not isinstance(self.relay_cert_sha256, str) or not re.fullmatch(
            r"[0-9a-f]{64}", self.relay_cert_sha256
        ):
            raise GuestError("identity_invalid")
        if type(self.vsock_port) is not int or self.vsock_port != VSOCK_PORT:
            raise GuestError("identity_invalid")

    def __repr__(self):
        return "<Identity private>"


def load_identity(directory):
    directory = Path(directory)
    check_private(directory, directory=True)
    path = directory / "identity.json"
    check_private(path)
    try:
        with path.open("rb") as source:
            value = decode(source.read(16385))
        exact(
            value,
            {
                "format",
                "installation_id",
                "environment_id",
                "job_id",
                "vm_id",
                "generation",
                "guest_id",
                "relay_cert_sha256",
                "vsock_port",
            },
        )
        if type(value["format"]) is not int or value["format"] != 1:
            raise GuestError("identity_invalid")
        binding = Binding(
            **{
                key: value[key]
                for key in ("installation_id", "environment_id", "job_id", "vm_id", "generation")
            }
        )
        return Identity(binding, value["guest_id"], value["relay_cert_sha256"], value["vsock_port"])
    except (OSError, ValueError, TypeError, GuestError):
        raise GuestError("identity_invalid") from None


def tls_context(directory):
    directory = Path(directory)
    check_private(directory, directory=True)
    for name in ("ca.crt", "client.crt", "client.key"):
        path = directory / name
        check_private(path)
        if path.stat().st_size > 65536:
            raise GuestError("tls_configuration_invalid")
    try:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.minimum_version = context.maximum_version = ssl.TLSVersion.TLSv1_3
        # Identidade não é DNS: CA, pin DER e SAN URI completo são obrigatórios.
        context.check_hostname = False
        context.verify_mode = ssl.CERT_REQUIRED
        context.verify_flags |= ssl.VERIFY_X509_STRICT
        context.load_verify_locations(cafile=directory / "ca.crt")
        context.load_cert_chain(directory / "client.crt", directory / "client.key")
        return context
    except (OSError, ssl.SSLError):
        raise GuestError("tls_configuration_invalid") from None


def verify_relay(stream, identity):
    if (
        not isinstance(stream, ssl.SSLSocket)
        or stream.server_side
        or stream.context.verify_mode != ssl.CERT_REQUIRED
        or stream.context.minimum_version != ssl.TLSVersion.TLSv1_3
        or stream.context.maximum_version != ssl.TLSVersion.TLSv1_3
        or stream.version() != "TLSv1.3"
    ):
        raise GuestError("peer_identity_invalid")
    der = stream.getpeercert(binary_form=True)
    certificate = stream.getpeercert()
    if (
        not der
        or hashlib.sha256(der).hexdigest() != identity.relay_cert_sha256
        or certificate.get("subjectAltName")
        != (("URI", certificate_uri(identity.binding, identity.guest_id, "host")),)
    ):
        raise GuestError("peer_identity_invalid")
