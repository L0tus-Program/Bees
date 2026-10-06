"""Handshake/reports autenticados e idempotentes; não recebe tarefas ou comandos."""

from datetime import UTC, datetime
from uuid import uuid4

import httpx
from pydantic import ValidationError

from bees_host.contracts import Report, Session
from bees_host.errors import HostError
from bees_host.probe import diagnose
from bees_host.state import StateStore

ROOT = "/api/v1/host-link/runtime"


class Runtime:
    def __init__(self, store: StateStore, *, transport=None, probe=diagnose) -> None:
        self.store = store
        self.state = store.load()
        if self.state is None:
            raise HostError("bootstrap_required")
        self.probe = probe
        self.client = httpx.Client(
            base_url=self.state.origin,
            headers={"Origin": self.state.origin},
            timeout=httpx.Timeout(10.0, connect=3.0),
            follow_redirects=False,
            trust_env=False,
            transport=transport,
        )

    def close(self) -> None:
        self.client.close()

    def _replace(self, **changes) -> None:
        updated = self.state.model_copy(update=changes)
        self.store.save(updated)
        self.state = updated

    def _request(self, method: str, path: str, *, payload=None, pair: bool = False) -> Session:
        headers = (
            {}
            if pair
            else {"Authorization": "Bearer " + self.state.host_credential.get_secret_value()}
        )
        try:
            # stream evita acumular resposta ilimitada antes de validar o contrato.
            with self.client.stream(method, ROOT + path, headers=headers, json=payload) as response:
                if response.status_code == 401:
                    if self.state.registered:
                        expired = (
                            not self.state.confirmed
                            and self.state.pairing_expires_at is not None
                            and self.state.pairing_expires_at <= datetime.now(UTC)
                        )
                        self._replace(revoked=not expired, expired=expired, pending_report=None)
                        self.store.publish_ended(self.state, "expired" if expired else "revoked")
                    raise HostError("host_unauthorized")
                if response.status_code == 409:
                    raise HostError("host_conflict")
                if response.status_code == 429 or response.status_code >= 500:
                    raise HostError("connection_unavailable")
                if response.status_code != 200:
                    raise HostError("host_protocol_invalid")
                body = bytearray()
                for chunk in response.iter_bytes(chunk_size=1024):
                    body.extend(chunk)
                    if len(body) > 16384:
                        raise HostError("host_protocol_invalid")
            session = Session.model_validate_json(body)
        except httpx.HTTPError:
            raise HostError("connection_unavailable") from None
        except ValidationError, ValueError, UnicodeError:
            raise HostError("host_protocol_invalid") from None
        if (
            session.host_id != self.state.host_id
            or session.installation_id != self.state.installation_id
            or session.fingerprint != self.state.expected_fingerprint
        ):
            raise HostError("host_protocol_invalid")
        return session

    def _session(self) -> Session:
        if self.state.registered:
            return self._request("GET", "/session")
        # Em resposta perdida, a credencial gravada pode já existir no servidor.
        try:
            session = self._request("GET", "/session")
        except HostError as error:
            if error.code != "host_unauthorized":
                raise
            if (
                self.state.invite_token is None
                or self.state.invite_expires_at is None
                or self.state.invite_expires_at <= datetime.now(UTC)
            ):
                raise HostError("pairing_expired") from None
            session = self._request(
                "POST",
                "/exchange",
                pair=True,
                payload={
                    "installation_id": str(self.state.installation_id),
                    "invite_token": self.state.invite_token.get_secret_value(),
                    "host_id": str(self.state.host_id),
                    "host_credential": self.state.host_credential.get_secret_value(),
                    "client_request_id": str(self.state.pair_request_id),
                },
            )
        self._replace(registered=True, invite_token=None, invite_expires_at=None)
        return session

    def step(self) -> Session:
        if self.state.revoked:
            raise HostError("host_revoked")
        if self.state.expired:
            raise HostError("pairing_expired")
        session = self._session()
        if session.status == "revoked":
            self._replace(revoked=True, pending_report=None)
            self.store.publish(session)
            return session
        self.store.publish(session)
        if self.state.pairing_expires_at != session.pairing_expires_at or (
            session.status == "active" and not self.state.confirmed
        ):
            self._replace(
                pairing_expires_at=session.pairing_expires_at,
                confirmed=self.state.confirmed or session.status == "active",
            )
        if session.status != "active":
            if not session.confirmable:
                self._replace(expired=True)
                self.store.publish_ended(self.state, "expired")
            return session
        if self.state.pending_report is not None:
            pending = self.state.pending_report
            if (
                session.last_report_request_id == pending.client_request_id
                and session.last_report_sequence == pending.sequence
                and session.report_revision == pending.expected_revision + 1
            ):
                self._replace(pending_report=None)
                return session
            if (
                session.last_report_sequence != pending.sequence - 1
                or session.report_revision != pending.expected_revision
            ):
                raise HostError("host_conflict")
        else:
            driver, probe = self.probe()
            pending = Report(
                host_id=self.state.host_id,
                sequence=session.last_report_sequence + 1,
                expected_revision=session.report_revision,
                client_request_id=uuid4(),
                driver=driver,
                probe=probe,
            )
            self._replace(pending_report=pending)
        result = self._request("POST", "/report", payload=pending.model_dump(mode="json"))
        if (
            result.status != "active"
            or result.last_report_request_id != pending.client_request_id
            or result.last_report_sequence != pending.sequence
            or result.report_revision != pending.expected_revision + 1
        ):
            raise HostError("host_protocol_invalid")
        self._replace(pending_report=None)
        self.store.publish(result)
        return result
