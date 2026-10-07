"""AF_HYPERV exclusivo e registro somente leitura; nunca abre TCP como alternativa.

Endereços e registro: learn.microsoft.com/en-us/windows-server/virtualization/
hyper-v/make-integration-service; tuple nativa: docs.python.org/3.14/library/socket.html.
"""

import os
import socket
import ssl
from uuid import UUID

from bees_host.guest_bridge.protocol import Binding, BridgeError, _timeout
from bees_host.guest_bridge.tls import VSOCK_PORT, HostIdentity

SERVICE_ID = f"{VSOCK_PORT:08x}-facb-11e6-bd58-64006a7986d3"
SERVICE_NAME = "Bees guest bridge v1"


def _registered() -> bool:
    try:
        import winreg

        path = (
            r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Virtualization"
            "\\GuestCommunicationServices\\" + SERVICE_ID
        )
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE, path, 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY
        ) as key:
            value, kind = winreg.QueryValueEx(key, "ElementName")
            return kind == winreg.REG_SZ and value == SERVICE_NAME
    except ImportError, OSError:
        return False


def create_hyperv_listener(binding: Binding, timeout: float = 5.0) -> socket.socket:
    _timeout(timeout)
    if os.name != "nt" or not hasattr(socket, "AF_HYPERV"):
        raise BridgeError("bridge_transport_unsupported")
    if not _registered():
        raise BridgeError("bridge_service_unregistered")
    stream = None
    try:
        stream = socket.socket(socket.AF_HYPERV, socket.SOCK_STREAM, socket.HV_PROTOCOL_RAW)
        stream.settimeout(timeout)
        stream.bind((binding.vm_id, SERVICE_ID))
        stream.listen(1)
        return stream
    except OSError:
        if stream is not None:
            stream.close()
        raise BridgeError("bridge_transport_unavailable") from None


def check_hyperv_peer(stream: socket.socket, binding: Binding) -> None:
    try:
        expected = (binding.vm_id, SERVICE_ID)
        if stream.family != getattr(socket, "AF_HYPERV", None):
            raise BridgeError("bridge_peer_mismatch")
        # Identidade obtida do kernel, antes de TLS/JSON; wildcard nunca é confiável.
        peer = tuple(part.lower() for part in stream.getpeername())
        if len(peer) != 2 or peer[0] != binding.vm_id:
            raise BridgeError("bridge_peer_mismatch")
        # Linux hvs_connect derives the peer ServiceId from its LOCAL ephemeral port.
        # Kernel source: net/vmw_vsock/hyperv_transport.c hvs_connect.
        peer_service = UUID(peer[1])
        if (
            str(peer_service) != peer[1]
            or peer[1][8:] != SERVICE_ID[8:]
            or not 1 <= peer_service.fields[0] <= 0xFFFFFFFE
        ):
            raise BridgeError("bridge_peer_mismatch")
        if tuple(part.lower() for part in stream.getsockname()) != expected:
            raise BridgeError("bridge_peer_mismatch")
    except OSError, TypeError, AttributeError, ValueError:
        raise BridgeError("bridge_peer_mismatch") from None


def accept_tls(listener: socket.socket, identity: HostIdentity, timeout: float = 5.0):
    _timeout(timeout)
    connection = None
    try:
        if listener.family != getattr(socket, "AF_HYPERV", None):
            raise BridgeError("bridge_transport_unsupported")
        listener.settimeout(timeout)
        connection, _ = listener.accept()
        check_hyperv_peer(connection, identity.binding)
        connection.settimeout(timeout)
        connection = identity.context.wrap_socket(connection, server_side=True)
        identity.check_peer(connection)
        return connection
    except BridgeError:
        if connection is not None:
            connection.close()
        raise
    except OSError, ssl.SSLError:
        if connection is not None:
            connection.close()
        raise BridgeError("bridge_tls_failed") from None
