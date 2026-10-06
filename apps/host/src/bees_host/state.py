"""Estado cifrado durável, bootstrap de uso único e trava de uma instância."""

import json
import os
import secrets
import time
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from pydantic import ValidationError

from bees_host.contracts import Bootstrap, HostState, Session, pairing_fingerprint
from bees_host.errors import HostError
from bees_host.security import Cipher, check_no_links, check_private


class StateStore:
    MAX_BYTES = 65536

    def __init__(self, directory: Path, cipher: Cipher) -> None:
        self.directory = Path(os.path.abspath(directory))
        self.cipher = cipher
        check_no_links(self.directory)
        # Só diretório novo recebe ACL; existente inseguro é rejeitado.
        if not self.directory.exists():
            self.directory.mkdir(mode=0o700, parents=True)
            check_private(self.directory, directory=True, protect=True)
        check_private(self.directory, directory=True)
        self.path = self.directory / "credentials.bin"

    @contextmanager
    def lock(self):
        path = self.directory / "host.lock"
        descriptor = self._create_or_open(path)
        with os.fdopen(descriptor, "r+b") as handle:
            deadline = time.monotonic() + 2
            while True:
                try:
                    if os.name == "nt":
                        import msvcrt

                        if handle.seek(0, os.SEEK_END) == 0:
                            handle.write(b"0")
                            handle.flush()
                        handle.seek(0)
                        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl

                        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise HostError("host_already_running") from None
                    time.sleep(0.1)
            try:
                yield
            finally:
                if os.name == "nt":
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def _create_or_open(self, path: Path) -> int:
        check_no_links(path)
        try:
            descriptor = os.open(path, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600)
            try:
                check_private(path, protect=True)
            except BaseException:
                os.close(descriptor)
                raise
            return descriptor
        except FileExistsError:
            check_private(path)
            return os.open(path, os.O_RDWR | getattr(os, "O_NOFOLLOW", 0))

    def _atomic_write(self, path: Path, data: bytes) -> None:
        check_private(self.directory, directory=True)
        check_no_links(path)
        if path.exists():
            check_private(path)
        temporary = self.directory / (".host-" + secrets.token_hex(16))
        descriptor = self._create_or_open(temporary)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
            check_private(path)
            if os.name != "nt":
                directory_fd = os.open(self.directory, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
        finally:
            temporary.unlink(missing_ok=True)

    def save(self, state: HostState) -> None:
        value = state.model_dump(mode="json")
        value["host_credential"] = state.host_credential.get_secret_value()
        value["invite_token"] = (
            state.invite_token.get_secret_value() if state.invite_token else None
        )
        plaintext = json.dumps(value, separators=(",", ":")).encode()
        self._atomic_write(self.path, self.cipher.encrypt(plaintext))

    def load(self) -> HostState | None:
        if not self.path.exists():
            return None
        check_private(self.path)
        try:
            if self.path.stat().st_size > self.MAX_BYTES:
                raise HostError("host_state_invalid")
            return HostState.model_validate_json(self.cipher.decrypt(self.path.read_bytes()))
        except ValidationError, ValueError:
            raise HostError("host_state_invalid") from None

    def initialize(self, bootstrap_file: Path | None, *, new_pair: bool = False) -> HostState:
        current = self.load()
        if bootstrap_file is None:
            if new_pair:
                raise HostError("bootstrap_required")
            if current is None:
                raise HostError("bootstrap_required")
            return current
        bootstrap_path = Path(os.path.abspath(bootstrap_file))
        check_private(bootstrap_path.parent, directory=True)
        check_private(bootstrap_path)
        try:
            if bootstrap_path.stat().st_size > 8192:
                raise HostError("bootstrap_invalid")
            bootstrap = Bootstrap.model_validate_json(bootstrap_path.read_bytes())
            bootstrap.require_unexpired()
        except ValidationError, ValueError:
            raise HostError("bootstrap_invalid") from None
        if current is not None:
            # Nunca troca instalação, credencial ou vínculo silenciosamente.
            if (
                current.origin != bootstrap.origin
                or current.installation_id != bootstrap.installation_id
            ):
                raise HostError("installation_conflict")
            if current.revoked and not new_pair:
                raise HostError("host_revoked")
            if current.expired and not new_pair:
                raise HostError("pairing_expired")
            if new_pair:
                current = None
            elif not current.registered:
                current = current.model_copy(
                    update={
                        "invite_token": bootstrap.invite_token,
                        "invite_expires_at": bootstrap.expires_at,
                        "pair_request_id": uuid4(),
                        "expected_fingerprint": pairing_fingerprint(
                            current.installation_id,
                            bootstrap.invite_id,
                            current.host_id,
                            current.host_credential.get_secret_value(),
                        ),
                    }
                )
        if current is None:
            host_id = uuid4()
            credential = "bh_" + secrets.token_urlsafe(32)
            current = HostState(
                origin=bootstrap.origin,
                installation_id=bootstrap.installation_id,
                host_id=host_id,
                host_credential=credential,
                expected_fingerprint=pairing_fingerprint(
                    bootstrap.installation_id, bootstrap.invite_id, host_id, credential
                ),
                pair_request_id=uuid4(),
                invite_token=bootstrap.invite_token,
                invite_expires_at=bootstrap.expires_at,
            )
        # Cifra ticket/identidade antes de remover bootstrap e antes da primeira requisição.
        self.save(current)
        bootstrap_path.unlink()
        return current

    def publish(self, session: Session) -> None:
        public = {
            "installation_id": str(session.installation_id),
            "host_id": str(session.host_id),
            "fingerprint": session.fingerprint,
            "status": session.status,
        }
        self._atomic_write(
            self.directory / "public-status.json", json.dumps(public).encode("utf-8")
        )

    def publish_ended(self, state: HostState, status: str) -> None:
        path = self.directory / "public-status.json"
        public = {"installation_id": str(state.installation_id), "host_id": str(state.host_id)}
        if path.exists():
            check_private(path)
            try:
                previous = json.loads(path.read_bytes())
                if previous.get("host_id") == str(state.host_id):
                    public["fingerprint"] = previous["fingerprint"]
            except ValueError, KeyError, AttributeError:
                pass
        public["status"] = status
        self._atomic_write(path, json.dumps(public).encode("utf-8"))
