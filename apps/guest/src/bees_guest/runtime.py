"""Cliente de saída. O único destino de produção é o host CID2/porta registrada."""

import socket
import ssl
import sys
from uuid import uuid4

from bees_guest import desktop
from bees_guest.errors import GuestError
from bees_guest.protocol import TIMEOUT_SECONDS, accepted, challenge, read_frame, write_frame
from bees_guest.security import verify_relay

MAX_REQUESTS_PER_SESSION = 100


def connect(identity, context):
    if (
        not isinstance(context, ssl.SSLContext)
        or context.verify_mode != ssl.CERT_REQUIRED
        or context.protocol != ssl.PROTOCOL_TLS_CLIENT
        or context.minimum_version != ssl.TLSVersion.TLSv1_3
        or context.maximum_version != ssl.TLSVersion.TLSv1_3
    ):
        raise GuestError("tls_configuration_invalid")
    if sys.platform != "linux" or not hasattr(socket, "AF_VSOCK"):
        raise GuestError("vsock_unavailable")
    stream = socket.socket(socket.AF_VSOCK, socket.SOCK_STREAM)
    secure = None
    try:
        stream.settimeout(TIMEOUT_SECONDS)
        stream.connect((socket.VMADDR_CID_HOST, identity.vsock_port))
        secure = context.wrap_socket(stream, server_hostname=None)
        verify_relay(secure, identity)
        return secure
    except (OSError, ssl.SSLError, GuestError):
        (secure if secure is not None else stream).close()
        raise GuestError("connection_unavailable") from None


class GuestClient:
    def __init__(self, identity, journal, *, probe=desktop.probe):
        self.identity, self.journal, self.probe = identity, journal, probe

    def run(self, stream, *, once=False):
        verify_relay(stream, self.identity)
        incoming = challenge(read_frame(stream), self.identity.binding)
        hello = incoming | {
            "type": "hello",
            "guest_id": self.identity.guest_id,
            "request_id": str(uuid4()),
        }
        write_frame(stream, hello)
        accepted(read_frame(stream), hello)
        self.journal.activate(incoming["nonce"])
        for _ in range(MAX_REQUESTS_PER_SESSION):
            request = read_frame(stream)
            response = self.journal.health(
                request,
                session_nonce=incoming["nonce"],
                probe=self.probe,
            )
            write_frame(stream, response)
            if once or response["type"] == "error":
                return response["type"] == "health"
        raise GuestError("session_limit")
