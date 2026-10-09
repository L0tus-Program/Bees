"""Assets/área de hardware separados do controle; descoberta fixa e checks offline.

IDs UUIDv5 identificam a topologia layout1, não uma geração nem proteção de rollback.
initialize é chamado somente pela composição humana com RootStore virgem travado.
Não prepara hardware/ACL VMMS, copia kit, baixa artefatos ou inicia um executor.
"""

import os
import threading
from contextlib import contextmanager
from typing import Literal
from uuid import UUID, uuid5

from pydantic import ValidationError, model_validator

from bees_host.errors import HostError
from bees_host.guest_bridge.private import check_ancestors
from bees_host.guest_bridge.protocol import BridgeError
from bees_host.provisioning import root_store
from bees_host.provisioning.contracts import Closed, ProvisionError, canonical
from bees_host.provisioning.enrollment import _decode, _read
from bees_host.provisioning.hardware_security import validate_hardware_file, validate_hardware_root
from bees_host.provisioning.image import ISO_NAME, PAYLOAD_NAME
from bees_host.provisioning.journal import create, private, sync_directory
from bees_host.security import check_private

BINDING_FIELDS = (
    "root_id",
    "enrollment_store_id",
    "ledger_store_id",
    "installation_id",
    "host_id",
    "provisioner_id",
)
KIT_FILES = frozenset(
    {
        ISO_NAME,
        PAYLOAD_NAME,
        "payload-inventory.json",
        "kit-info.json",
        "README.txt",
        "SHA256SUMS",
        "SHA256SUMS.sign",
        "debian-cd.gpg",
        "SHA256SUMS.json",
    }
)
ASSETS_FILES = frozenset({"identity.json", "owner.lock", "kit"})
WORKSPACE_FILES = frozenset({"identity.json", "owner.lock"})


class AssetsBinding(Closed):
    root_id: UUID
    enrollment_store_id: UUID
    ledger_store_id: UUID
    installation_id: UUID
    host_id: UUID
    provisioner_id: UUID

    @model_validator(mode="after")
    def valid_binding(self):
        if any(not getattr(self, name).int for name in BINDING_FIELDS):
            raise ValueError("assets_invalid")
        return self

    @classmethod
    def from_root_check(cls, check: root_store.RootCheck):
        if type(check) is not root_store.RootCheck:
            raise ProvisionError("provision_assets_binding_invalid")
        try:
            checked = root_store.RootCheck.model_validate_json(check.model_dump_json())
            return cls(**checked.model_dump(include=set(BINDING_FIELDS)))
        except TypeError, ValueError, ValidationError, RecursionError:
            raise ProvisionError("provision_assets_binding_invalid") from None

    @property
    def assets_id(self):
        return uuid5(self.root_id, "bees-provisioner-assets/layout1")

    @property
    def kit_id(self):
        return uuid5(self.root_id, "bees-provisioner-assets/kit/layout1")

    @property
    def workspace_id(self):
        return uuid5(self.root_id, "bees-provisioner-hardware/layout1")


class _Identity(AssetsBinding):
    format: Literal[1]
    # O valor persistido repete explicitamente os IDs derivados.
    sealed_assets_id: UUID
    sealed_kit_id: UUID
    sealed_workspace_id: UUID

    @model_validator(mode="after")
    def valid_identity(self):
        if (
            type(self.format) is not int
            or self.sealed_assets_id != self.assets_id
            or self.sealed_kit_id != self.kit_id
            or self.sealed_workspace_id != self.workspace_id
        ):
            raise ValueError("assets_invalid")
        return self


class AssetsCheck(AssetsBinding):
    configured_assets_local: Literal[True] = True
    kit_present_local: bool


class WorkspaceCheck(AssetsBinding):
    configured_workspace_local: Literal[True] = True
    plan_count: int


def _binding(value):
    if type(value) is not AssetsBinding:
        raise ProvisionError("provision_assets_binding_invalid")
    try:
        return AssetsBinding.model_validate_json(value.model_dump_json())
    except TypeError, ValueError, ValidationError, RecursionError:
        raise ProvisionError("provision_assets_binding_invalid") from None


def _identity(binding):
    return _Identity(
        **binding.model_dump(),
        format=1,
        sealed_assets_id=binding.assets_id,
        sealed_kit_id=binding.kit_id,
        sealed_workspace_id=binding.workspace_id,
    )


def _roots():
    control = root_store._native_root()
    if (
        not control.is_absolute()
        or os.name == "nt"
        and (
            len(control.drive) != 2
            or not control.drive[0].isascii()
            or not control.drive[0].isalpha()
            or control.drive[1] != ":"
        )
    ):
        raise ProvisionError("provision_assets_invalid")
    assets = control.with_name("bees-provisioner-assets")
    hardware = control.with_name("bees-provisioner-hardware")
    root_store._require_local_volume(assets)
    root_store._require_local_volume(hardware)
    try:
        if any(
            getattr(parent.lstat(), "st_file_attributes", 0) & 0x400 for parent in control.parents
        ):
            raise ProvisionError("provision_assets_invalid")
    except OSError:
        raise ProvisionError("provision_assets_invalid") from None
    return assets, hardware


def _hardware_read(path, limit):
    validate_hardware_file(path)
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(descriptor, "rb") as source:
        initial = os.fstat(source.fileno())
        if initial.st_nlink != 1 or not 0 < initial.st_size <= limit:
            raise ProvisionError("provision_workspace_invalid")
        data = source.read(limit + 1)
        final, current = os.fstat(source.fileno()), path.stat()
        if (
            len(data) != initial.st_size
            or final.st_size != initial.st_size
            or final.st_mtime_ns != initial.st_mtime_ns
            or (initial.st_dev, initial.st_ino) != (current.st_dev, current.st_ino)
        ):
            raise ProvisionError("provision_workspace_invalid")
    validate_hardware_file(path)
    return data


class _Store:
    def __init__(self):
        raise ProvisionError("provision_assets_explicit_required")

    @classmethod
    def _instance(cls, binding):
        store = cls.__new__(cls)
        store.binding = _binding(binding)
        assets, hardware = _roots()
        store._directory = assets if cls is AssetsStore else hardware
        store._hardware_directory = hardware
        store._mutex = threading.Lock()
        store._owner_thread = None
        return store

    @classmethod
    def open(cls, *, binding: AssetsBinding):
        store = cls._instance(binding)
        store.check_only()
        return store

    @property
    def assets_id(self):
        return self.binding.assets_id

    @property
    def kit_id(self):
        return self.binding.kit_id

    @property
    def workspace_id(self):
        return self.binding.workspace_id

    @contextmanager
    def lock(self):
        if not self._mutex.acquire(blocking=False):
            raise ProvisionError("provision_lock_required")
        try:
            self._check()
            path = self._directory / "owner.lock"
            descriptor = os.open(path, os.O_RDWR | getattr(os, "O_NOFOLLOW", 0))
            os.set_inheritable(descriptor, False)
            with os.fdopen(descriptor, "r+b") as handle:
                opened, current = os.fstat(handle.fileno()), path.stat()
                if opened.st_nlink != 1 or (opened.st_dev, opened.st_ino) != (
                    current.st_dev,
                    current.st_ino,
                ):
                    raise ProvisionError("provision_assets_invalid")
                try:
                    if os.name == "nt":
                        import msvcrt

                        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl

                        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                except OSError:
                    raise ProvisionError("provision_owner_running") from None
                self._owner_thread = threading.get_ident()
                try:
                    yield self._check_locked()
                    self._check_locked()
                finally:
                    self._owner_thread = None
                    if os.name == "nt":
                        handle.seek(0)
                        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except (
            OSError,
            HostError,
            BridgeError,
            ValueError,
            TypeError,
            ValidationError,
            RecursionError,
        ):
            raise ProvisionError("provision_assets_invalid") from None
        finally:
            self._mutex.release()

    def assert_locked(self):
        if self._owner_thread != threading.get_ident():
            raise ProvisionError("provision_lock_required")

    def _check_locked(self):
        self.assert_locked()
        try:
            return self._check()
        except (
            OSError,
            HostError,
            BridgeError,
            ValueError,
            TypeError,
            ValidationError,
            RecursionError,
        ):
            raise ProvisionError("provision_assets_invalid") from None

    def check_only(self):
        if self._owner_thread == threading.get_ident():
            return self._check_locked()
        with self.lock() as checked:
            return checked


class AssetsStore(_Store):
    @classmethod
    def initialize(cls, *, binding: AssetsBinding):
        """Composição humana exige Root virgem/lock; este método não abre o controle."""
        store = cls._instance(binding)
        try:
            check_ancestors(store._directory)
            if store._hardware_directory.exists() or store._hardware_directory.is_symlink():
                raise ProvisionError("provision_workspace_already_present")
            if store._directory.exists() or store._directory.is_symlink():
                raise ProvisionError("provision_state_already_present")
            try:
                store._directory.mkdir(mode=0o700)
            except FileExistsError:
                raise ProvisionError("provision_state_already_present") from None
            check_private(store._directory, directory=True, protect=True)
            create(
                store._directory / "identity.json",
                canonical(_identity(store.binding).model_dump(mode="json")),
            )
            create(store._directory / "owner.lock", b"0")
            kit = store._directory / "kit"
            kit.mkdir(mode=0o700)
            check_private(kit, directory=True, protect=True)
            sync_directory(store._directory)
            sync_directory(store._directory.parent)
            store.check_only()
            return store
        except (
            OSError,
            HostError,
            BridgeError,
            ValueError,
            TypeError,
            ValidationError,
            RecursionError,
        ):
            raise ProvisionError("provision_assets_invalid") from None

    @property
    def kit_directory(self):
        return self._directory / "kit"

    def _check(self):
        if not self._directory.exists():
            raise ProvisionError("provision_state_missing")
        private(self._directory, directory=True)
        if {path.name for path in self._directory.iterdir()} != ASSETS_FILES:
            raise ProvisionError("provision_assets_invalid")
        identity = _decode(_read(self._directory / "identity.json", 8192), _Identity)
        if identity != _identity(self.binding):
            raise ProvisionError("provision_assets_binding_invalid")
        private(self._directory / "owner.lock")
        if (self._directory / "owner.lock").stat().st_size != 1:
            raise ProvisionError("provision_assets_invalid")
        private(self.kit_directory, directory=True)
        entries = {path.name for path in self.kit_directory.iterdir()}
        if entries and entries != KIT_FILES:
            raise ProvisionError("provision_assets_invalid")
        for name in entries:
            private(self.kit_directory / name)
        return AssetsCheck(**self.binding.model_dump(), kit_present_local=bool(entries))


class WorkspaceStore(_Store):
    """Área previamente preparada pelo operador; sem initialize/grant/repair público."""

    @property
    def storage_root(self):
        return self._directory

    def plan_ids(self) -> frozenset[UUID]:
        """Inventário integral sob o dono; não associa/adota pastas como corridas."""
        self.assert_locked()
        self._check_locked()
        try:
            names = {path.name for path in self._directory.iterdir()} - WORKSPACE_FILES
            result = frozenset(UUID(name) for name in names)
        except OSError, ValueError:
            raise ProvisionError("provision_workspace_invalid") from None
        self._check_locked()
        if {path.name for path in self._directory.iterdir()} - WORKSPACE_FILES != names:
            raise ProvisionError("provision_workspace_invalid")
        return result

    def _check(self):
        if not self._directory.exists():
            raise ProvisionError("provision_state_missing")
        validate_hardware_root(self._directory)
        entries = {path.name for path in self._directory.iterdir()}
        if not WORKSPACE_FILES <= entries:
            raise ProvisionError("provision_workspace_invalid")
        identity = _decode(_hardware_read(self._directory / "identity.json", 8192), _Identity)
        if identity != _identity(self.binding):
            raise ProvisionError("provision_assets_binding_invalid")
        validate_hardware_file(self._directory / "owner.lock")
        if (self._directory / "owner.lock").stat().st_size != 1:
            raise ProvisionError("provision_workspace_invalid")
        plans = entries - WORKSPACE_FILES
        for name in plans:
            if not UUID(name).int or str(UUID(name)) != name:
                raise ProvisionError("provision_workspace_invalid")
            # ACL/dados internos por plano são verificados pelo script específico
            # com VMID físico; não relaxar a guarda da raiz para aceitar esse SID.
            path = self._directory / name
            info = path.lstat()
            if (
                path.is_symlink()
                or not path.is_dir()
                or getattr(info, "st_file_attributes", 0) & 0x400
            ):
                raise ProvisionError("provision_workspace_invalid")
        return WorkspaceCheck(**self.binding.model_dump(), plan_count=len(plans))
