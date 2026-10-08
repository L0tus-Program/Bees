"""Inscrição privada offline, explícita e sem reparo de estado parcial.

Somente composição confiável escolhe caminhos, vínculos e cifra. Não emite segredo,
consulta o serviço, reserva plano ou ativa o provisionador. A credencial bp_ nunca
compartilha o estado bh_ do helper diagnóstico.
"""

import json
import os
import re
import threading
import time
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from pydantic import AwareDatetime, Field, SecretStr, ValidationError, model_validator

from bees_host.errors import HostError
from bees_host.provisioning.contracts import Closed, ProvisionError, canonical
from bees_host.provisioning.journal import create, private, sync_directory
from bees_host.security import Cipher, DPAPICipher, FernetCipher, check_private, native_cipher

MAX_JSON = 8192
MAX_BLOB = 65536
FINAL_FILES = frozenset({"identity.json", "owner.lock", "credentials.bin"})
ID_FIELDS = ("installation_id", "host_id", "provisioner_id", "issue_request_id")


def _origin(value: str) -> None:
    """Validação sintática somente: nenhuma resolução DNS ou importação de transporte."""
    try:
        if any(char.isspace() or ord(char) < 32 for char in value):
            raise ValueError
        parsed = urlsplit(value)
        host = parsed.hostname
        authority = "[::1]" if host == "::1" else host
        if parsed.port is not None:
            authority += ":" + str(parsed.port)
        if (
            parsed.scheme not in {"http", "https"}
            or host not in {"localhost", "127.0.0.1", "::1"}
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path
            or parsed.query
            or parsed.fragment
            or value != parsed.scheme + "://" + authority
            or parsed.port is not None
            and not 1 <= parsed.port <= 65535
        ):
            raise ValueError
    except TypeError, ValueError:
        raise ValueError("enrollment_invalid") from None


class EnrollmentBinding(Closed):
    origin: str = Field(max_length=128)
    installation_id: UUID
    host_id: UUID
    provisioner_id: UUID
    issue_request_id: UUID

    @model_validator(mode="after")
    def coherent(self):
        _origin(self.origin)
        if any(not getattr(self, name).int for name in ID_FIELDS):
            raise ValueError("enrollment_invalid")
        return self


class EnrollmentBootstrap(EnrollmentBinding):
    format: Literal[1]
    provisioner_credential: SecretStr = Field(repr=False)
    expires_at: AwareDatetime

    @model_validator(mode="after")
    def credential_valid(self):
        if type(self.format) is not int or not re.fullmatch(
            r"bp_[A-Za-z0-9_-]{43}", self.provisioner_credential.get_secret_value()
        ):
            raise ValueError("enrollment_invalid")
        return self


class _Identity(EnrollmentBinding):
    format: Literal[1]
    store_id: UUID

    @model_validator(mode="after")
    def identity_valid(self):
        if type(self.format) is not int or not self.store_id.int:
            raise ValueError("enrollment_invalid")
        return self


class EnrollmentState(_Identity):
    provisioner_credential: SecretStr = Field(repr=False)

    @model_validator(mode="after")
    def credential_valid(self):
        if not re.fullmatch(
            r"bp_[A-Za-z0-9_-]{43}", self.provisioner_credential.get_secret_value()
        ):
            raise ValueError("enrollment_invalid")
        return self


class EnrollmentCheck(Closed):
    store_id: UUID
    installation_id: UUID
    host_id: UUID
    provisioner_id: UUID
    issue_request_id: UUID
    configured_local: Literal[True] = True


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError
        result[key] = value
    return result


def _nonfinite(value):
    raise ValueError


def _decode(data: bytes, model):
    if type(data) is not bytes or not 0 < len(data) <= MAX_JSON:
        raise ValueError
    value = json.loads(data.decode("utf-8"), object_pairs_hook=_unique, parse_constant=_nonfinite)
    if not isinstance(value, dict):
        raise ValueError
    if "format" in value and type(value["format"]) is not int:
        raise ValueError
    return model.model_validate_json(canonical(value))


def _binding(value):
    if not isinstance(value, EnrollmentBinding) or type(value) is not EnrollmentBinding:
        raise ProvisionError("provision_enrollment_binding_invalid")
    try:
        return _decode(value.model_dump_json().encode(), EnrollmentBinding)
    except ValueError, ValidationError:
        raise ProvisionError("provision_enrollment_binding_invalid") from None


def _same_binding(value, binding):
    if value.origin != binding.origin or any(
        getattr(value, name) != getattr(binding, name) for name in ID_FIELDS
    ):
        raise ProvisionError("provision_enrollment_binding_invalid")


def _cipher(value):
    result = value if value is not None else native_cipher()
    # Injeção só de proteção nativa conhecida; nunca protocolo que devolva plaintext.
    if type(result) is not (DPAPICipher if os.name == "nt" else FernetCipher):
        raise ProvisionError("provision_enrollment_protection_required")
    return result


def _read(path: Path, limit: int) -> bytes:
    private(path)
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(descriptor, "rb") as source:
        initial = os.fstat(source.fileno())
        if initial.st_nlink != 1 or not 0 < initial.st_size <= limit:
            raise ProvisionError("provision_enrollment_invalid")
        data = source.read(limit + 1)
        final = os.fstat(source.fileno())
        current = path.stat()
        if (
            len(data) != initial.st_size
            or final.st_size != initial.st_size
            or final.st_mtime_ns != initial.st_mtime_ns
            or (initial.st_dev, initial.st_ino) != (current.st_dev, current.st_ino)
        ):
            raise ProvisionError("provision_enrollment_invalid")
    private(path)
    return data


@contextmanager
def _exclusive(directory: Path):
    private(directory, directory=True)
    path = directory / "owner.lock"
    private(path)
    if path.stat().st_size != 1:
        raise ProvisionError("provision_enrollment_invalid")
    descriptor = os.open(path, os.O_RDWR | getattr(os, "O_NOFOLLOW", 0))
    os.set_inheritable(descriptor, False)
    with os.fdopen(descriptor, "r+b") as handle:
        opened, current = os.fstat(handle.fileno()), path.stat()
        if opened.st_nlink != 1 or (opened.st_dev, opened.st_ino) != (
            current.st_dev,
            current.st_ino,
        ):
            raise ProvisionError("provision_enrollment_invalid")
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise ProvisionError("provision_owner_running") from None
        try:
            yield
        finally:
            if os.name == "nt":
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


class EnrollmentStore:
    """Formato1 imutável. Falhas deixam evidências; abrir nunca cria ou completa."""

    @classmethod
    def initialize(
        cls,
        directory: Path,
        bootstrap_file: Path,
        *,
        binding: EnrollmentBinding,
        cipher: Cipher | None = None,
        now: datetime | None = None,
    ):
        binding = _binding(binding)
        directory, bootstrap_file = Path(directory).absolute(), Path(bootstrap_file).absolute()
        private(directory.parent, directory=True)
        if directory.exists() or directory.is_symlink():
            raise ProvisionError("provision_state_already_present")
        try:
            started = time.monotonic()
            explicit_now = now is not None
            private(bootstrap_file.parent, directory=True)
            original = _read(bootstrap_file, MAX_JSON)
            bootstrap = _decode(original, EnrollmentBootstrap)
            _same_binding(bootstrap, binding)
            now = now if now is not None else datetime.now(UTC)
            if (
                type(now) is not datetime
                or now.tzinfo is None
                or now.utcoffset() is None
                or not (0 < (bootstrap.expires_at - now).total_seconds() <= 900)
            ):
                raise ProvisionError("provision_enrollment_expired")
            cipher = _cipher(cipher)
            identity = _Identity(format=1, store_id=uuid4(), **binding.model_dump())
            state = EnrollmentState(
                **identity.model_dump(), provisioner_credential=bootstrap.provisioner_credential
            )
            value = state.model_dump(mode="json")
            value["provisioner_credential"] = state.provisioner_credential.get_secret_value()
            plaintext = canonical(value)
            encrypted = cipher.encrypt(plaintext)
            if type(encrypted) is not bytes or not 0 < len(encrypted) <= MAX_BLOB:
                raise ProvisionError("provision_enrollment_invalid")
            # A raiz não é reciclada: mkdir exclusivo conserva evidência desde aqui.
            directory.mkdir(mode=0o700)
            check_private(directory, directory=True, protect=True)
            sync_directory(directory.parent)
            create(directory / "identity.json", canonical(identity.model_dump(mode="json")))
            create(directory / "owner.lock", b"0")
            sync_directory(directory)
            with _exclusive(directory):
                staged = directory / "staged.bin"
                create(staged, encrypted)
                sync_directory(directory)
                verified = _decode(cipher.decrypt(_read(staged, MAX_BLOB)), EnrollmentState)
                if verified != state:
                    raise ProvisionError("provision_enrollment_invalid")
                _same_binding(verified, binding)
                # Vínculo e bytes originais são reavaliados imediatamente antes de apagar.
                if _read(bootstrap_file, MAX_JSON) != original:
                    raise ProvisionError("provision_enrollment_invalid")
                elapsed = time.monotonic() - started
                current_time = now + timedelta(seconds=elapsed)
                if not explicit_now:
                    current_time = max(current_time, datetime.now(UTC))
                if elapsed < 0 or current_time >= bootstrap.expires_at:
                    raise ProvisionError("provision_enrollment_expired")
                bootstrap_file.unlink()
                sync_directory(bootstrap_file.parent)
                if bootstrap_file.exists() or bootstrap_file.is_symlink():
                    raise ProvisionError("provision_enrollment_invalid")
                create(directory / "credentials.bin", encrypted)
                sync_directory(directory)
                verified = _decode(
                    cipher.decrypt(_read(directory / "credentials.bin", MAX_BLOB)), EnrollmentState
                )
                if verified != state:
                    raise ProvisionError("provision_enrollment_invalid")
                _same_binding(verified, binding)
                staged.unlink()
                sync_directory(directory)
            return cls.open(directory, binding=binding, cipher=cipher)
        except OSError, HostError, ValueError, TypeError, ValidationError, RecursionError:
            raise ProvisionError("provision_enrollment_unavailable") from None

    @classmethod
    def open(cls, directory: Path, *, binding: EnrollmentBinding, cipher: Cipher | None = None):
        store = cls.__new__(cls)
        store.directory = Path(directory).absolute()
        store.binding = _binding(binding)
        store._owner_thread = None
        try:
            store.cipher = _cipher(cipher)
            store.load()
        except OSError, HostError, ValueError, TypeError, ValidationError, RecursionError:
            raise ProvisionError("provision_enrollment_invalid") from None
        return store

    @contextmanager
    def lock(self):
        if self._owner_thread is not None:
            raise ProvisionError("provision_lock_required")
        try:
            with _exclusive(self.directory):
                self._owner_thread = threading.get_ident()
                try:
                    yield
                finally:
                    self._owner_thread = None
        except OSError, HostError:
            raise ProvisionError("provision_enrollment_unavailable") from None

    def _load_locked(self):
        private(self.directory, directory=True)
        files = {path.name for path in self.directory.iterdir()}
        if files != FINAL_FILES:
            raise ProvisionError("provision_enrollment_invalid")
        identity = _decode(_read(self.directory / "identity.json", MAX_JSON), _Identity)
        _same_binding(identity, self.binding)
        state = _decode(
            self.cipher.decrypt(_read(self.directory / "credentials.bin", MAX_BLOB)),
            EnrollmentState,
        )
        _same_binding(state, self.binding)
        if state.model_dump(exclude={"provisioner_credential"}) != identity.model_dump():
            raise ProvisionError("provision_enrollment_invalid")
        if {path.name for path in self.directory.iterdir()} != FINAL_FILES:
            raise ProvisionError("provision_enrollment_invalid")
        return state

    def load(self) -> EnrollmentState:
        try:
            if self._owner_thread == threading.get_ident():
                return self._load_locked()
            with self.lock():
                return self._load_locked()
        except OSError, HostError, ValueError, TypeError, ValidationError, RecursionError:
            raise ProvisionError("provision_enrollment_invalid") from None

    def check_only(self) -> EnrollmentCheck:
        state = self.load()
        return EnrollmentCheck(store_id=state.store_id, **{n: getattr(state, n) for n in ID_FIELDS})
