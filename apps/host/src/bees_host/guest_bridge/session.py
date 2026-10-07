"""Handshake e health apenas. Nenhuma resposta concede prontidão, claims ou ferramentas."""

import ssl
import threading
import time
from dataclasses import dataclass
from uuid import uuid4

from bees_host.guest_bridge.protocol import (
    ERROR_CODES,
    STATUS_FIELDS,
    Binding,
    BridgeError,
    frame,
    receive_frame,
    send_frame,
    validate_frame,
)
from bees_host.guest_bridge.tls import HostIdentity


class SessionFence:
    """Fence em RAM exige dono único e reuso entre conexões do mesmo vínculo.

    Não oferece fencing entre processos/restarts nem lease de Task ou autorização.
    Supervisor futuro precisa de lock privado + exclusividade do listener real.
    """

    def __init__(self, binding: Binding, guest_id: str) -> None:
        self.binding, self.guest_id = binding, guest_id
        self._nonce: str | None = None
        self._stream: ssl.SSLSocket | None = None
        self._revoked = False
        self._last_sequence = 0
        self._lock = threading.Lock()

    def activate(self, nonce: str, stream: ssl.SSLSocket) -> None:
        with self._lock:
            if self._revoked:
                raise BridgeError("bridge_session_revoked")
            old = self._stream
            self._nonce, self._stream = nonce, stream
        if old is not None and old is not stream:
            old.close()

    def check(self, nonce: str) -> None:
        with self._lock:
            if self._revoked or self._nonce != nonce:
                raise BridgeError("bridge_session_fenced")

    def observe(self, nonce: str, sequence: int, cached: bool) -> float | None:
        with self._lock:
            if self._revoked or self._nonce != nonce:
                raise BridgeError("bridge_session_fenced")
            if cached:
                return None
            if sequence <= self._last_sequence:
                raise BridgeError("bridge_sequence_invalid")
            self._last_sequence = sequence
            return time.monotonic()

    def revoke(self) -> None:
        with self._lock:
            self._revoked, self._nonce = True, None
            stream, self._stream = self._stream, None
        if stream is not None:
            stream.close()


@dataclass(frozen=True)
class HealthObservation:
    sequence: int
    cached: bool
    desktop_session: bool
    chromium: bool
    writer: bool
    workspace: bool
    observed_monotonic: float | None


class HostSession:
    def __init__(self, stream, identity, fence, nonce, timeout) -> None:
        self._stream, self._identity, self._fence = stream, identity, fence
        self._nonce, self._timeout = nonce, timeout
        self._lock = threading.Lock()

    @classmethod
    def accept(cls, listener, identity, fence, timeout: float = 5.0) -> HostSession:
        """Entrada nativa: identidade de VM obtida pelo kernel antes de TLS/frames."""
        from bees_host.guest_bridge.transport import accept_tls

        stream = accept_tls(listener, identity, timeout)
        return cls.handshake(stream, identity, fence, timeout)

    @classmethod
    def handshake(
        cls,
        stream: ssl.SSLSocket,
        identity: HostIdentity,
        fence: SessionFence,
        timeout: float = 5.0,
    ) -> HostSession:
        """Socket já mTLS; produção aceita apenas accept_tls(AF_HYPERV)."""
        try:
            if not isinstance(stream, ssl.SSLSocket) or not stream.server_side:
                raise BridgeError("bridge_tls_required")
            if stream.context.verify_mode != ssl.CERT_REQUIRED:
                raise BridgeError("bridge_tls_required")
            identity.check_peer(stream)
            if fence.binding != identity.binding or fence.guest_id != identity.guest_id:
                raise BridgeError("bridge_binding_mismatch")
            nonce = str(uuid4())
            send_frame(stream, frame(identity.binding, "challenge", nonce=nonce), timeout)
            hello = validate_frame(
                receive_frame(stream, timeout),
                identity.binding,
                "hello",
                {"guest_id", "request_id", "nonce"},
            )
            if hello["guest_id"] != identity.guest_id or hello["nonce"] != nonce:
                raise BridgeError("bridge_handshake_mismatch")
            fence.activate(nonce, stream)
            send_frame(
                stream,
                frame(
                    identity.binding,
                    "accepted",
                    guest_id=identity.guest_id,
                    request_id=hello["request_id"],
                    nonce=nonce,
                ),
                timeout,
            )
            return cls(stream, identity, fence, nonce, timeout)
        except BaseException:
            stream.close()
            raise

    def health(self) -> HealthObservation:
        """Sem retry automático: erro/desconexão invalida esta conexão."""
        with self._lock:
            try:
                self._fence.check(self._nonce)
                self._identity.check_peer(self._stream)
                request = frame(
                    self._identity.binding,
                    "health_request",
                    guest_id=self._identity.guest_id,
                    request_id=str(uuid4()),
                    nonce=str(uuid4()),
                    session_nonce=self._nonce,
                )
                send_frame(self._stream, request, self._timeout)
                value = receive_frame(self._stream, self._timeout)
                shared = {"guest_id", "request_id", "nonce", "session_nonce"}
                if value.get("type") == "error":
                    validate_frame(value, self._identity.binding, "error", shared | {"code"})
                    self._correlate(value, request)
                    if type(value["code"]) is not str or value["code"] not in ERROR_CODES:
                        raise BridgeError("bridge_protocol_invalid")
                    raise BridgeError("bridge_" + value["code"])
                validate_frame(
                    value,
                    self._identity.binding,
                    "health",
                    shared | {"sequence", "cached", "status"},
                )
                self._correlate(value, request)
                if type(value["sequence"]) is not int or not 1 <= value["sequence"] <= 2**63 - 1:
                    raise BridgeError("bridge_sequence_invalid")
                if type(value["cached"]) is not bool:
                    raise BridgeError("bridge_protocol_invalid")
                status = value["status"]
                if (
                    type(status) is not dict
                    or set(status) != STATUS_FIELDS
                    or any(type(item) is not bool for item in status.values())
                ):
                    raise BridgeError("bridge_protocol_invalid")
                self._identity.check_peer(self._stream)
                observed = self._fence.observe(self._nonce, value["sequence"], value["cached"])
                return HealthObservation(
                    value["sequence"], value["cached"], **status, observed_monotonic=observed
                )
            except BaseException:
                self._stream.close()
                raise

    def _correlate(self, value: dict, request: dict) -> None:
        for name in ("guest_id", "request_id", "nonce", "session_nonce"):
            if value[name] != request[name]:
                raise BridgeError("bridge_response_mismatch")

    def close(self) -> None:
        self._stream.close()
