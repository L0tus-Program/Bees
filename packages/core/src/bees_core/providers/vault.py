"""Credenciais cifradas fora do banco e sem chave simétrica gerada no diretório.

DPAPI usa o usuário Windows atual. Fernet exige chave externa. Estes mecanismos
não protegem contra comprometimento integral da conta/processo do serviço.
"""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
import re
import stat
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Protocol
from uuid import UUID, uuid4

from cryptography.fernet import Fernet
from pydantic import BaseModel, ConfigDict, SecretStr

from bees_core.providers.errors import ProviderError

REFERENCE = re.compile(
    r"vault:[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\Z"
)


class EncryptionBackend(Protocol):
    name: str

    def encrypt(self, data: bytes) -> bytes: ...

    def decrypt(self, data: bytes) -> bytes: ...


class VaultStatus(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    available: bool
    backend: str
    reason: str | None = None


class FernetBackend:
    """Criptografia autenticada com chave provisionada externamente."""

    name = "fernet"

    def __init__(self, key: SecretStr) -> None:
        if not isinstance(key, SecretStr):
            raise ProviderError("secret_unavailable")
        try:
            self._fernet = Fernet(key.get_secret_value().encode("ascii"))
        except ValueError, UnicodeError:
            raise ProviderError("secret_unavailable") from None

    def encrypt(self, data: bytes) -> bytes:
        return self._fernet.encrypt(data)

    def decrypt(self, data: bytes) -> bytes:
        return self._fernet.decrypt(data)


class _DataBlob(ctypes.Structure):
    _fields_ = [("length", ctypes.c_uint32), ("data", ctypes.POINTER(ctypes.c_ubyte))]


class DPAPIBackend:
    """DPAPI CurrentUser, sem LOCAL_MACHINE e sem interface nativa interativa."""

    name = "dpapi_current_user"
    UI_FORBIDDEN = 0x1

    def __init__(self) -> None:
        if os.name != "nt":
            raise ProviderError("secret_unavailable")
        try:
            crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            self._protect = crypt32.CryptProtectData
            self._unprotect = crypt32.CryptUnprotectData
            parameters = [
                ctypes.POINTER(_DataBlob),
                ctypes.c_void_p,
                ctypes.POINTER(_DataBlob),
                ctypes.c_void_p,
                ctypes.c_void_p,
                ctypes.c_uint32,
                ctypes.POINTER(_DataBlob),
            ]
            self._protect.argtypes = parameters
            self._unprotect.argtypes = parameters
            self._protect.restype = self._unprotect.restype = ctypes.c_int
            self._free = kernel32.LocalFree
            self._free.argtypes = [ctypes.c_void_p]
            self._free.restype = ctypes.c_void_p
        except OSError, AttributeError:
            raise ProviderError("secret_unavailable") from None

    def _transform(self, data: bytes, *, decrypt: bool) -> bytes:
        source_buffer = ctypes.create_string_buffer(data)
        source = _DataBlob(len(data), ctypes.cast(source_buffer, ctypes.POINTER(ctypes.c_ubyte)))
        destination = _DataBlob()
        operation = self._unprotect if decrypt else self._protect
        try:
            if not operation(
                ctypes.byref(source),
                None,
                None,
                None,
                None,
                self.UI_FORBIDDEN,
                ctypes.byref(destination),
            ):
                raise ProviderError("secret_unavailable")
            if not destination.data or not 0 < destination.length <= 65536:
                raise ProviderError("secret_unavailable")
            return ctypes.string_at(destination.data, destination.length)
        finally:
            ctypes.memset(source_buffer, 0, len(source_buffer))
            if destination.data:
                # Buffers nativos de plaintext são apagados antes de LocalFree.
                ctypes.memset(destination.data, 0, destination.length)
                self._free(destination.data)

    def encrypt(self, data: bytes) -> bytes:
        return self._transform(data, decrypt=False)

    def decrypt(self, data: bytes) -> bytes:
        return self._transform(data, decrypt=True)


class FileSecretVault:
    def __init__(
        self,
        directory: str | Path,
        *,
        key: SecretStr | None = None,
        backend: EncryptionBackend | None = None,
        max_secrets: int = 1024,
        max_secret_bytes: int = 8192,
        max_ciphertext_bytes: int = 65536,
    ) -> None:
        if any(
            isinstance(value, bool) or not isinstance(value, int) or value < 1
            for value in (
                max_secrets,
                max_secret_bytes,
                max_ciphertext_bytes,
            )
        ):
            raise ValueError("Limites do cofre precisam ser inteiros positivos.")
        self.directory = Path(os.path.abspath(Path(directory).expanduser()))
        self.max_secrets = max_secrets
        self.max_secret_bytes = min(max_secret_bytes, 8192)
        self.max_ciphertext_bytes = min(max_ciphertext_bytes, 65536)
        self._thread_lock = threading.RLock()
        self._backend: EncryptionBackend | None = backend
        self._reason: str | None = None
        self._backend_name = (
            backend.name if backend else ("dpapi_current_user" if os.name == "nt" else "fernet")
        )
        if self._backend is None:
            try:
                if os.name == "nt":
                    self._backend = DPAPIBackend()
                else:
                    if key is None:
                        raw = os.environ.get("BEES_VAULT_KEY")
                        key = SecretStr(raw) if raw else None
                    if key is None:
                        self._reason = "Chave externa do cofre não provisionada."
                    else:
                        self._backend = FernetBackend(key)
            except ProviderError:
                self._reason = "Proteção do cofre indisponível nesta instalação."

    @property
    def available(self) -> bool:
        return self.status().available

    def status(self) -> VaultStatus:
        reason = self._reason
        try:
            self._check_root()
        except OSError, ProviderError:
            reason = "Diretório do cofre indisponível ou inseguro."
        return VaultStatus(
            available=self._backend is not None and reason is None,
            backend=self._backend_name,
            reason=reason,
        )

    @staticmethod
    def _is_link(info: os.stat_result) -> bool:
        return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & 0x400)

    def _check_root(self) -> None:
        # Não converter previamente por resolve: isso esconderia junctions/symlinks.
        for path in [*reversed(self.directory.parents), self.directory]:
            try:
                info = path.lstat()
            except FileNotFoundError:
                continue
            if self._is_link(info) or not stat.S_ISDIR(info.st_mode):
                raise ProviderError("secret_unavailable")

    def _prepare_root(self) -> None:
        self._check_root()
        missing = []
        parent = self.directory
        while not parent.exists():
            missing.append(parent)
            parent = parent.parent
        for path in reversed(missing):
            path.mkdir(mode=0o700, exist_ok=True)
            self._check_root()
            if os.name != "nt":
                path.chmod(0o700)
        if os.name != "nt":
            self.directory.chmod(0o700)

    @staticmethod
    def _filename(reference: str) -> str:
        if not isinstance(reference, str) or REFERENCE.fullmatch(reference) is None:
            raise ProviderError("invalid_secret_reference")
        return f"{UUID(reference[6:])}.secret"

    def _path(self, filename: str) -> Path:
        self._check_root()
        if "/" in filename or "\\" in filename or filename in (".", ".."):
            raise ProviderError("invalid_secret_reference")
        path = self.directory / filename
        try:
            info = path.lstat()
        except FileNotFoundError:
            return path
        if self._is_link(info) or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ProviderError("secret_unavailable")
        if path.resolve().parent != self.directory:
            raise ProviderError("secret_unavailable")
        return path

    def _guard_handle(self, descriptor: int) -> None:
        info = os.fstat(descriptor)
        if self._is_link(info) or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ProviderError("secret_unavailable")
        if os.name == "nt":
            import msvcrt

            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            get_path = kernel32.GetFinalPathNameByHandleW
            get_path.argtypes = [
                ctypes.c_void_p,
                ctypes.c_wchar_p,
                ctypes.c_uint32,
                ctypes.c_uint32,
            ]
            get_path.restype = ctypes.c_uint32
            buffer = ctypes.create_unicode_buffer(32768)
            length = get_path(msvcrt.get_osfhandle(descriptor), buffer, len(buffer), 0)
            if length == 0 or length >= len(buffer):
                raise ProviderError("secret_unavailable")
            resolved = buffer.value
            if resolved.startswith("\\\\?\\UNC\\"):
                resolved = "\\\\" + resolved[8:]
            elif resolved.startswith("\\\\?\\"):
                resolved = resolved[4:]
            # A raiz esperada é fixa; resolve() aqui poderia seguir uma junction nova.
            if Path(resolved).parent != self.directory:
                raise ProviderError("secret_unavailable")

    def _windows_root_handle(self) -> int:
        """Manter raiz aberta sem compartilhar DELETE impede sua substituição."""
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        create = kernel32.CreateFileW
        create.argtypes = [
            ctypes.c_wchar_p,
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.c_void_p,
        ]
        create.restype = ctypes.c_void_p
        handle = create(str(self.directory), 0x80, 0x3, None, 3, 0x02200000, None)
        if handle == ctypes.c_void_p(-1).value:
            raise ProviderError("secret_unavailable")
        try:
            self._check_root()
            get_path = kernel32.GetFinalPathNameByHandleW
            get_path.argtypes = [
                ctypes.c_void_p,
                ctypes.c_wchar_p,
                ctypes.c_uint32,
                ctypes.c_uint32,
            ]
            get_path.restype = ctypes.c_uint32
            buffer = ctypes.create_unicode_buffer(32768)
            length = get_path(handle, buffer, len(buffer), 0)
            if length == 0 or length >= len(buffer):
                raise ProviderError("secret_unavailable")
            resolved = buffer.value
            if resolved.startswith("\\\\?\\UNC\\"):
                resolved = "\\\\" + resolved[8:]
            elif resolved.startswith("\\\\?\\"):
                resolved = resolved[4:]
            if Path(resolved) != self.directory:
                raise ProviderError("secret_unavailable")
            return handle
        except BaseException:
            self._close_windows_handle(handle)
            raise

    @staticmethod
    def _close_windows_handle(handle: int) -> None:
        close = ctypes.WinDLL("kernel32", use_last_error=True).CloseHandle
        close.argtypes = [ctypes.c_void_p]
        close.restype = ctypes.c_int
        close(handle)

    def _windows_unlink(self, path: Path) -> None:
        """Excluir pelo handle validado evita abrir novamente um caminho trocado."""
        import msvcrt

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        create = kernel32.CreateFileW
        create.argtypes = [
            ctypes.c_wchar_p,
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.c_void_p,
        ]
        create.restype = ctypes.c_void_p
        # GENERIC_READ | DELETE | FILE_READ_ATTRIBUTES; OPEN_REPARSE_POINT.
        handle = create(str(path), 0x80010080, 0, None, 3, 0x00200000, None)
        if handle == ctypes.c_void_p(-1).value:
            if ctypes.get_last_error() in (2, 3):
                return
            raise ProviderError("secret_unavailable")
        try:
            descriptor = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
        except BaseException:
            close = kernel32.CloseHandle
            close.argtypes = [ctypes.c_void_p]
            close(handle)
            raise
        try:
            self._guard_handle(descriptor)
            disposition = ctypes.c_ubyte(1)
            delete = kernel32.SetFileInformationByHandle
            delete.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32]
            delete.restype = ctypes.c_int
            if not delete(handle, 4, ctypes.byref(disposition), ctypes.sizeof(disposition)):
                raise ProviderError("secret_unavailable")
        finally:
            os.close(descriptor)

    def _open(self, filename: str, flags: int, directory_fd: int | None) -> int:
        path = self._path(filename)
        flags |= getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
        if directory_fd is not None:
            descriptor = os.open(filename, flags, 0o600, dir_fd=directory_fd)
        else:
            descriptor = os.open(path, flags, 0o600)
        try:
            self._guard_handle(descriptor)
            if os.name != "nt":
                os.fchmod(descriptor, 0o600)
        except BaseException:
            os.close(descriptor)
            raise
        return descriptor

    @contextmanager
    def _locked(self, *, create: bool) -> Iterator[int | None]:
        with self._thread_lock:
            if create:
                self._prepare_root()
            else:
                self._check_root()
                if not self.directory.exists():
                    raise ProviderError("secret_unavailable")
            directory_fd = None
            windows_root_handle = None
            lock_fd = None
            acquired = False
            try:
                if os.name != "nt":
                    directory_fd = os.open(
                        self.directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
                    )
                else:
                    windows_root_handle = self._windows_root_handle()
                lock_fd = self._open(".lock", os.O_RDWR | os.O_CREAT, directory_fd)
                if os.fstat(lock_fd).st_size == 0:
                    os.write(lock_fd, b"0")
                deadline = time.monotonic() + 5
                while True:
                    try:
                        if os.name == "nt":
                            import msvcrt

                            os.lseek(lock_fd, 0, os.SEEK_SET)
                            msvcrt.locking(lock_fd, msvcrt.LK_NBLCK, 1)
                        else:
                            import fcntl

                            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        acquired = True
                        break
                    except OSError:
                        if time.monotonic() >= deadline:
                            raise ProviderError("secret_unavailable") from None
                        time.sleep(0.025)
                self._check_root()
                yield directory_fd
            finally:
                if lock_fd is not None:
                    if acquired:
                        if os.name == "nt":
                            import msvcrt

                            os.lseek(lock_fd, 0, os.SEEK_SET)
                            msvcrt.locking(lock_fd, msvcrt.LK_UNLCK, 1)
                        else:
                            import fcntl

                            fcntl.flock(lock_fd, fcntl.LOCK_UN)
                    os.close(lock_fd)
                if directory_fd is not None:
                    os.close(directory_fd)
                if windows_root_handle is not None:
                    self._close_windows_handle(windows_root_handle)

    def _unlink(self, filename: str, directory_fd: int | None) -> None:
        path = self._path(filename)
        try:
            if directory_fd is not None:
                os.unlink(filename, dir_fd=directory_fd)
            else:
                self._windows_unlink(path)
        except FileNotFoundError:
            pass

    def _secret(self, secret: SecretStr) -> str:
        if not isinstance(secret, SecretStr):
            raise ProviderError("invalid_secret")
        value = secret.get_secret_value()
        if not value or not value.strip() or len(value.encode("utf-8")) > self.max_secret_bytes:
            raise ProviderError("invalid_secret")
        if any(ord(character) < 32 or ord(character) > 126 for character in value):
            raise ProviderError("invalid_secret")
        return value

    def put(self, secret: SecretStr) -> str:
        value = self._secret(secret)
        if self._backend is None:
            raise ProviderError("secret_unavailable")
        reference = f"vault:{uuid4()}"
        filename = self._filename(reference)
        staging = f".staging-{uuid4()}.tmp"
        payload = json.dumps(
            {
                "version": 1,
                "reference": reference,
                "value": value,
                "digest": hashlib.sha256(value.encode("utf-8")).hexdigest(),
            },
            separators=(",", ":"),
        ).encode("utf-8")
        try:
            encrypted = self._backend.encrypt(payload)
            if (
                not isinstance(encrypted, bytes)
                or not 0 < len(encrypted) <= self.max_ciphertext_bytes
            ):
                raise ProviderError("secret_unavailable")
            with self._locked(create=True) as directory_fd:
                count = 0
                with os.scandir(self.directory) as entries:
                    for entry in entries:
                        if entry.name != ".lock":
                            count += 1
                            if count >= self.max_secrets:
                                raise ProviderError("secret_unavailable")
                target = self._path(filename)
                if target.exists():
                    raise ProviderError("secret_unavailable")
                descriptor = self._open(staging, os.O_WRONLY | os.O_CREAT | os.O_EXCL, directory_fd)
                published = False
                try:
                    with os.fdopen(descriptor, "wb") as file:
                        file.write(encrypted)
                        file.flush()
                        os.fsync(file.fileno())
                    # Revalidar ambos os caminhos absolutos antes de mover no Windows.
                    staged = self._path(staging)
                    target = self._path(filename)
                    if directory_fd is None:
                        os.replace(staged, target)
                    else:
                        os.replace(
                            staging, filename, src_dir_fd=directory_fd, dst_dir_fd=directory_fd
                        )
                    published = True
                    if directory_fd is not None:
                        os.fsync(directory_fd)
                except BaseException:
                    if published:
                        self._unlink(filename, directory_fd)
                    raise
                finally:
                    self._unlink(staging, directory_fd)
            return reference
        except ProviderError:
            raise
        except Exception:
            raise ProviderError("secret_unavailable") from None

    def resolve(self, reference: str) -> SecretStr:
        filename = self._filename(reference)
        if self._backend is None:
            raise ProviderError("secret_unavailable")
        try:
            with self._locked(create=False) as directory_fd:
                descriptor = self._open(
                    filename, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0), directory_fd
                )
                with os.fdopen(descriptor, "rb") as file:
                    if os.fstat(file.fileno()).st_size > self.max_ciphertext_bytes:
                        raise ProviderError("secret_unavailable")
                    encrypted = file.read(self.max_ciphertext_bytes + 1)
                if not 0 < len(encrypted) <= self.max_ciphertext_bytes:
                    raise ProviderError("secret_unavailable")
                payload = json.loads(self._backend.decrypt(encrypted))
                if (
                    not isinstance(payload, dict)
                    or set(payload) != {"version", "reference", "value", "digest"}
                    or type(payload["version"]) is not int
                    or payload["version"] != 1
                    or payload["reference"] != reference
                    or not isinstance(payload["value"], str)
                ):
                    raise ProviderError("secret_unavailable")
                value = self._secret(SecretStr(payload["value"]))
                if payload["digest"] != hashlib.sha256(value.encode("utf-8")).hexdigest():
                    raise ProviderError("secret_unavailable")
                return SecretStr(value)
        except ProviderError:
            raise
        except Exception:
            raise ProviderError("secret_unavailable") from None

    def delete(self, reference: str) -> None:
        filename = self._filename(reference)
        try:
            self._check_root()
            if not self.directory.exists():
                return
            with self._locked(create=False) as directory_fd:
                self._unlink(filename, directory_fd)
                if directory_fd is not None:
                    os.fsync(directory_fd)
        except ProviderError:
            raise
        except Exception:
            raise ProviderError("secret_unavailable") from None
