import socket
import struct
import threading
import time
from uuid import uuid4

import pytest
from bees_host.guest_bridge.protocol import (
    MAX_FRAME,
    SPECIAL_VM_IDS,
    Binding,
    BridgeError,
    frame,
    receive_frame,
    send_frame,
    validate_frame,
)


def binding():
    return Binding(*(str(uuid4()) for _ in range(4)), generation=1)


@pytest.mark.parametrize(
    "raw",
    [
        b'{"protocol":1,"protocol":1}',
        b'{"status":NaN}',
        b'{"status":Infinity}',
        b"[]",
        b"null",
        b'"text"',
        b'{"secret":"\xff"}',
        b"{",
        b'{"x":' + b"[" * 2000 + b"]" * 2000 + b"}",
    ],
    ids=["duplicate", "nan", "infinity", "array", "null", "string", "utf8", "truncated", "nested"],
)
def test_invalid_json_never_surfaces_payload(raw):
    reader, writer = socket.socketpair()
    try:
        writer.sendall(struct.pack("!I", len(raw)) + raw)
        with pytest.raises(BridgeError) as caught:
            receive_frame(reader)
        assert str(caught.value) == "bridge_protocol_invalid"
        assert "secret" not in str(caught.value)
    finally:
        reader.close()
        writer.close()


@pytest.mark.parametrize("size", [0, MAX_FRAME + 1, 2**32 - 1])
def test_oversized_header_rejected_before_receiving_body(size):
    reader, writer = socket.socketpair()
    try:
        writer.sendall(struct.pack("!I", size))
        with pytest.raises(BridgeError, match="bridge_frame_size_invalid"):
            receive_frame(reader)
    finally:
        reader.close()
        writer.close()


def test_truncated_stream_fails_closed():
    reader, writer = socket.socketpair()
    try:
        writer.sendall(struct.pack("!I", 100) + b'{"secret":')
        writer.close()
        with pytest.raises(BridgeError, match="bridge_disconnected"):
            receive_frame(reader)
    finally:
        reader.close()
        writer.close()


def test_fragmentation_success_and_total_deadline():
    reader, writer = socket.socketpair()
    try:
        worker = threading.Thread(target=lambda: _fragment(writer))
        worker.start()
        started = time.monotonic()
        with pytest.raises(BridgeError, match="bridge_timeout"):
            receive_frame(reader, timeout=0.08)
        assert time.monotonic() - started < 0.3
        worker.join(timeout=1)
        assert not worker.is_alive()
    finally:
        reader.close()
        writer.close()


def _fragment(writer):
    for byte in struct.pack("!I", 2) + b"{}":
        writer.send(bytes([byte]))
        time.sleep(0.03)


def test_frame_roundtrip():
    reader, writer = socket.socketpair()
    try:
        value = frame(binding(), "challenge", nonce=str(uuid4()))
        send_frame(writer, value)
        assert receive_frame(reader) == value
    finally:
        reader.close()
        writer.close()


@pytest.mark.parametrize("generation", [True, 0, -1, 2**63, "1", 1.0])
def test_generation_cannot_be_coerced(generation):
    value = binding()
    with pytest.raises(BridgeError, match="bridge_binding_invalid"):
        Binding(**value.fields() | {"generation": generation})


@pytest.mark.parametrize("vm_id", sorted(SPECIAL_VM_IDS))
def test_wildcards_or_parent_cannot_be_identity(vm_id):
    with pytest.raises(BridgeError):
        Binding(**binding().fields() | {"vm_id": vm_id})


@pytest.mark.parametrize(
    "updates",
    [
        {"protocol": True},
        {"protocol": 2},
        {"type": "execute"},
        {"commands": []},
        {"generation": True},
        {"installation_id": str(uuid4())},
        {"nonce": "00000000-0000-0000-0000-000000000000"},
        {"nonce": str(uuid4()).upper()},
    ],
)
def test_exact_schema_and_binding(updates):
    value = binding()
    packet = frame(value, "challenge", nonce=str(uuid4())) | updates
    with pytest.raises(BridgeError):
        validate_frame(packet, value, "challenge", {"nonce"})


@pytest.mark.parametrize("timeout", [0, -1, float("nan"), float("inf"), 16, True, "1"])
def test_invalid_timeout_cannot_open_or_read_stream(timeout):
    with pytest.raises(BridgeError, match="bridge_timeout_invalid"):
        receive_frame(None, timeout)
