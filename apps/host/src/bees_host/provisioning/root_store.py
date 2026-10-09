"""Raiz nativa offline, única por conta, sem criação ou reparo implícitos.

A âncora externa conserva o vínculo original mesmo após perda de ``state``.
Apagar a própria âncora perde essa evidência: ausência não autoriza inscrição;
initialize só pode ser chamado por futura composição humana confiável explícita.
Journals são inventariados somente leitura; não cria transporte ou hardware.
"""

import ctypes
import os
import threading
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid4

from pydantic import ValidationError, model_validator

from bees_host.errors import HostError
from bees_host.guest_bridge.private import check_ancestors
from bees_host.guest_bridge.protocol import BridgeError
from bees_host.provisioning.contracts import (
    OPERATIONS,
    Closed,
    ProvisionError,
    ReceiptResult,
    canonical,
)
from bees_host.provisioning.enrollment import (
    EnrollmentBinding,
    EnrollmentBootstrap,
    EnrollmentStore,
    _binding,
    _cipher,
    _decode,
    _exclusive,
    _read,
    _same_binding,
)
from bees_host.provisioning.journal import Journal, create, private, sync_directory
from bees_host.provisioning.supervisor_store import SupervisorStore
from bees_host.security import Cipher, check_private

FINAL_FILES = frozenset({"identity.json", "owner.lock", "composition.json", "state"})
STATE_FILES = frozenset({"enrollment", "ledger", "plans"})


def _native_root() -> Path:
    """Fonte de sistema; não usa HOME, XDG, LOCALAPPDATA ou caminhos de modelos."""
    if os.name != "nt":
        import pwd

        return Path(pwd.getpwuid(os.getuid()).pw_dir) / "bees-provisioner"
    folder = ctypes.create_string_buffer(UUID("f1b32785-6fba-4fcf-9d55-7b8e7f157091").bytes_le, 16)
    shell = ctypes.WinDLL("shell32", use_last_error=True)
    ole = ctypes.WinDLL("ole32", use_last_error=True)
    shell.SHGetKnownFolderPath.argtypes = [
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_void_p),
    ]
    shell.SHGetKnownFolderPath.restype = ctypes.c_int32
    ole.CoTaskMemFree.argtypes = [ctypes.c_void_p]
    ole.CoInitializeEx.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    ole.CoInitializeEx.restype = ctypes.c_int32
    ole.CoUninitialize.argtypes = []
    output = ctypes.c_void_p()
    initialized = ole.CoInitializeEx(None, 2)
    if initialized not in {0, 1, -2147417850}:  # RPC_E_CHANGED_MODE: COM já ativo.
        raise ProvisionError("provision_root_unavailable")
    try:
        # DONT_VERIFY, sem CREATE: consultar não cria nem repara a pasta nativa.
        if shell.SHGetKnownFolderPath(folder, 0x4000, None, ctypes.byref(output)) or not output:
            raise ProvisionError("provision_root_unavailable")
        return Path(ctypes.wstring_at(output)) / "bees-provisioner"
    finally:
        if output:
            ole.CoTaskMemFree(output)
        if initialized in {0, 1}:
            ole.CoUninitialize()


class _Identity(EnrollmentBinding):
    format: Literal[1]
    root_id: UUID

    @model_validator(mode="after")
    def valid_identity(self):
        if type(self.format) is not int or not self.root_id.int:
            raise ValueError("root_invalid")
        return self


class _Composition(_Identity):
    enrollment_store_id: UUID
    ledger_store_id: UUID

    @model_validator(mode="after")
    def valid_composition(self):
        if not self.enrollment_store_id.int or not self.ledger_store_id.int:
            raise ValueError("root_invalid")
        return self


class RootCheck(Closed):
    root_id: UUID
    enrollment_store_id: UUID
    ledger_store_id: UUID
    installation_id: UUID
    host_id: UUID
    provisioner_id: UUID
    configured_local: Literal[True] = True
    execution_blocked_local: bool


def _directory(path: Path):
    path.mkdir(mode=0o700)
    check_private(path, directory=True, protect=True)
    sync_directory(path.parent)


def _require_local_volume(path: Path):
    if os.name == "nt":
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.GetDriveTypeW.argtypes = [ctypes.c_wchar_p]
        kernel.GetDriveTypeW.restype = ctypes.c_uint32
        if kernel.GetDriveTypeW(path.anchor) != 3:  # DRIVE_FIXED, não drive mapeado/remoto.
            raise ProvisionError("provision_root_local_required")


class RootStore:
    """Composição offline fechada; seus caminhos não são parâmetros públicos.

    configured_local não indica conexão, autoridade vigente, VM ou execução pronta.
    Ler um plano nunca recupera execução ou concede acesso ao seu journal.
    """

    @classmethod
    def _instance(cls, binding, cipher):
        store = cls.__new__(cls)
        store.binding = _binding(binding)
        store.cipher = _cipher(cipher)
        store._directory = _native_root()
        if (
            not store._directory.is_absolute()
            or os.name == "nt"
            and (
                len(store._directory.drive) != 2
                or not store._directory.drive[0].isascii()
                or not store._directory.drive[0].isalpha()
                or store._directory.drive[1] != ":"
            )
        ):
            raise ProvisionError("provision_root_invalid")
        _require_local_volume(store._directory)
        store._mutex = threading.Lock()
        store._owner_thread = None
        store._identity = None
        store._composition = None
        return store

    def __init__(self):
        raise ProvisionError("provision_root_explicit_required")

    @classmethod
    def initialize(
        cls,
        *,
        binding: EnrollmentBinding,
        bootstrap: Path,
        cipher: Cipher | None = None,
    ):
        """Decisão explícita; exclusividade mkdir impede inscrição concorrente.

        Erros conservam a âncora e os filhos existentes. Não há remoção ou retry.
        """
        store = cls._instance(binding, cipher)
        directory = store._directory
        try:
            check_ancestors(directory)
            if directory.exists() or directory.is_symlink():
                raise ProvisionError("provision_state_already_present")
            # Valida a origem real do bootstrap antes de qualquer nova âncora.
            if type(bootstrap) is not type(Path()):
                raise ProvisionError("provision_root_bootstrap_invalid")
            private(bootstrap.parent, directory=True)
            payload = _decode(_read(bootstrap, 8192), EnrollmentBootstrap)
            _same_binding(payload, store.binding)
            if not 0 < (payload.expires_at - datetime.now(UTC)).total_seconds() <= 900:
                raise ProvisionError("provision_enrollment_expired")
            try:
                _directory(directory)
            except FileExistsError:
                raise ProvisionError("provision_state_already_present") from None
            identity = _Identity(format=1, root_id=uuid4(), **store.binding.model_dump())
            create(directory / "identity.json", canonical(identity.model_dump(mode="json")))
            create(directory / "owner.lock", b"0")
            sync_directory(directory)
            store._identity = identity
            with store._lock(initializing=True):
                state = directory / "state"
                _directory(state)
                enrolled = EnrollmentStore.initialize(
                    state / "enrollment", bootstrap, binding=store.binding, cipher=store.cipher
                )
                enrollment_id = enrolled.check_only().store_id
                ledger = SupervisorStore.initialize_for_acquisition(
                    state / "ledger",
                    installation_id=store.binding.installation_id,
                    host_id=store.binding.host_id,
                )
                try:
                    ledger_id = ledger.store_id
                finally:
                    ledger.close()
                _directory(state / "plans")
                sync_directory(state)
                composition = _Composition(
                    **identity.model_dump(),
                    enrollment_store_id=enrollment_id,
                    ledger_store_id=ledger_id,
                )
                create(
                    directory / "composition.json",
                    canonical(composition.model_dump(mode="json")),
                )
                sync_directory(directory)
                store._composition = composition
                store._check_locked()
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
            raise ProvisionError("provision_root_unavailable") from None

    @classmethod
    def open(cls, *, binding: EnrollmentBinding, cipher: Cipher | None = None):
        store = cls._instance(binding, cipher)
        store.check_only()
        return store

    def _anchor(self, initializing=False):
        directory = self._directory
        if _native_root() != directory:
            raise ProvisionError("provision_root_invalid")
        _require_local_volume(directory)
        private(directory, directory=True)
        expected = {"identity.json", "owner.lock"} if initializing else FINAL_FILES
        if {path.name for path in directory.iterdir()} != expected:
            raise ProvisionError("provision_root_invalid")
        identity = _decode(_read(directory / "identity.json", 8192), _Identity)
        _same_binding(identity, self.binding)
        if self._identity is not None and self._identity != identity:
            raise ProvisionError("provision_root_invalid")
        private(directory / "owner.lock")
        if (directory / "owner.lock").stat().st_size != 1:
            raise ProvisionError("provision_root_invalid")
        return identity

    @contextmanager
    def _lock(self, *, initializing=False):
        if not self._mutex.acquire(blocking=False):
            raise ProvisionError("provision_lock_required")
        try:
            identity = self._anchor(initializing)
            with _exclusive(self._directory):
                if self._anchor(initializing) != identity:
                    raise ProvisionError("provision_root_invalid")
                self._owner_thread = threading.get_ident()
                try:
                    yield
                finally:
                    self._owner_thread = None
        except OSError, HostError, ValueError, TypeError, ValidationError, RecursionError:
            raise ProvisionError("provision_root_invalid") from None
        finally:
            self._mutex.release()

    @contextmanager
    def lock(self):
        """Trava exclusiva externa; métodos internos exigem o mesmo thread."""
        with self._lock():
            yield self._check_locked()

    def _check_locked(self, *, ledger=None) -> RootCheck:
        if self._owner_thread != threading.get_ident():
            raise ProvisionError("provision_lock_required")
        identity = self._anchor()
        composition = _decode(_read(self._directory / "composition.json", 8192), _Composition)
        if (
            composition.model_dump(exclude={"enrollment_store_id", "ledger_store_id"})
            != (identity.model_dump())
            or self._composition is not None
            and self._composition != composition
        ):
            raise ProvisionError("provision_root_invalid")
        state = self._directory / "state"
        private(state, directory=True)
        if {path.name for path in state.iterdir()} != STATE_FILES:
            raise ProvisionError("provision_root_invalid")
        plans = state / "plans"
        private(plans, directory=True)
        enrolled = EnrollmentStore.open(
            state / "enrollment", binding=self.binding, cipher=self.cipher
        ).check_only()
        if enrolled.store_id != composition.enrollment_store_id:
            raise ProvisionError("provision_root_invalid")
        if ledger is None:
            reader = SupervisorStore.open_read_only(state / "ledger")
            try:
                with reader.lock():
                    blocked = self._check_ledger_locked(reader, composition, state)
            finally:
                reader.close()
        else:
            blocked = self._check_ledger_locked(ledger, composition, state)
        if (
            self._anchor() != identity
            or _decode(_read(self._directory / "composition.json", 8192), _Composition)
            != composition
        ):
            raise ProvisionError("provision_root_invalid")
        self._identity, self._composition = identity, composition
        return RootCheck(
            root_id=identity.root_id,
            enrollment_store_id=composition.enrollment_store_id,
            ledger_store_id=composition.ledger_store_id,
            installation_id=self.binding.installation_id,
            host_id=self.binding.host_id,
            provisioner_id=self.binding.provisioner_id,
            execution_blocked_local=blocked,
        )

    def _check_ledger_locked(self, ledger, composition, state):
        ledger.assert_locked()
        if (
            ledger.directory != state / "ledger"
            or ledger.version != 2
            or ledger.store_id != composition.ledger_store_id
            or ledger.installation_id != self.binding.installation_id
            or ledger.host_id != self.binding.host_id
        ):
            raise ProvisionError("provision_root_invalid")
        self._check_plans_locked(state / "plans", ledger, composition.enrollment_store_id)
        return ledger.execution_blocked_local()

    def _check_plans_locked(self, plans, ledger, enrollment_store_id):
        """Root → ledger → journal; consulta integral, sem adoção ou recuperação."""
        if self._owner_thread != threading.get_ident():
            raise ProvisionError("provision_lock_required")
        ledger.assert_locked()
        inventory = ledger.plan_inventory()
        entries = {path.name for path in plans.iterdir()}
        for name in entries:
            if not UUID(name).int or str(UUID(name)) != name:
                raise ProvisionError("provision_root_invalid")
            private(plans / name, directory=True)
        allowed, required = set(), set()
        for plan_id, association in inventory.items():
            acquisition, run = association["acquisition"], association["run"]
            if (
                acquisition["enrollment_store_id"] != str(enrollment_store_id)
                or acquisition["issue_request_id"] != str(self.binding.issue_request_id)
                or acquisition["provisioner_id"] != str(self.binding.provisioner_id)
                or acquisition["origin"] != self.binding.origin
            ):
                raise ProvisionError("provision_root_invalid")
            if acquisition["claim_id"] is not None:
                allowed.add(plan_id)
            if run is not None:
                required.add(plan_id)
        if entries - allowed or required - entries:
            raise ProvisionError("provision_root_invalid")
        journal_ids = set()
        for plan_id in sorted(entries):
            association = inventory[plan_id]
            journal = Journal.open_read_only(plans / plan_id)
            try:
                with journal.lock():
                    if journal.journal_id in journal_ids:
                        raise ProvisionError("provision_root_invalid")
                    journal_ids.add(journal.journal_id)
                    self._check_plan_locked(plan_id, association, journal)
            finally:
                journal.close()
        private(plans, directory=True)
        if {
            path.name for path in plans.iterdir()
        } != entries or ledger.plan_inventory() != inventory:
            raise ProvisionError("provision_root_invalid")

    def _check_plan_locked(self, plan_id, association, journal):
        acquisition, run = association["acquisition"], association["run"]
        operations = journal.operations()
        claim = journal.claim
        if any(
            row["effect_request_id"] is not None and row["effect_request_id"] != row["request_id"]
            for row in operations.values()
        ):
            raise ProvisionError("provision_root_invalid")
        if (
            str(claim.plan_id) != plan_id
            or claim.installation_id != self.binding.installation_id
            or claim.host_id != self.binding.host_id
            or claim.provisioner_id != self.binding.provisioner_id
            or any(
                str(getattr(claim, name)) != acquisition[name]
                for name in ("claim_id", "plan_id", "plan_hash", "owner_id", "provisioner_id")
            )
            or claim.generation != acquisition["generation"]
            or claim.revision < acquisition["claim_revision"]
            or claim.lease_expires_at < datetime.fromisoformat(acquisition["lease_expires_at"])
            or claim.revision == acquisition["claim_revision"]
            and (
                claim.status != acquisition["claim_status"]
                or claim.lease_expires_at != datetime.fromisoformat(acquisition["lease_expires_at"])
            )
        ):
            raise ProvisionError("provision_root_invalid")
        if run is None:
            if operations or claim.status != "claimed":
                raise ProvisionError("provision_root_invalid")
            return
        if (
            any(
                str(getattr(claim, name)) != run[name]
                for name in ("claim_id", "plan_id", "plan_hash", "owner_id", "provisioner_id")
            )
            or claim.generation != run["generation"]
        ):
            raise ProvisionError("provision_root_invalid")
        if run["status"] == "stopped" and operations:
            raise ProvisionError("provision_root_invalid")
        if run["status"] == "completed":
            if (
                set(operations) != set(OPERATIONS)
                or any(row["status"] != "confirmed" for row in operations.values())
                or claim.status != "confirmed"
                or claim.revision < run["revision"]
                or claim.lease_expires_at < datetime.fromisoformat(run["lease_expires_at"])
            ):
                raise ProvisionError("provision_root_invalid")
            result = ReceiptResult.model_validate_json(operations["verify"]["result_json"])
            if (
                not result.verified
                or result.vm_id is None
                or not result.vm_id.int
                or result.cpu_count != claim.plan.cpu_count
                or result.memory_bytes != claim.plan.memory_bytes
                or result.disk_bytes != claim.plan.disk_bytes
                or result.image_iso_sha256 != claim.plan.image_iso_sha256
                or result.powered_off is not True
                or result.network_none is not True
            ):
                raise ProvisionError("provision_root_invalid")

    def check_only(self) -> RootCheck:
        with self._lock():
            return self._check_locked()
