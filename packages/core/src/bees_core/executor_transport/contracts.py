"""ExecutorTransport v1 fechado e independente de SQLite/APSW/modelos."""

import hashlib
import json
import math
import re
import struct
import time
from dataclasses import dataclass
from uuid import UUID

MAX_FRAME = 32768
MAX_SECONDS = 5.0
MAX_GENERATION = 2**63 - 1
ENVELOPE_KEYS = {
    "protocol",
    "installation_id",
    "environment_id",
    "resource_id",
    "adapter",
    "action_id",
    "request_id",
    "agent_id",
    "run_id",
    "owner_id",
    "generation",
    "control_revision",
    "safety_generation",
    "binding_hash",
    "input_hash",
    "tool_name",
    "tool_version",
    "deadline_ms",
    "identity",
}
CREDENTIAL = re.compile(r"bx_[A-Za-z0-9_-]{43}\Z")
DIGEST = re.compile(r"[0-9a-f]{64}\Z")


class TransportError(RuntimeError):
    """Somente códigos fechados; nunca copiar exceções/entradas privadas."""

    def __init__(self, code):
        self.code = code
        super().__init__(code)


def exact(value, keys):
    if type(value) is not dict or value.keys() != keys:
        raise TransportError("executor_protocol_invalid")
    return value


def canonical_uuid(value):
    if type(value) is not str:
        raise TransportError("executor_protocol_invalid")
    try:
        parsed = UUID(value)
    except ValueError:
        raise TransportError("executor_protocol_invalid") from None
    if parsed.int == 0 or str(parsed) != value:
        raise TransportError("executor_protocol_invalid")
    return value


def canonical(value):
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except TypeError, ValueError, RecursionError, UnicodeError:
        raise TransportError("executor_protocol_invalid") from None


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise TransportError("executor_protocol_invalid")
        result[key] = value
    return result


def _constant(_):
    raise TransportError("executor_protocol_invalid")


def decode(data):
    if not 1 <= len(data) <= MAX_FRAME:
        raise TransportError("executor_frame_invalid")
    try:
        value = json.loads(data.decode("utf-8"), object_pairs_hook=_pairs, parse_constant=_constant)
    except ValueError, UnicodeError, RecursionError:
        raise TransportError("executor_protocol_invalid") from None
    if type(value) is not dict:
        raise TransportError("executor_protocol_invalid")
    return value


def remaining(deadline):
    value = deadline - time.monotonic()
    if not math.isfinite(value) or value <= 0:
        raise TransportError("executor_timeout")
    return min(value, MAX_SECONDS)


def read_frame(stream, deadline):
    def receive(size):
        result = bytearray()
        while len(result) < size:
            stream.settimeout(remaining(deadline))
            try:
                piece = stream.recv(size - len(result))
            except TimeoutError:
                raise TransportError("executor_timeout") from None
            except OSError:
                raise TransportError("executor_unavailable") from None
            if not piece:
                raise TransportError("executor_closed")
            result.extend(piece)
        return result

    size = struct.unpack("!I", receive(4))[0]
    if not 1 <= size <= MAX_FRAME:
        raise TransportError("executor_frame_invalid")
    return decode(receive(size))


def write_frame(stream, value, deadline):
    data = canonical(value)
    if not 1 <= len(data) <= MAX_FRAME:
        raise TransportError("executor_frame_invalid")
    stream.settimeout(remaining(deadline))
    try:
        stream.sendall(struct.pack("!I", len(data)) + data)
    except TimeoutError:
        raise TransportError("executor_timeout") from None
    except OSError:
        raise TransportError("executor_unavailable") from None


@dataclass(frozen=True)
class ResourceIdentity:
    installation_id: str
    resource_id: str
    agent_id: str

    def __post_init__(self):
        canonical_uuid(self.installation_id)
        canonical_uuid(self.resource_id)
        canonical_uuid(self.agent_id)

    def fields(self):
        return {
            "installation_id": self.installation_id,
            "environment_id": self.resource_id,
            "resource_id": self.resource_id,
            "adapter": "local_process_text",
            "agent_id": self.agent_id,
            "identity": "bees_user",
        }


def envelope(value, identity):
    exact(value, ENVELOPE_KEYS)
    for key in (
        "installation_id",
        "environment_id",
        "resource_id",
        "action_id",
        "request_id",
        "agent_id",
        "run_id",
        "owner_id",
    ):
        canonical_uuid(value[key])
    if any(
        type(value[key]) is not type(expected) or value[key] != expected
        for key, expected in identity.fields().items()
    ):
        raise TransportError("executor_binding_mismatch")
    if type(value["protocol"]) is not int or value["protocol"] != 1:
        raise TransportError("executor_protocol_invalid")
    if value["tool_name"] != "text.normalize" or value["tool_version"] != "1":
        raise TransportError("executor_tool_unavailable")
    for key, minimum in (
        ("generation", 1),
        ("control_revision", 0),
        ("safety_generation", 0),
        ("deadline_ms", 1),
    ):
        if type(value[key]) is not int or not minimum <= value[key] <= MAX_GENERATION:
            raise TransportError("executor_protocol_invalid")
    for key in ("binding_hash", "input_hash"):
        if type(value[key]) is not str or DIGEST.fullmatch(value[key]) is None:
            raise TransportError("executor_protocol_invalid")
    return value


def _bounded_text(value, characters, utf8_bytes):
    if type(value) is not str or len(value) > characters or "\x00" in value:
        return False
    try:
        return len(value.encode("utf-8")) <= utf8_bytes
    except UnicodeError:
        return False


def arguments(value, expected_hash):
    exact(value, {"text", "operation"})
    if not _bounded_text(value["text"], 4096, 8192) or value["operation"] not in (
        "trim",
        "upper",
        "lower",
    ):
        raise TransportError("executor_input_invalid")
    if digest(value) != expected_hash:
        raise TransportError("executor_input_changed")
    return value


def result(value):
    exact(value, {"text"})
    if not _bounded_text(value["text"], 8192, 16384):
        raise TransportError("executor_result_invalid")
    return value


def current_deadline(value):
    delta = value["deadline_ms"] / 1000 - time.time()
    if not 0 < delta <= MAX_SECONDS:
        raise TransportError("executor_deadline_invalid")


ERROR_CODES = frozenset(
    {
        "executor_protocol_invalid",
        "executor_frame_invalid",
        "executor_timeout",
        "executor_unavailable",
        "executor_closed",
        "executor_binding_mismatch",
        "executor_tool_unavailable",
        "executor_input_invalid",
        "executor_input_changed",
        "executor_result_invalid",
        "executor_deadline_invalid",
        "executor_credentials_invalid",
        "executor_permit_invalid",
        "executor_internal_error",
        "executor_journal_missing",
        "executor_journal_invalid",
        "executor_journal_unavailable",
        "executor_resource_busy",
        "executor_private_storage_required",
        "executor_platform_unavailable",
        "executor_fenced",
        "executor_request_conflict",
        "executor_request_replay",
        "executor_journal_limit",
    }
)
