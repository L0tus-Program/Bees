import socket
from types import SimpleNamespace
from uuid import uuid4

import pytest
from bees_host.guest_bridge import transport
from bees_host.guest_bridge.protocol import Binding, BridgeError


@pytest.fixture
def binding():
    return Binding(*(str(uuid4()) for _ in range(4)), generation=1)


class NativeSocket:
    def __init__(self, vm_id, peer_service=None):
        self.family = getattr(socket, "AF_HYPERV", 34)
        self.vm_id = vm_id
        self.peer_service = peer_service or "00010001-facb-11e6-bd58-64006a7986d3"
        self.closed = False
        self.calls = []

    def settimeout(self, value):
        self.calls.append(("timeout", value))

    def bind(self, value):
        self.calls.append(("bind", value))

    def listen(self, value):
        self.calls.append(("listen", value))

    def close(self):
        self.closed = True

    def getsockname(self):
        return (self.vm_id, transport.SERVICE_ID)

    def getpeername(self):
        return (self.vm_id, self.peer_service)


def test_unsupported_host_never_falls_back_to_tcp(binding, monkeypatch):
    monkeypatch.setattr(transport, "os", SimpleNamespace(name="posix"))
    monkeypatch.setattr(socket, "socket", lambda *args: pytest.fail("unexpected socket"))
    with pytest.raises(BridgeError, match="bridge_transport_unsupported"):
        transport.create_hyperv_listener(binding)


def test_registration_is_required_before_opening_socket(binding, monkeypatch):
    monkeypatch.setattr(transport, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(socket, "AF_HYPERV", 34, raising=False)
    monkeypatch.setattr(transport, "_registered", lambda: False)
    monkeypatch.setattr(socket, "socket", lambda *args: pytest.fail("unexpected socket"))
    with pytest.raises(BridgeError, match="bridge_service_unregistered"):
        transport.create_hyperv_listener(binding)


def test_native_factory_exact_vm_bind_and_no_any(binding, monkeypatch):
    native = NativeSocket(binding.vm_id)
    args = []
    monkeypatch.setattr(transport, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(socket, "AF_HYPERV", 34, raising=False)
    monkeypatch.setattr(socket, "HV_PROTOCOL_RAW", 1, raising=False)
    monkeypatch.setattr(transport, "_registered", lambda: True)
    monkeypatch.setattr(socket, "socket", lambda *value: args.append(value) or native)
    assert transport.create_hyperv_listener(binding) is native
    assert args == [(34, socket.SOCK_STREAM, 1)]
    assert ("bind", (binding.vm_id, "00000ac9-facb-11e6-bd58-64006a7986d3")) in native.calls
    assert ("listen", 1) in native.calls


def test_bind_failure_closes_socket_and_never_fallback(binding, monkeypatch):
    native = NativeSocket(binding.vm_id)
    monkeypatch.setattr(transport, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(socket, "AF_HYPERV", 34, raising=False)
    monkeypatch.setattr(socket, "HV_PROTOCOL_RAW", 1, raising=False)
    monkeypatch.setattr(transport, "_registered", lambda: True)
    monkeypatch.setattr(socket, "socket", lambda *args: native)

    def failed(value):
        raise OSError("private host information")

    native.bind = failed
    with pytest.raises(BridgeError, match="^bridge_transport_unavailable$"):
        transport.create_hyperv_listener(binding)
    assert native.closed


@pytest.mark.parametrize(
    "service",
    [
        "00010001-facb-11e6-bd58-64006a7986d3",
        transport.SERVICE_ID,
        "fffffffe-facb-11e6-bd58-64006a7986d3",
    ],
)
def test_kernel_peer_guest_ephemeral_port_is_valid(binding, monkeypatch, service):
    monkeypatch.setattr(socket, "AF_HYPERV", 34, raising=False)
    transport.check_hyperv_peer(NativeSocket(binding.vm_id, service), binding)


@pytest.mark.parametrize(
    "service",
    [
        "00000000-facb-11e6-bd58-64006a7986d3",
        "ffffffff-facb-11e6-bd58-64006a7986d3",
        str(uuid4()),
        "not a service",
    ],
)
def test_peer_service_namespace_not_arbitrary(binding, monkeypatch, service):
    monkeypatch.setattr(socket, "AF_HYPERV", 34, raising=False)
    with pytest.raises(BridgeError, match="bridge_peer_mismatch"):
        transport.check_hyperv_peer(NativeSocket(binding.vm_id, service), binding)


def test_vm_identity_from_kernel_not_frame(binding, monkeypatch):
    monkeypatch.setattr(socket, "AF_HYPERV", 34, raising=False)
    with pytest.raises(BridgeError, match="bridge_peer_mismatch"):
        transport.check_hyperv_peer(NativeSocket(str(uuid4())), binding)


def test_tcp_listener_cannot_enter_native_tls_path(binding):
    listener = socket.socket()
    try:
        with pytest.raises(BridgeError, match="bridge_transport_unsupported"):
            transport.accept_tls(listener, SimpleNamespace(binding=binding))
    finally:
        listener.close()
