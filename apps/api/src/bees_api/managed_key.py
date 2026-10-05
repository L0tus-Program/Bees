"""Provisionamento Linux explícito: chave privada fora do volume dos dados.

Separar volumes evita incluir a chave no backup/exportação comum dos dados;
não protege contra comprometimento da conta/processo que pode acessar ambos.
Arquivos inválidos, perdidos ou substituídos exigem recuperação pelo operador.
"""

import hashlib
import os
import stat
import time
from contextlib import contextmanager
from pathlib import Path

from cryptography.fernet import Fernet
from pydantic import SecretStr

from bees_core.storage.database import Database

MARKER = ".vault-key-id"


class ManagedKeyError(RuntimeError):
    def __init__(self) -> None:
        super().__init__(
            "Chave gerenciada indisponível ou insegura. Confira volumes e permissões; "
            "restaure dados e chave correspondentes, sem regenerar credenciais existentes."
        )


@contextmanager
def _directory(path: Path, *, create: bool = False, private: bool = False):
    """Abrir cada componente por descritor impede seguir symlinks nos ancestrais."""
    descriptor = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in path.parts[1:]:
            if create:
                try:
                    os.mkdir(part, mode=0o700, dir_fd=descriptor)
                except FileExistsError:
                    pass
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        info = os.fstat(descriptor)
        if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & (
            0o077 if private else 0o022
        ):
            raise ManagedKeyError()
        yield descriptor
    finally:
        os.close(descriptor)


def _private_file(descriptor: int, *, size: int | None = None) -> None:
    info = os.fstat(descriptor)
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_nlink != 1
        or info.st_uid != os.geteuid()
        or stat.S_IMODE(info.st_mode) & 0o077
        or (size is not None and info.st_size != size)
    ):
        raise ManagedKeyError()


def _read(directory: int, filename: str, *, size: int) -> bytes | None:
    try:
        descriptor = os.open(
            filename, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory
        )
    except FileNotFoundError:
        return None
    try:
        _private_file(descriptor, size=size)
        value = os.read(descriptor, size + 1)
        if len(value) != size:
            raise ManagedKeyError()
        return value
    finally:
        os.close(descriptor)


def _create(directory: int, filename: str, value: bytes) -> None:
    # O_EXCL: nunca sobrescrever. Um crash com arquivo parcial exige recuperação.
    descriptor = os.open(
        filename, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory
    )
    try:
        _private_file(descriptor)
        position = 0
        while position < len(value):
            written = os.write(descriptor, value[position:])
            if written <= 0:
                raise ManagedKeyError()
            position += written
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    os.fsync(directory)


@contextmanager
def _lock(directory: int):
    import fcntl

    descriptor = os.open(
        ".provision.lock",
        os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK,
        0o600,
        dir_fd=directory,
    )
    try:
        _private_file(descriptor)
        deadline = time.monotonic() + 5
        while True:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise ManagedKeyError() from None
                time.sleep(0.025)
        yield
    finally:
        # Fechar também solta o flock em caminhos de erro.
        os.close(descriptor)


def _existing_credentials(data: int, database: Database) -> bool:
    try:
        vault = os.open("vault", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=data)
    except FileNotFoundError:
        vault = None
    if vault is not None:
        try:
            if any(name != ".lock" for name in os.listdir(vault)):
                return True
        finally:
            os.close(vault)
    with database.transaction(write=False) as connection:
        return bool(
            connection.execute(
                "SELECT EXISTS(SELECT 1 FROM agents "
                "WHERE json_extract(model_config_json,'$.secret_ref') LIKE 'vault:%')"
            ).get
        )


def prepare_managed_directories(key_file: Path, data_dir: Path) -> None:
    """Validar caminhos antes de SQLite resolver/abrir o banco no startup."""
    if os.name != "posix":
        raise ManagedKeyError()
    key_file = Path(os.path.abspath(key_file.expanduser()))
    data_dir = Path(os.path.abspath(data_dir.expanduser()))
    if key_file.is_relative_to(data_dir) or data_dir.is_relative_to(key_file.parent):
        raise ManagedKeyError()
    try:
        with _directory(key_file.parent, create=True, private=True):
            with _directory(data_dir, create=True) as data:
                try:
                    descriptor = os.open(
                        "bees.sqlite3", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=data
                    )
                except FileNotFoundError:
                    return
                try:
                    _private_file(descriptor)
                finally:
                    os.close(descriptor)
    except ManagedKeyError:
        raise
    except Exception:
        raise ManagedKeyError() from None


def managed_vault_key(
    key_file: Path, data_dir: Path, database: Database, *, provision: bool = True
) -> SecretStr:
    """Gerar apenas instalação nova; reabrir sem mudar chave em reinícios.

    O marcador não é uma credencial: associa o volume de dados à chave, sem texto
    das credenciais. Remover só o volume de chaves falha mesmo com cofre vazio.
    """
    if os.name != "posix":
        raise ManagedKeyError()
    key_file = Path(os.path.abspath(key_file.expanduser()))
    data_dir = Path(os.path.abspath(data_dir.expanduser()))
    if key_file.is_relative_to(data_dir) or data_dir.is_relative_to(key_file.parent):
        raise ManagedKeyError()
    try:
        with _directory(key_file.parent, create=provision, private=True) as key_root:
            with _lock(key_root), _directory(data_dir) as data:
                marker = _read(data, MARKER, size=64)
                key = _read(key_root, key_file.name, size=44)
                if not provision and (marker is None or key is None):
                    raise ManagedKeyError()
                if key is None:
                    if marker is not None or _existing_credentials(data, database):
                        raise ManagedKeyError()
                    key = Fernet.generate_key()
                    _create(key_root, key_file.name, key)
                # Mesmo um arquivo com 44 bytes pode não conter uma chave Fernet.
                Fernet(key)
                fingerprint = hashlib.sha256(key).hexdigest().encode("ascii")
                if marker is None:
                    if _existing_credentials(data, database):
                        raise ManagedKeyError()
                    _create(data, MARKER, fingerprint)
                elif marker != fingerprint:
                    raise ManagedKeyError()
                return SecretStr(key.decode("ascii"))
    except ManagedKeyError:
        raise
    except Exception:
        # Não deixar exceptions de filesystem/SQLite/chave revelarem dados locais.
        raise ManagedKeyError() from None
