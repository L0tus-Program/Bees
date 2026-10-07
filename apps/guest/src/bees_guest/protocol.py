"""Wire protocol 1: JSON fechado com framing/deadline e IDs canônicos."""

import json
import math
import struct
import time
from dataclasses import dataclass
from uuid import UUID

from bees_guest.errors import GuestError

MAX_FRAME = 16384
TIMEOUT_SECONDS = 5.0
MAX_GENERATION = 2**63 - 1
VSOCK_PORT = 2761
BINDING_KEYS = {"installation_id", "environment_id", "job_id", "vm_id", "generation"}
STATUS_KEYS = {"desktop_session", "chromium", "writer", "workspace"}
SPECIAL_VM_IDS = {
    "ffffffff-ffff-ffff-ffff-ffffffffffff",
    "90db8b89-0d35-4f79-8ce9-49ea0ac8b7cd",
    "e0e16197-dd56-4a10-9195-5ee7a155a838",
    "a42e7cda-d03f-480c-9cc2-a4de20abb878",
}


def canonical_uuid(value):
    if not isinstance(value, str):
        raise GuestError("protocol_invalid")
    try:
        parsed = UUID(value)
    except ValueError:
        raise GuestError("protocol_invalid") from None
    if str(parsed) != value or parsed.int == 0:
        raise GuestError("protocol_invalid")
    return value


def exact(value, keys):
    if type(value) is not dict or value.keys() != keys:
        raise GuestError("protocol_invalid")
    return value


@dataclass(frozen=True)
class Binding:
    installation_id: str
    environment_id: str
    job_id: str
    vm_id: str
    generation: int

    def __post_init__(self):
        for key in BINDING_KEYS - {"generation"}:
            canonical_uuid(getattr(self, key))
        if self.vm_id in SPECIAL_VM_IDS:
            raise GuestError("protocol_invalid")
        if type(self.generation) is not int or not 1 <= self.generation <= MAX_GENERATION:
            raise GuestError("protocol_invalid")

    def fields(self):
        return {key: getattr(self, key) for key in sorted(BINDING_KEYS)}


def validate_binding(value, binding):
    if value.get("protocol") != 1 or type(value.get("protocol")) is not int:
        raise GuestError("protocol_invalid")
    for key, expected in binding.fields().items():
        actual = value.get(key)
        if type(actual) is not type(expected) or actual != expected:
            raise GuestError("binding_mismatch")


def challenge(value, binding):
    exact(value, BINDING_KEYS | {"protocol", "type", "nonce"})
    validate_binding(value, binding)
    if value["type"] != "challenge":
        raise GuestError("protocol_invalid")
    canonical_uuid(value["nonce"])
    return value


def accepted(value, hello):
    exact(value, hello.keys())
    if value != hello | {"type": "accepted"}:
        raise GuestError("binding_mismatch")


def health_request(value, binding, guest_id):
    exact(
        value,
        BINDING_KEYS | {"protocol", "type", "guest_id", "request_id", "nonce", "session_nonce"},
    )
    validate_binding(value, binding)
    if value["type"] != "health_request" or value["guest_id"] != guest_id:
        raise GuestError("binding_mismatch")
    for key in ("guest_id", "request_id", "nonce", "session_nonce"):
        canonical_uuid(value[key])
    return value


def status(value):
    exact(value, STATUS_KEYS)
    if any(type(item) is not bool for item in value.values()):
        raise GuestError("protocol_invalid")
    return dict(value)


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise GuestError("protocol_invalid")
        result[key] = value
    return result


def _invalid_constant(_):
    raise GuestError("protocol_invalid")


def canonical_json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def decode(data):
    if not 1 <= len(data) <= MAX_FRAME:
        raise GuestError("frame_invalid")
    try:
        value = json.loads(
            data.decode("utf-8"), object_pairs_hook=_pairs, parse_constant=_invalid_constant
        )
    except (UnicodeError, ValueError, RecursionError):
        raise GuestError("protocol_invalid") from None
    if type(value) is not dict:
        raise GuestError("protocol_invalid")
    return value


def read_frame(stream, *, timeout=TIMEOUT_SECONDS):
    if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 < timeout <= 15:
        raise GuestError("timeout_invalid")
    deadline = time.monotonic() + timeout

    def receive(size):
        result = bytearray()
        while len(result) < size:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise GuestError("transport_timeout")
            stream.settimeout(remaining)
            try:
                part = stream.recv(size - len(result))
            except TimeoutError:
                raise GuestError("transport_timeout") from None
            except OSError:
                raise GuestError("transport_unavailable") from None
            if not part:
                raise GuestError("transport_closed")
            result.extend(part)
        return bytes(result)

    size = struct.unpack("!I", receive(4))[0]
    if not 1 <= size <= MAX_FRAME:
        raise GuestError("frame_invalid")
    return decode(receive(size))


def write_frame(stream, value, *, timeout=TIMEOUT_SECONDS):
    if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 < timeout <= 15:
        raise GuestError("timeout_invalid")
    try:
        data = canonical_json(value)
    except (ValueError, TypeError, RecursionError):
        raise GuestError("protocol_invalid") from None
    if not 1 <= len(data) <= MAX_FRAME:
        raise GuestError("frame_invalid")
    stream.settimeout(timeout)
    try:
        stream.sendall(struct.pack("!I", len(data)) + data)
    except TimeoutError:
        raise GuestError("transport_timeout") from None
    except OSError:
        raise GuestError("transport_unavailable") from None


def certificate_uri(binding, guest_id, role):
    if role not in ("host", "guest"):
        raise GuestError("protocol_invalid")
    canonical_uuid(guest_id)
    return "urn:bees:bridge:" + ":".join(
        [
            role,
            binding.installation_id,
            binding.environment_id,
            binding.job_id,
            binding.vm_id,
            str(binding.generation),
            guest_id,
        ]
    )
