"""Assets/HW ACL/locks próprios; nenhum root real, Hyper-V, kit copiado ou concessão."""

import ctypes
import json
import os
import subprocess
import sys
import threading
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from bees_host.provisioning import assets, root_store
from bees_host.provisioning.assets import KIT_FILES, AssetsBinding, AssetsStore, WorkspaceStore
from bees_host.provisioning.contracts import ProvisionError, canonical
from bees_host.provisioning.hardware_security import validate_hardware_file, validate_hardware_root
from bees_host.provisioning.journal import create
from bees_host.provisioning.root_store import RootCheck
from bees_host.security import check_private
from test_guest_bridge_private import allow_everyone
from test_guest_bridge_tls import private_bridge_directory


def set_hardware_acl(path, *, extra="", flags=None, trusted=True, protected=True):
    """Somente fixture descartável; não usar em código de produção/preflight."""
    if os.name != "nt":
        path.chmod(0o700 if path.is_dir() else 0o600)
        if extra or not trusted or not protected:
            path.chmod(0o777 if path.is_dir() else 0o666)
        return
    adv = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    ptr, dword = ctypes.c_void_p, ctypes.c_uint32
    kernel.GetCurrentProcess.restype = ptr
    kernel.CloseHandle.argtypes = [ptr]
    kernel.LocalFree.argtypes = [ptr]
    adv.OpenProcessToken.argtypes = [ptr, dword, ctypes.POINTER(ptr)]
    adv.GetTokenInformation.argtypes = [ptr, ctypes.c_int, ptr, dword, ctypes.POINTER(dword)]
    adv.ConvertSidToStringSidW.argtypes = [ptr, ctypes.POINTER(ptr)]
    adv.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
        ctypes.c_wchar_p,
        dword,
        ctypes.POINTER(ptr),
        ctypes.POINTER(dword),
    ]
    adv.GetSecurityDescriptorDacl.argtypes = [
        ptr,
        ctypes.POINTER(ctypes.c_int),
        ctypes.POINTER(ptr),
        ctypes.POINTER(ctypes.c_int),
    ]
    adv.SetNamedSecurityInfoW.argtypes = [ctypes.c_wchar_p, ctypes.c_int, dword, ptr, ptr, ptr, ptr]
    token, text, descriptor = ptr(), ptr(), ptr()
    try:
        assert adv.OpenProcessToken(kernel.GetCurrentProcess(), 8, ctypes.byref(token))
        needed = dword()
        adv.GetTokenInformation(token, 1, None, 0, ctypes.byref(needed))
        user = ctypes.create_string_buffer(needed.value)
        assert adv.GetTokenInformation(token, 1, user, needed, ctypes.byref(needed))
        sid = ctypes.cast(user, ctypes.POINTER(ptr))[0]
        assert adv.ConvertSidToStringSidW(sid, ctypes.byref(text))
        current = ctypes.wstring_at(text)
        actual_flags = flags if flags is not None else ("OICI" if path.is_dir() else "")
        acl = "D:" + ("P" if protected else "") + f"(A;{actual_flags};FA;;;{current})"
        if trusted:
            acl += f"(A;{actual_flags};FA;;;SY)(A;{actual_flags};FA;;;BA)"
        acl += extra
        assert adv.ConvertStringSecurityDescriptorToSecurityDescriptorW(
            acl, 1, ctypes.byref(descriptor), None
        )
        present, defaulted, dacl = ctypes.c_int(), ctypes.c_int(), ptr()
        assert adv.GetSecurityDescriptorDacl(
            descriptor, ctypes.byref(present), ctypes.byref(dacl), ctypes.byref(defaulted)
        )
        security_info = 0x80000004 if protected else 0x20000004
        assert not adv.SetNamedSecurityInfoW(str(path), 1, security_info, None, None, dacl, None)
    finally:
        if token:
            kernel.CloseHandle(token)
        if text:
            kernel.LocalFree(text)
        if descriptor:
            kernel.LocalFree(descriptor)


def prepare_workspace(binding, directory=None):
    """Fixture do futuro preparo humano: cria somente diretório/metadata descartáveis."""
    directory = directory if directory is not None else assets._roots()[1]
    assert any(parent.name.startswith(".bees-bridge-test-") for parent in directory.parents)
    directory.mkdir(mode=0o700)
    check_private(directory, directory=True, protect=True)
    create(
        directory / "identity.json", canonical(assets._identity(binding).model_dump(mode="json"))
    )
    create(directory / "owner.lock", b"0")
    for path in (directory / "identity.json", directory / "owner.lock", directory):
        set_hardware_acl(path)
    return directory


def fingerprint(directory):
    result = {".": ("directory", directory.stat().st_mtime_ns)}
    for path in directory.rglob("*"):
        if path.is_dir():
            result[str(path.relative_to(directory))] = ("directory", path.stat().st_mtime_ns)
        else:
            result[str(path.relative_to(directory))] = (path.read_bytes(), path.stat().st_mtime_ns)
    return result


@pytest.fixture
def fixture(monkeypatch):
    with private_bridge_directory() as base:
        monkeypatch.setattr(root_store, "_native_root", lambda: base / "bees-provisioner")
        checked = RootCheck(
            root_id=uuid4(),
            enrollment_store_id=uuid4(),
            ledger_store_id=uuid4(),
            installation_id=uuid4(),
            host_id=uuid4(),
            provisioner_id=uuid4(),
            execution_blocked_local=False,
        )
        binding = AssetsBinding.from_root_check(checked)
        yield base, binding


def test_initialization_only_private_assets_empty_kit_fixed_native_roots(fixture, monkeypatch):
    base, binding = fixture
    monkeypatch.setenv("LOCALAPPDATA", str(base / "foreign"))
    monkeypatch.setenv("HOME", str(base / "foreign"))
    before = set(base.iterdir())
    store = AssetsStore.initialize(binding=binding)
    assert set(base.iterdir()) - before == {base / "bees-provisioner-assets"}
    assert not (base / "bees-provisioner-hardware").exists()
    assert store.kit_directory == base / "bees-provisioner-assets" / "kit"
    assert set(store.kit_directory.iterdir()) == set()
    assert store.check_only().kit_present_local is False
    assert not hasattr(WorkspaceStore, "initialize")
    with pytest.raises(ProvisionError, match="provision_state_already_present"):
        AssetsStore.initialize(binding=binding)
    assert not (base / "foreign").exists()


def test_topology_ids_are_deterministic_and_binding_changes_not_adopted(fixture):
    base, binding = fixture
    store = AssetsStore.initialize(binding=binding)
    assert (store.assets_id, store.kit_id, store.workspace_id) == (
        binding.assets_id,
        binding.kit_id,
        binding.workspace_id,
    )
    assert len({binding.assets_id, binding.kit_id, binding.workspace_id}) == 3
    before = fingerprint(base)
    changed = binding.model_copy(update={"provisioner_id": uuid4()})
    with pytest.raises(ProvisionError, match="provision_assets_binding_invalid"):
        AssetsStore.open(binding=changed)
    changed_root = binding.model_copy(update={"root_id": uuid4()})
    assert changed_root.workspace_id != binding.workspace_id
    with pytest.raises(ProvisionError):
        AssetsStore.open(binding=changed_root)
    assert fingerprint(base) == before


def test_open_and_nested_check_only_preserve_files_mtime_and_binding(fixture):
    base, binding = fixture
    AssetsStore.initialize(binding=binding)
    before = fingerprint(base)
    store = AssetsStore.open(binding=binding)
    with store.lock() as checked:
        store.assert_locked()
        assert store.check_only() == checked
        with pytest.raises(ProvisionError, match="provision_lock_required"):
            with store.lock():
                pytest.fail("lock reentrante")
    assert fingerprint(base) == before


@pytest.mark.parametrize("missing", ["identity.json", "owner.lock", "kit"])
def test_missing_or_partial_assets_never_recreated(fixture, missing):
    base, binding = fixture
    store = AssetsStore.initialize(binding=binding)
    target = store._directory / missing
    target.rmdir() if target.is_dir() else target.unlink()
    before = fingerprint(base)
    with pytest.raises(ProvisionError):
        AssetsStore.open(binding=binding)
    assert fingerprint(base) == before


def test_assets_initialization_refuses_existing_workspace_before_new_anchor(fixture):
    base, binding = fixture
    prepare_workspace(binding, base / "bees-provisioner-hardware")
    before = fingerprint(base)
    with pytest.raises(ProvisionError, match="provision_workspace_already_present"):
        AssetsStore.initialize(binding=binding)
    assert not (base / "bees-provisioner-assets").exists() and fingerprint(base) == before


def test_kit_requires_complete_nine_file_artifact_or_empty(fixture):
    base, binding = fixture
    store = AssetsStore.initialize(binding=binding)
    create(store.kit_directory / "README.txt", b"fixture; not authenticated kit")
    with pytest.raises(ProvisionError, match="provision_assets_invalid"):
        store.check_only()
    for name in KIT_FILES - {"README.txt"}:
        create(store.kit_directory / name, b"fixture")
    before = fingerprint(base)
    assert AssetsStore.open(binding=binding).check_only().kit_present_local is True
    assert fingerprint(base) == before
    # Presença não autentica ISO/payload; a composição deve usar LocalTemplate.verify.
    create(store.kit_directory / "extra.bin", b"extra")
    with pytest.raises(ProvisionError, match="provision_assets_invalid"):
        store.check_only()


@pytest.mark.parametrize(
    "changes",
    [
        {"format": True},
        {"sealed_workspace_id": str(uuid4())},
        {"sealed_assets_id": str(uuid4())},
        {"sealed_kit_id": str(uuid4())},
        {"origin": "secret"},
        {"root_id": str(UUID(int=0))},
    ],
)
def test_closed_identity_and_topology_binding_reject_alteration(fixture, changes):
    base, binding = fixture
    store = AssetsStore.initialize(binding=binding)
    marker = store._directory / "identity.json"
    values = json.loads(marker.read_bytes()) | changes
    marker.write_bytes(canonical(values))
    before = fingerprint(base)
    with pytest.raises(ProvisionError) as error:
        AssetsStore.open(binding=binding)
    assert "secret" not in str(error.value) and str(base) not in str(error.value)
    assert fingerprint(base) == before


def test_corrupt_duplicated_nested_identity_and_foreign_files_refused(fixture):
    base, binding = fixture
    store = AssetsStore.initialize(binding=binding)
    marker = store._directory / "identity.json"
    original = marker.read_bytes()
    for data in (
        b"",
        original[:-1] + b',"format":1}',
        b'{"x":' + b"[" * 1200 + b"0" + b"]" * 1200 + b"}",
    ):
        marker.write_bytes(data)
        before = fingerprint(base)
        with pytest.raises(ProvisionError):
            store.check_only()
        assert fingerprint(base) == before
    marker.write_bytes(original)
    create(store._directory / "staged.json", b"partial")
    with pytest.raises(ProvisionError):
        store.check_only()


def test_workspace_open_missing_never_creates_and_valid_check_is_readonly(fixture):
    base, binding = fixture
    before = fingerprint(base)
    with pytest.raises(ProvisionError, match="provision_state_missing"):
        WorkspaceStore.open(binding=binding)
    assert fingerprint(base) == before
    hardware = prepare_workspace(binding, base / "bees-provisioner-hardware")
    before = fingerprint(base)
    store = WorkspaceStore.open(binding=binding)
    assert store.storage_root == hardware
    with store.lock() as checked:
        assert store.check_only() == checked and checked.plan_count == 0
        store.assert_locked()
    assert fingerprint(base) == before


def test_workspace_prefix_uuid_dirs_not_arbitrary_files_or_sealed_workspace_substitute(fixture):
    base, binding = fixture
    hardware = prepare_workspace(binding, base / "bees-provisioner-hardware")
    directory = hardware / str(uuid4())
    directory.mkdir()
    assert WorkspaceStore.open(binding=binding).check_only().plan_count == 1
    create(hardware / "extra.json", b"foreign")
    with pytest.raises(ProvisionError):
        WorkspaceStore.open(binding=binding)
    (hardware / "extra.json").unlink()
    marker = hardware / "identity.json"
    value = json.loads(marker.read_bytes())
    value["sealed_workspace_id"] = str(uuid4())
    marker.write_bytes(canonical(value))
    before = fingerprint(base)
    with pytest.raises(ProvisionError):
        WorkspaceStore.open(binding=binding)
    assert fingerprint(base) == before


@pytest.mark.parametrize("target", ["root", "marker", "lock"])
def test_hardware_guards_refuse_foreign_read_write_and_do_not_fix_acl(fixture, target):
    base, binding = fixture
    hardware = prepare_workspace(binding, base / "bees-provisioner-hardware")
    node = (
        hardware
        if target == "root"
        else hardware / ({"marker": "identity.json", "lock": "owner.lock"}[target])
    )
    allow_everyone(node)
    before = fingerprint(base)
    with pytest.raises(ProvisionError, match="provision_hardware_private_required"):
        WorkspaceStore.open(binding=binding)
    assert fingerprint(base) == before


@pytest.mark.skipif(os.name != "nt", reason="DACL Windows literal")
@pytest.mark.parametrize(
    "changes",
    [
        {"trusted": False},
        {"protected": False},
        {"flags": "OICIIO"},
        {"extra": "(A;;FR;;;WD)"},
        {"extra": "(D;;FA;;;WD)"},
    ],
)
def test_windows_acl_requires_exact_three_allow_sids_and_protection(fixture, changes):
    base, binding = fixture
    hardware = prepare_workspace(binding, base / "bees-provisioner-hardware")
    try:
        set_hardware_acl(hardware, **changes)
        with pytest.raises(ProvisionError, match="provision_hardware_private_required"):
            validate_hardware_root(hardware)
    finally:
        # Restaura somente a fixture para permitir a remoção pelo context manager.
        set_hardware_acl(hardware, flags="OICI")


def test_hardware_files_no_hardlinks_and_private_assets_no_foreign_acl(fixture):
    base, binding = fixture
    store = AssetsStore.initialize(binding=binding)
    hardware = prepare_workspace(binding, base / "bees-provisioner-hardware")
    os.link(hardware / "identity.json", hardware / "linked.json")
    with pytest.raises(ProvisionError, match="provision_hardware_private_required"):
        validate_hardware_file(hardware / "identity.json")
    allow_everyone(store.kit_directory)
    with pytest.raises(ProvisionError, match="provision_private_required"):
        AssetsStore.open(binding=binding)


def test_native_locks_between_processes_and_owner_thread(fixture):
    base, binding = fixture
    store = AssetsStore.initialize(binding=binding)
    prepare_workspace(binding, base / "bees-provisioner-hardware")
    workspace = WorkspaceStore.open(binding=binding)
    before = fingerprint(base)
    code = """
import sys,json
from pathlib import Path
from bees_host.provisioning import root_store
from bees_host.provisioning.assets import AssetsBinding,AssetsStore,WorkspaceStore
from bees_host.provisioning.contracts import ProvisionError
root_store._native_root=lambda:Path(sys.argv[1])/'bees-provisioner'
binding=AssetsBinding.model_validate_json(sys.stdin.read())
try:
 (AssetsStore if sys.argv[2]=='assets' else WorkspaceStore).open(binding=binding)
except ProvisionError as error:sys.exit(0 if str(error)=='provision_owner_running' else 10)
sys.exit(9)
"""
    for kind, target in (("assets", store), ("workspace", workspace)):
        with target.lock():
            process = subprocess.run(
                [sys.executable, "-c", code, str(base), kind],
                input=binding.model_dump_json(),
                text=True,
                capture_output=True,
                timeout=10,
                check=False,
            )
            assert process.returncode == 0 and not process.stdout and not process.stderr
            failures = []

            def foreign(current=target, caught=failures):
                try:
                    current.assert_locked()
                except BaseException as error:
                    caught.append(error)

            thread = threading.Thread(target=foreign)
            thread.start()
            thread.join(timeout=5)
            assert not thread.is_alive() and len(failures) == 1
            assert (
                type(failures[0]) is ProvisionError
                and str(failures[0]) == "provision_lock_required"
            )
            target.assert_locked()
    assert fingerprint(base) == before


def test_initialization_failure_preserves_anchor_and_refuses_retry(fixture, monkeypatch):
    base, binding = fixture
    original = assets.create

    def fail(path, data):
        if path.name == "owner.lock":
            raise OSError("private fixture failure")
        return original(path, data)

    monkeypatch.setattr(assets, "create", fail)
    with pytest.raises(ProvisionError):
        AssetsStore.initialize(binding=binding)
    assert (base / "bees-provisioner-assets" / "identity.json").exists()
    before = fingerprint(base)
    with pytest.raises(ProvisionError, match="provision_state_already_present"):
        AssetsStore.initialize(binding=binding)
    with pytest.raises(ProvisionError):
        AssetsStore.open(binding=binding)
    assert fingerprint(base) == before


@pytest.mark.parametrize("value", [None, {}, RootCheck])
def test_binding_only_accepts_closed_root_check_and_nonzero_ids(fixture, value):
    base, binding = fixture
    with pytest.raises(ProvisionError, match="provision_assets_binding_invalid"):
        AssetsBinding.from_root_check(value)
    with pytest.raises(ProvisionError, match="provision_assets_binding_invalid"):
        AssetsStore.initialize(binding=binding.model_copy(update={"host_id": UUID(int=0)}))
    assert not (base / "bees-provisioner-assets").exists()


def test_workspace_full_inventory_requires_lock_and_retains_topology_only(fixture):
    base, binding = fixture
    hardware = prepare_workspace(binding, base / "bees-provisioner-hardware")
    identifiers = {uuid4() for _ in range(103)}
    for identifier in identifiers:
        (hardware / str(identifier)).mkdir()
    store = WorkspaceStore.open(binding=binding)
    before = fingerprint(base)
    with pytest.raises(ProvisionError, match="provision_lock_required"):
        store.plan_ids()
    with store.lock():
        assert store.plan_ids() == identifiers
        assert store.check_only().plan_count == len(identifiers)
    assert fingerprint(base) == before


@pytest.mark.parametrize("name", ["not-a-plan", str(UUID(int=0)), str(uuid4()).upper()])
def test_workspace_refuses_noncanonical_plan_directories_without_adoption(fixture, name):
    base, binding = fixture
    hardware = prepare_workspace(binding)
    (hardware / name).mkdir()
    before = fingerprint(base)
    with pytest.raises(ProvisionError):
        WorkspaceStore.open(binding=binding)
    assert fingerprint(base) == before


def test_locked_revalidation_refuses_workspace_marker_corruption_without_repair(fixture):
    base, binding = fixture
    hardware = prepare_workspace(binding)
    store = WorkspaceStore.open(binding=binding)
    with store.lock():
        marker = hardware / "identity.json"
        original = marker.read_bytes()
        marker.write_bytes(b'{"x":' + b"[" * 1200 + b"0" + b"]" * 1200 + b"}")
        before = (marker.read_bytes(), marker.stat().st_mtime_ns)
        with pytest.raises(ProvisionError):
            store.check_only()
        assert (marker.read_bytes(), marker.stat().st_mtime_ns) == before
        marker.write_bytes(original)


def test_lock_exit_rechecks_assets_partial_state_and_preserves_evidence(fixture):
    base, binding = fixture
    store = AssetsStore.initialize(binding=binding)
    with pytest.raises(ProvisionError, match="provision_assets_invalid"):
        with store.lock():
            create(store._directory / "staged.json", b"unfinished")
    before = fingerprint(base)
    with pytest.raises(ProvisionError):
        AssetsStore.open(binding=binding)
    assert fingerprint(base) == before


def test_hardware_guard_refuses_relative_paths_before_native_acl():
    with pytest.raises(ProvisionError, match="provision_hardware_private_required"):
        validate_hardware_root(Path("hardware"))


def test_reparse_ancestor_refused_before_creating_assets_or_hardware_acl(fixture, monkeypatch):
    base, binding = fixture
    original = Path.lstat

    def reparse(path):
        if path == base:
            return SimpleNamespace(st_file_attributes=0x400)
        return original(path)

    monkeypatch.setattr(Path, "lstat", reparse)
    with pytest.raises(ProvisionError, match="provision_assets_invalid"):
        AssetsStore.initialize(binding=binding)
    with pytest.raises(ProvisionError, match="provision_hardware_private_required"):
        validate_hardware_root(base / "bees-provisioner-hardware")
    assert not (base / "bees-provisioner-assets").exists()
