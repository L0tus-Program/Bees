import socket
import struct
from dataclasses import replace
from uuid import uuid4

import pytest

from bees_guest.errors import GuestError
from bees_guest.protocol import (
    MAX_FRAME,
    Binding,
    accepted,
    canonical_uuid,
    challenge,
    decode,
    health_request,
    read_frame,
    status,
    write_frame,
)


@pytest.fixture
def binding():
    return Binding(*(str(uuid4()) for _ in range(4)), generation=1)


@pytest.mark.parametrize(
    "data",
    [
        b'{"private-secret":1,"private-secret":2}',
        b'{"field":NaN}',
        b'{"field":Infinity}',
        b"[]",
        b"null",
        b"\xff",
        b"{",
        b'{"x":"\x00"}',
        b"{" * 12000,
        b"x" * (MAX_FRAME + 1),
    ],
)
def test_malicious_json_is_rejected_without_content(data):
    with pytest.raises(GuestError) as error:
        decode(data)
    assert "private-secret" not in str(error.value)


@pytest.mark.parametrize(
    "value", ["0" * 32, "00000000-0000-0000-0000-000000000000", 1, True, "../x"]
)
def test_uuid_is_canonical_nonzero(value):
    with pytest.raises(GuestError):
        canonical_uuid(value)


@pytest.mark.parametrize("generation", [True, 0, -1, 2**63, 1.0, "1"])
def test_generation_type_and_bounds(binding, generation):
    with pytest.raises(GuestError):
        replace(binding, generation=generation)


def test_binding_and_frames_are_closed(binding):
    original = binding.fields() | {"protocol": 1, "type": "challenge", "nonce": str(uuid4())}
    assert challenge(original, binding) == original
    for change in ({"protocol": True}, {"generation": 2}, {"vm_id": str(uuid4())}, {"shell": "id"}):
        with pytest.raises(GuestError):
            challenge(original | change, binding)
    hello = original | {"type": "hello", "guest_id": str(uuid4()), "request_id": str(uuid4())}
    accepted(hello | {"type": "accepted"}, hello)
    with pytest.raises(GuestError):
        accepted(hello | {"type": "accepted", "nonce": str(uuid4())}, hello)
    request = hello | {"type": "health_request", "session_nonce": original["nonce"]}
    health_request(request, binding, hello["guest_id"])
    with pytest.raises(GuestError):
        health_request(request | {"command": "whoami"}, binding, hello["guest_id"])
    with pytest.raises(GuestError):
        status({"desktop_session": 1, "chromium": False, "writer": False, "workspace": True})


def test_framing_multiple_frames_and_short_chunks():
    left, right = socket.socketpair()
    try:
        write_frame(left, {"first": True})
        write_frame(left, {"second": False})
        assert read_frame(right) == {"first": True}
        assert read_frame(right) == {"second": False}
    finally:
        left.close()
        right.close()


@pytest.mark.parametrize("length", [0, MAX_FRAME + 1, 2**32 - 1])
def test_frame_length_rejected_before_body(length):
    left, right = socket.socketpair()
    try:
        left.sendall(struct.pack("!I", length))
        with pytest.raises(GuestError, match="frame_invalid"):
            read_frame(right)
    finally:
        left.close()
        right.close()


def test_truncated_frame_and_timeout():
    left, right = socket.socketpair()
    try:
        left.sendall(struct.pack("!I", 10) + b"{}")
        with pytest.raises(GuestError, match="transport_timeout"):
            read_frame(right, timeout=0.02)
        left.close()
        with pytest.raises(GuestError, match="transport_closed"):
            read_frame(right)
    finally:
        right.close()


@pytest.mark.parametrize("timeout", [True, 0, -1, float("nan"), float("inf"), 16])
def test_timeout_invalid(timeout):
    with pytest.raises(GuestError, match="timeout_invalid"):
        read_frame(None, timeout=timeout)
    with pytest.raises(GuestError, match="timeout_invalid"):
        write_frame(None, {}, timeout=timeout)
