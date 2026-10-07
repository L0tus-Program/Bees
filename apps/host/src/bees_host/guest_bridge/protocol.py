"""Wire v1: framing limitado e snapshots exatos; conteúdo externo não muda identidade."""

import json
import math
import socket
import struct
import time
from dataclasses import dataclass
from uuid import UUID

MAX_FRAME = 16384
STATUS_FIELDS = frozenset({"desktop_session", "chromium", "writer", "workspace"})
ERROR_CODES = frozenset({"request_unknown", "request_conflict", "session_fenced"})
SPECIAL_VM_IDS = frozenset(
    {
        "00000000-0000-0000-0000-000000000000",
        "ffffffff-ffff-ffff-ffff-ffffffffffff",
        "90db8b89-0d35-4f79-8ce9-49ea0ac8b7cd",
        "e0e16197-dd56-4a10-9195-5ee7a155a838",
        "a42e7cda-d03f-480c-9cc2-a4de20abb878",
    }
)


class BridgeError(RuntimeError):
    """Somente códigos fixos, sem dados de frames, caminhos ou exceções TLS."""


def uuid_text(value: object) -> str:
    if type(value) is not str:
        raise BridgeError("bridge_protocol_invalid")
    try:
        result = UUID(value)
    except ValueError, AttributeError:
        raise BridgeError("bridge_protocol_invalid") from None
    if str(result) != value or result.int == 0:
        raise BridgeError("bridge_protocol_invalid")
    return value


@dataclass(frozen=True)
class Binding:
    installation_id: str
    environment_id: str
    job_id: str
    vm_id: str
    generation: int

    def __post_init__(self) -> None:
        for value in (self.installation_id, self.environment_id, self.job_id, self.vm_id):
            uuid_text(value)
        if self.vm_id in SPECIAL_VM_IDS:
            raise BridgeError("bridge_binding_invalid")
        if type(self.generation) is not int or not 1 <= self.generation <= 2**63 - 1:
            raise BridgeError("bridge_binding_invalid")

    def fields(self) -> dict:
        return {
            "installation_id": self.installation_id,
            "environment_id": self.environment_id,
            "job_id": self.job_id,
            "vm_id": self.vm_id,
            "generation": self.generation,
        }


def certificate_uri(binding: Binding, guest_id: str, role: str) -> str:
    uuid_text(guest_id)
    if role not in {"host", "guest"}:
        raise BridgeError("bridge_identity_invalid")
    return (
        "urn:bees:bridge:"
        + role
        + ":"
        + ":".join(str(value) for value in (*binding.fields().values(), guest_id))
    )


def frame(binding: Binding, kind: str, **fields: object) -> dict:
    return {"protocol": 1, "type": kind, **binding.fields(), **fields}


def validate_frame(value: object, binding: Binding, kind: str, fields: set[str]) -> dict:
    expected = {"protocol", "type", *binding.fields(), *fields}
    if type(value) is not dict or set(value) != expected:
        raise BridgeError("bridge_protocol_invalid")
    if type(value["protocol"]) is not int or value["protocol"] != 1 or value["type"] != kind:
        raise BridgeError("bridge_protocol_invalid")
    for name, fixed in binding.fields().items():
        if type(value[name]) is not type(fixed) or value[name] != fixed:
            raise BridgeError("bridge_binding_mismatch")
    for name in {"guest_id", "request_id", "nonce", "session_nonce"} & fields:
        uuid_text(value[name])
    return value


def _pairs(pairs: list[tuple[str, object]]) -> dict:
    value = {}
    for name, item in pairs:
        if name in value:
            raise BridgeError("bridge_protocol_invalid")
        value[name] = item
    return value


def _reject_constant(value: str) -> None:
    raise BridgeError("bridge_protocol_invalid")


def _timeout(value: float) -> None:
    if type(value) not in {float, int} or not math.isfinite(value) or not 0 < value <= 15:
        raise BridgeError("bridge_timeout_invalid")


def _read(stream: socket.socket, size: int, deadline: float) -> bytes:
    chunks = bytearray()
    while len(chunks) < size:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise BridgeError("bridge_timeout")
        stream.settimeout(remaining)
        try:
            chunk = stream.recv(size - len(chunks))
        except TimeoutError:
            raise BridgeError("bridge_timeout") from None
        except OSError:
            raise BridgeError("bridge_transport_failed") from None
        if not chunk:
            raise BridgeError("bridge_disconnected")
        chunks.extend(chunk)
    return bytes(chunks)


def receive_frame(stream: socket.socket, timeout: float = 5.0) -> dict:
    _timeout(timeout)
    deadline = time.monotonic() + timeout
    size = struct.unpack("!I", _read(stream, 4, deadline))[0]
    if not 0 < size <= MAX_FRAME:
        raise BridgeError("bridge_frame_size_invalid")
    raw = _read(stream, size, deadline)
    try:
        value = json.loads(
            raw.decode("utf-8"), object_pairs_hook=_pairs, parse_constant=_reject_constant
        )
    except UnicodeError, ValueError, RecursionError:
        raise BridgeError("bridge_protocol_invalid") from None
    if type(value) is not dict:
        raise BridgeError("bridge_protocol_invalid")
    stack, nodes = [(value, 0)], 0
    while stack:
        item, depth = stack.pop()
        nodes += 1
        if depth > 8 or nodes > 256:
            raise BridgeError("bridge_protocol_invalid")
        if type(item) is dict:
            stack.extend((entry, depth + 1) for entry in item.values())
        elif type(item) is list:
            stack.extend((entry, depth + 1) for entry in item)
        elif type(item) is float and not math.isfinite(item):
            raise BridgeError("bridge_protocol_invalid")
    return value


def send_frame(stream: socket.socket, value: dict, timeout: float = 5.0) -> None:
    _timeout(timeout)
    try:
        raw = json.dumps(value, allow_nan=False, separators=(",", ":")).encode("utf-8")
    except TypeError, ValueError, RecursionError:
        raise BridgeError("bridge_protocol_invalid") from None
    if not 0 < len(raw) <= MAX_FRAME:
        raise BridgeError("bridge_frame_size_invalid")
    stream.settimeout(timeout)
    try:
        stream.sendall(struct.pack("!I", len(raw)) + raw)
    except TimeoutError:
        raise BridgeError("bridge_timeout") from None
    except OSError:
        raise BridgeError("bridge_transport_failed") from None
