"""Autoridade HTTP fechada, sem emissão, armazenamento, supervisor ou efeitos Windows.

Somente composição confiável fornece origem, credencial bp_ e identidade esperada.
Não repete requests, gera UUIDs, segue redirects ou transforma replay em autorização.
"""

import asyncio
import ipaddress
import json
import re
import socket
import threading
import time
from datetime import UTC, datetime
from typing import Literal
from urllib.parse import urlsplit
from uuid import UUID

import httpx
from pydantic import Field, SecretStr, ValidationError

from bees_host.provisioning.contracts import (
    OPERATIONS,
    Claim,
    Closed,
    DispatchPermit,
    Operation,
    ProvisionError,
    ReceiptResult,
    canonical,
)

ROOT = "/api/v1/provisioner/runtime"
MAX_BYTES = 16384
REQUEST_BUDGET = 5.0
_TRANSITIONS = {
    "claimed": {"claimed", "dispatch_started", "aborted", "outcome_unknown"},
    "dispatch_started": {"dispatch_started", "confirmed", "outcome_unknown"},
    "confirmed": {"confirmed"},
    "aborted": {"aborted"},
    "outcome_unknown": {"outcome_unknown"},
}


class Session(Closed):
    installation_id: UUID
    host_id: UUID
    provisioner_id: UUID
    revision: int = Field(ge=1)
    status: Literal["active"]


def _uuid(value: UUID) -> str:
    if type(value) is not UUID or not value.int:
        raise ProvisionError("provision_protocol_invalid")
    return str(value)


def _origin(value: str) -> tuple[str, str, str]:
    """localhost resolve uma vez e disca IP fixado; Host/Origin/SNI ficam originais."""
    try:
        if type(value) is not str or any(char.isspace() or ord(char) < 32 for char in value):
            raise ValueError
        parsed = urlsplit(value)
        host = parsed.hostname
        authority = "[::1]" if host == "::1" else host
        if parsed.port is not None:
            authority += ":" + str(parsed.port)
        origin = parsed.scheme + "://" + authority
        if (
            parsed.scheme not in {"http", "https"}
            or host not in {"localhost", "127.0.0.1", "::1"}
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
            or parsed.netloc != authority
            or value not in {origin, origin + "/"}
            or parsed.port is not None
            and not 1 <= parsed.port <= 65535
        ):
            raise ValueError
        dial = host
        if host == "localhost":
            answers = socket.getaddrinfo(
                host,
                parsed.port or (443 if parsed.scheme == "https" else 80),
                type=socket.SOCK_STREAM,
            )
            addresses = {answer[4][0] for answer in answers}
            if not addresses or any(
                not ipaddress.ip_address(address).is_loopback for address in addresses
            ):
                raise ValueError
            # Fixar o IP também impede nova resolução durante o request.
            dial = "127.0.0.1" if "127.0.0.1" in addresses else "::1"
            if dial not in addresses:
                raise ValueError
        dial_authority = "[::1]" if dial == "::1" else dial
        if parsed.port is not None:
            dial_authority += ":" + str(parsed.port)
        return origin, parsed.scheme + "://" + dial_authority, authority
    except ValueError, TypeError, OSError:
        raise ProvisionError("provision_origin_invalid") from None


def _pairs(values):
    result = {}
    for key, value in values:
        if key in result:
            raise ValueError
        result[key] = value
    return result


def _constant(value):
    raise ValueError


class HTTPAuthority:
    def __init__(
        self,
        origin: str,
        credential: SecretStr,
        *,
        installation_id: UUID,
        host_id: UUID,
        provisioner_id: UUID,
        transport=None,
    ):
        self.origin, dial_origin, authority = _origin(origin)
        if (
            type(credential) is not SecretStr
            or re.fullmatch(r"bp_[A-Za-z0-9_-]{43}", credential.get_secret_value()) is None
        ):
            raise ProvisionError("provision_credentials_invalid")
        for value in (installation_id, host_id, provisioner_id):
            _uuid(value)
        self._identity = (installation_id, host_id, provisioner_id)
        self._credential = credential
        self._revision = 0
        self._generation = 0
        self._claims: dict[UUID, Claim] = {}
        self._effects: dict[UUID, tuple[UUID, Operation]] = {}
        self._lock = threading.RLock()
        self._dial_origin = dial_origin
        self._authority = authority
        self._transport = transport
        self._closed = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def close(self):
        with self._lock:
            self._closed = True

    async def _exchange(self, path: str, payload):
        headers = {
            "Authorization": "Bearer " + self._credential.get_secret_value(),
            "Origin": self.origin,
            "Host": self._authority,
            "Accept": "application/json",
            "Accept-Encoding": "identity",
        }
        if payload is not None:
            headers["Content-Type"] = "application/json"
        extensions = {"sni_hostname": urlsplit(self.origin).hostname}
        async with (
            asyncio.timeout(REQUEST_BUDGET),
            httpx.AsyncClient(
                base_url=self._dial_origin,
                timeout=httpx.Timeout(2.0, connect=2.0),
                follow_redirects=False,
                trust_env=False,
                transport=self._transport,
            ) as client,
        ):
            async with client.stream(
                "GET" if path == "/session" else "POST",
                ROOT + path,
                headers=headers,
                content=canonical(payload) if payload is not None else None,
                extensions=extensions,
            ) as response:
                if response.status_code == 401:
                    raise ProvisionError("provision_credentials_invalid")
                if response.status_code in {403, 409}:
                    raise ProvisionError("provision_authority_rejected")
                if response.status_code == 429 or response.status_code >= 500:
                    raise ProvisionError("provision_connection_unavailable")
                if response.status_code != 200:
                    raise ProvisionError("provision_protocol_invalid")
                content_types = response.headers.get_list("content-type")
                encodings = response.headers.get_list("content-encoding")
                lengths = response.headers.get_list("content-length")
                if (
                    len(content_types) != 1
                    or content_types[0].lower().replace(" ", "")
                    not in {"application/json", "application/json;charset=utf-8"}
                    or encodings
                    and encodings != ["identity"]
                    or len(lengths) > 1
                    or lengths
                    and (not re.fullmatch(r"[0-9]{1,5}", lengths[0]) or int(lengths[0]) > MAX_BYTES)
                ):
                    raise ProvisionError("provision_protocol_invalid")
                body = bytearray()
                async for chunk in response.aiter_raw():
                    body.extend(chunk)
                    if len(body) > MAX_BYTES:
                        raise ProvisionError("provision_protocol_invalid")
                if lengths and len(body) != int(lengths[0]):
                    raise ProvisionError("provision_protocol_invalid")
                return body

    def _request(self, path: str, model, payload=None):
        if self._closed:
            raise ProvisionError("provision_authority_closed")
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass
        else:
            raise ProvisionError("provision_async_context_unsupported")
        started = time.monotonic()
        try:
            body = asyncio.run(self._exchange(path, payload))
            data = json.loads(
                body.decode("utf-8"), object_pairs_hook=_pairs, parse_constant=_constant
            )
            result = model.model_validate_json(
                json.dumps(data, ensure_ascii=False, allow_nan=False)
            )
            if time.monotonic() - started >= REQUEST_BUDGET:
                raise ProvisionError("provision_connection_unavailable")
            return result
        except httpx.HTTPError, OSError, TimeoutError:
            raise ProvisionError("provision_connection_unavailable") from None
        except ValueError, TypeError, RecursionError, ValidationError:
            raise ProvisionError("provision_protocol_invalid") from None

    def _identity_matches(self, value):
        if (value.installation_id, value.host_id, value.provisioner_id) != self._identity:
            raise ProvisionError("provision_protocol_invalid")

    def _claim(self, current: Claim, expected: Claim | None = None, *, active=True):
        self._identity_matches(current)
        previous = self._claims.get(current.claim_id)
        for old in (expected, previous):
            if old is not None and (
                any(
                    getattr(old, name) != getattr(current, name)
                    for name in (
                        "claim_id",
                        "installation_id",
                        "host_id",
                        "provisioner_id",
                        "plan_id",
                        "plan_hash",
                        "owner_id",
                        "generation",
                        "plan",
                    )
                )
                or current.revision < old.revision
                or current.lease_expires_at < old.lease_expires_at
                or current.status not in _TRANSITIONS[old.status]
                or current.revision == old.revision
                and current != old
            ):
                raise ProvisionError("provision_claim_stale")
        if current.generation < self._generation:
            raise ProvisionError("provision_claim_stale")
        if active and (
            current.status not in {"claimed", "dispatch_started"}
            or current.lease_expires_at <= datetime.now(UTC)
        ):
            raise ProvisionError("provision_claim_stale")
        self._generation = current.generation
        self._claims[current.claim_id] = current
        return current

    def _binding(self, claim: Claim):
        try:
            claim = Claim.model_validate_json(claim.model_dump_json())
        except ValidationError, ValueError, TypeError, AttributeError:
            raise ProvisionError("provision_protocol_invalid") from None
        self._identity_matches(claim)
        return {
            "claim_id": _uuid(claim.claim_id),
            "owner_id": _uuid(claim.owner_id),
            "generation": claim.generation,
        }

    def session(self) -> Session:
        with self._lock:
            result = self._request("/session", Session)
            self._identity_matches(result)
            if result.revision < self._revision:
                raise ProvisionError("provision_protocol_invalid")
            self._revision = result.revision
            return result

    def claim(self, plan_id: UUID, plan_hash: str, owner_id: UUID, request_id: UUID) -> Claim:
        with self._lock:
            if type(plan_hash) is not str or re.fullmatch(r"[0-9a-f]{64}", plan_hash) is None:
                raise ProvisionError("provision_protocol_invalid")
            result = self._request(
                "/claim",
                Claim,
                {
                    "plan_id": _uuid(plan_id),
                    "plan_hash": plan_hash,
                    "owner_id": _uuid(owner_id),
                    "client_request_id": _uuid(request_id),
                },
            )
            if (result.plan_id, result.plan_hash, result.owner_id) != (
                plan_id,
                plan_hash,
                owner_id,
            ):
                raise ProvisionError("provision_protocol_invalid")
            return self._claim(result)

    def assert_current(self, claim: Claim) -> Claim:
        with self._lock:
            return self._claim(self._request("/current", Claim, self._binding(claim)), claim)

    def renew(self, claim: Claim, request_id: UUID) -> Claim:
        with self._lock:
            return self._claim(
                self._request(
                    "/renew", Claim, self._binding(claim) | {"client_request_id": _uuid(request_id)}
                ),
                claim,
            )

    def begin_dispatch(
        self, claim: Claim, operation: Operation, client_request_id: UUID
    ) -> DispatchPermit:
        with self._lock:
            if type(operation) is not str or operation not in OPERATIONS:
                raise ProvisionError("provision_protocol_invalid")
            result = self._request(
                "/begin",
                DispatchPermit,
                self._binding(claim)
                | {"operation": operation, "client_request_id": _uuid(client_request_id)},
            )
            self._claim(result.claim, claim, active=not result.cached)
            if (
                result.operation != operation
                or result.effect_request_id != client_request_id
                or result.dispatch_allowed
                != (not result.cached and result.status == "dispatch_started")
            ):
                raise ProvisionError("provision_protocol_invalid")
            binding = (claim.claim_id, operation)
            if (
                result.effect_request_id in self._effects
                and self._effects[result.effect_request_id] != binding
            ):
                raise ProvisionError("provision_protocol_invalid")
            self._effects[result.effect_request_id] = binding
            return result

    def record_receipt(
        self, claim: Claim, effect_request_id: UUID, client_request_id: UUID, result: ReceiptResult
    ) -> DispatchPermit:
        with self._lock:
            try:
                validated = ReceiptResult.model_validate_json(result.model_dump_json())
            except ValidationError, ValueError, TypeError, AttributeError:
                raise ProvisionError("provision_protocol_invalid") from None
            if not validated.verified:
                raise ProvisionError("provision_protocol_invalid")
            receipt = self._request(
                "/receipt",
                DispatchPermit,
                self._binding(claim)
                | {
                    "effect_request_id": _uuid(effect_request_id),
                    "client_request_id": _uuid(client_request_id),
                    "result": validated.model_dump(mode="json", exclude_none=True)
                    | {"vm_id": str(validated.vm_id) if validated.vm_id else None},
                },
            )
            self._claim(receipt.claim, claim, active=False)
            expected = self._effects.get(effect_request_id)
            if (
                receipt.effect_request_id != effect_request_id
                or receipt.dispatch_allowed
                or receipt.status != "confirmed"
                or expected is not None
                and (claim.claim_id, receipt.operation) != expected
            ):
                raise ProvisionError("provision_protocol_invalid")
            return receipt

    def mark_unknown(self, claim: Claim, effect_id: UUID, request_id: UUID) -> Claim:
        with self._lock:
            result = self._request(
                "/unknown",
                Claim,
                self._binding(claim)
                | {"effect_request_id": _uuid(effect_id), "client_request_id": _uuid(request_id)},
            )
            self._claim(result, claim, active=False)
            if result.status != "outcome_unknown":
                raise ProvisionError("provision_protocol_invalid")
            return result
