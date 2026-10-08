"""Raiz fixa descartável: ACL/cifra/locks e falhas, sem DNS ou hardware."""

import inspect
import json
import os
import shutil
import socket
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from bees_host.provisioning import root_store as module
from bees_host.provisioning.contracts import ProvisionError, canonical
from bees_host.provisioning.enrollment import EnrollmentBinding, EnrollmentStore
from bees_host.provisioning.journal import create
from bees_host.provisioning.root_store import RootStore
from bees_host.provisioning.supervisor_store import SupervisorStore
from bees_host.security import check_private
from test_guest_bridge_private import allow_everyone
from test_guest_bridge_tls import private_bridge_directory
from test_provisioning_enrollment import SECRET, bootstrap_value, cipher_fixture


@pytest.fixture
def setup(monkeypatch):
    with private_bridge_directory() as base:
        root = base / "bees-provisioner"
        monkeypatch.setattr(module, "_native_root", lambda: root)
        binding = EnrollmentBinding(
            origin="http://localhost:8080",
            installation_id=uuid4(),
            host_id=uuid4(),
            provisioner_id=uuid4(),
            issue_request_id=uuid4(),
        )
        bootstrap = base / "bootstrap.json"
        create(bootstrap, canonical(bootstrap_value(binding, datetime.now(UTC))))
        yield root, binding, bootstrap, cipher_fixture()


def initialize(setup):
    _, binding, bootstrap, cipher = setup
    return RootStore.initialize(binding=binding, bootstrap=bootstrap, cipher=cipher)


def reopen(setup):
    _, binding, _, cipher = setup
    return RootStore.open(binding=binding, cipher=cipher)


def snapshot(root):
    return {
        str(path.relative_to(root)): (
            path.read_bytes() if path.is_file() else None,
            path.stat().st_mtime_ns,
        )
        for path in (root, *root.rglob("*"))
    }


def replace_json(path, value):
    path.write_bytes(canonical(value))


def test_native_root_smoke_ignores_environment_and_does_not_create(monkeypatch):
    monkeypatch.setenv("HOME", "invalid-home")
    monkeypatch.setenv("LOCALAPPDATA", "invalid-localappdata")
    monkeypatch.setenv("XDG_STATE_HOME", "invalid-xdg")
    root = module._native_root()
    before = root.exists()
    assert root.is_absolute() and root.name == "bees-provisioner"
    assert "invalid-" not in str(root)
    assert module._native_root() == root and root.exists() == before


def test_public_api_has_no_root_path_or_automatic_initialization():
    for method in (RootStore.initialize, RootStore.open, RootStore.check_only, RootStore.lock):
        assert not {"root", "directory", "path"} & set(inspect.signature(method).parameters)
    with pytest.raises(ProvisionError, match="provision_root_explicit_required"):
        RootStore()


@pytest.mark.parametrize("kind", ["expired", "far_future", "binding", "invalid"])
def test_bad_bootstrap_cannot_create_anchor(setup, kind):
    root, binding, bootstrap, _ = setup
    value = bootstrap_value(binding, datetime.now(UTC))
    if kind == "expired":
        value["expires_at"] = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
    elif kind == "far_future":
        value["expires_at"] = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    elif kind == "binding":
        value["issue_request_id"] = str(uuid4())
    else:
        value = {"invalid": True}
    bootstrap.write_bytes(canonical(value))
    before = bootstrap.read_bytes()
    with pytest.raises(ProvisionError):
        initialize(setup)
    assert not root.exists() and bootstrap.read_bytes() == before


@pytest.mark.skipif(os.name != "nt", reason="Sintaxe de caminhos Windows")
@pytest.mark.parametrize("path", [r"\\server\share\bees-provisioner", r"\\?\C:\bees-provisioner"])
def test_unc_and_device_roots_refused_before_filesystem(setup, monkeypatch, path):
    _, binding, _, cipher = setup
    monkeypatch.setattr(module, "_native_root", lambda: Path(path))

    def forbidden(*args, **kwargs):
        pytest.fail("Raiz UNC/device tentou filesystem")

    monkeypatch.setattr(module, "check_ancestors", forbidden)
    monkeypatch.setattr(Path, "exists", forbidden)
    with pytest.raises(ProvisionError, match="provision_root_invalid"):
        RootStore.open(binding=binding, cipher=cipher)


@pytest.mark.skipif(os.name != "nt", reason="Tipos de volume Windows")
@pytest.mark.parametrize("drive_type", [0, 1, 2, 4, 5, 6])
def test_non_fixed_volume_refused_before_filesystem(setup, monkeypatch, drive_type):
    _, binding, _, cipher = setup

    class Kernel:
        GetDriveTypeW = staticmethod(lambda path: drive_type)

    monkeypatch.setattr(module.ctypes, "WinDLL", lambda *args, **kwargs: Kernel())

    def forbidden(*args, **kwargs):
        pytest.fail("Volume não fixo tentou filesystem")

    monkeypatch.setattr(module, "check_ancestors", forbidden)
    monkeypatch.setattr(Path, "exists", forbidden)
    with pytest.raises(ProvisionError, match="provision_root_local_required"):
        RootStore.open(binding=binding, cipher=cipher)


def test_unsafe_ancestor_failure_has_provision_error_before_anchor(setup, monkeypatch):
    root, _, bootstrap, _ = setup
    original = bootstrap.read_bytes()

    def fail(*args, **kwargs):
        raise module.BridgeError("bridge_identity_private_required")

    monkeypatch.setattr(module, "check_ancestors", fail)
    with pytest.raises(ProvisionError, match="provision_root_unavailable"):
        initialize(setup)
    assert not root.exists() and bootstrap.read_bytes() == original


def test_full_composition_and_check_only_are_private_and_immutable(setup, monkeypatch, caplog):
    root, binding, bootstrap, cipher = setup
    store = initialize(setup)
    assert not bootstrap.exists()
    assert {p.name for p in root.iterdir()} == module.FINAL_FILES
    assert {p.name for p in (root / "state").iterdir()} == module.STATE_FILES
    before = snapshot(root)

    def forbidden(*args, **kwargs):
        pytest.fail("Consulta offline tentou criar estado, rede ou processo")

    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(EnrollmentStore, "initialize", forbidden)
    monkeypatch.setattr(SupervisorStore, "initialize_for_acquisition", forbidden)
    monkeypatch.setattr(SupervisorStore, "open", forbidden)
    check = store.check_only()
    assert check == RootStore.open(binding=binding, cipher=cipher).check_only()
    assert check.configured_local and not check.execution_blocked_local
    value = check.model_dump(mode="json")
    assert set(value) == {
        "root_id",
        "enrollment_store_id",
        "ledger_store_id",
        "installation_id",
        "host_id",
        "provisioner_id",
        "configured_local",
        "execution_blocked_local",
    }
    assert snapshot(root) == before
    assert SECRET not in repr(check) + caplog.text
    assert all(SECRET.encode() not in bytes_ for bytes_, _ in before.values() if bytes_)


def test_missing_whole_root_is_never_created_by_open(setup):
    root, _, bootstrap, _ = setup
    original = bootstrap.read_bytes()
    with pytest.raises(ProvisionError):
        reopen(setup)
    assert not root.exists() and bootstrap.read_bytes() == original


@pytest.mark.parametrize(
    "relative",
    [
        "identity.json",
        "owner.lock",
        "composition.json",
        "state",
        "state/enrollment",
        "state/ledger",
        "state/plans",
        "state/enrollment/credentials.bin",
        "state/enrollment/identity.json",
        "state/ledger/identity.json",
        "state/ledger/supervisor.sqlite3",
    ],
)
def test_missing_anchored_component_refuses_repair_or_reinitialize(setup, relative):
    root, _, _, _ = setup
    initialize(setup)
    target = root / relative
    if target.is_dir():
        shutil.rmtree(target)
    else:
        target.unlink()
    before = snapshot(root)
    with pytest.raises(ProvisionError):
        reopen(setup)
    with pytest.raises(ProvisionError, match="provision_state_already_present"):
        initialize(setup)
    assert snapshot(root) == before


@pytest.mark.parametrize(
    "relative", ["", "state", "state/enrollment", "state/ledger", "state/plans"]
)
def test_unexpected_entries_refused_without_cleanup(setup, relative):
    root, _, _, _ = setup
    initialize(setup)
    create(root / relative / "unexpected", b"evidence")
    before = snapshot(root)
    with pytest.raises(ProvisionError):
        reopen(setup)
    assert snapshot(root) == before


@pytest.mark.parametrize(
    "field", ["origin", "installation_id", "host_id", "provisioner_id", "issue_request_id"]
)
def test_binding_change_refused_without_mutating(setup, field):
    root, binding, _, cipher = setup
    initialize(setup)
    values = binding.model_dump()
    values[field] = "https://localhost:8081" if field == "origin" else uuid4()
    before = snapshot(root)
    with pytest.raises(ProvisionError):
        RootStore.open(binding=EnrollmentBinding(**values), cipher=cipher)
    assert snapshot(root) == before


@pytest.mark.parametrize(
    "file,field",
    [
        ("identity.json", "root_id"),
        ("composition.json", "root_id"),
        ("composition.json", "enrollment_store_id"),
        ("composition.json", "ledger_store_id"),
        ("state/enrollment/identity.json", "store_id"),
        ("state/ledger/identity.json", "store_id"),
    ],
)
def test_marker_id_changes_refused(setup, file, field):
    root, _, _, _ = setup
    store = initialize(setup)
    path = root / file
    value = json.loads(path.read_bytes())
    value[field] = str(uuid4())
    replace_json(path, value)
    before = snapshot(root)
    with pytest.raises(ProvisionError):
        store.check_only()
    with pytest.raises(ProvisionError):
        reopen(setup)
    assert snapshot(root) == before


@pytest.mark.parametrize(
    "data", [b"{}", b'{"format":1,"format":1}', b'{"format":NaN}', b"x" * 8193]
)
def test_anchor_json_closed_and_bounded(setup, data):
    root, _, _, _ = setup
    initialize(setup)
    (root / "composition.json").write_bytes(data)
    with pytest.raises(ProvisionError):
        reopen(setup)


def test_replacement_of_entire_state_cannot_redefine_child_ids(setup):
    root, binding, bootstrap, cipher = setup
    initialize(setup)
    sealed = (root / "composition.json").read_bytes()
    shutil.rmtree(root / "state")
    state = root / "state"
    state.mkdir(mode=0o700)
    check_private(state, directory=True, protect=True)
    create(bootstrap, canonical(bootstrap_value(binding, datetime.now(UTC))))
    EnrollmentStore.initialize(state / "enrollment", bootstrap, binding=binding, cipher=cipher)
    ledger = SupervisorStore.initialize_for_acquisition(
        state / "ledger", installation_id=binding.installation_id, host_id=binding.host_id
    )
    ledger.close()
    (state / "plans").mkdir(mode=0o700)
    check_private(state / "plans", directory=True, protect=True)
    with pytest.raises(ProvisionError):
        reopen(setup)
    assert (root / "composition.json").read_bytes() == sealed


def test_different_cipher_type_refused_even_with_no_root(setup):
    root, binding, _, _ = setup

    class Plaintext:
        def encrypt(self, value):
            return value

        def decrypt(self, value):
            return value

    with pytest.raises(ProvisionError, match="provision_enrollment_protection_required"):
        RootStore.open(binding=binding, cipher=Plaintext())
    assert not root.exists()


def test_hardlink_anchor_refused(setup):
    root, _, _, _ = setup
    initialize(setup)
    os.link(root / "composition.json", root.parent / "linked.json")
    with pytest.raises(ProvisionError):
        reopen(setup)


def test_writable_foreign_acl_or_mode_refused(setup):
    root, _, _, _ = setup
    initialize(setup)
    path = root / "composition.json"
    if os.name == "nt":
        allow_everyone(path)
    else:
        path.chmod(0o666)
    with pytest.raises(ProvisionError):
        reopen(setup)


@pytest.mark.parametrize("relative", ["state", "state/plans", "composition.json"])
def test_symlink_components_refused(setup, relative):
    root, _, _, _ = setup
    initialize(setup)
    path = root / relative
    backup = root.parent / "backup"
    path.rename(backup)
    try:
        path.symlink_to(backup, target_is_directory=backup.is_dir())
    except OSError as error:
        if os.name == "nt" and error.winerror == 1314:
            pytest.skip("Conta sem privilégio para symlink Windows")
        raise
    with pytest.raises(ProvisionError):
        reopen(setup)


def test_failed_child_initialization_preserves_partial_anchor(setup, monkeypatch):
    root, _, bootstrap, _ = setup
    original = bootstrap.read_bytes()

    def fail(*args, **kwargs):
        raise OSError("controlled failure")

    monkeypatch.setattr(EnrollmentStore, "initialize", fail)
    with pytest.raises(ProvisionError, match="provision_root_invalid"):
        initialize(setup)
    assert {p.name for p in root.iterdir()} == {"identity.json", "owner.lock", "state"}
    assert bootstrap.read_bytes() == original
    before = snapshot(root)
    with pytest.raises(ProvisionError):
        reopen(setup)
    with pytest.raises(ProvisionError, match="provision_state_already_present"):
        initialize(setup)
    assert snapshot(root) == before


def test_reentrant_cross_thread_and_second_instance_refused(setup):
    store = initialize(setup)
    other = reopen(setup)
    with store.lock():
        with pytest.raises(ProvisionError, match="provision_lock_required"):
            store.check_only()
        with pytest.raises(ProvisionError, match="provision_owner_running"):
            other.check_only()
        with ThreadPoolExecutor(max_workers=1) as pool:
            with pytest.raises(ProvisionError, match="provision_lock_required"):
                pool.submit(store._check_locked).result()
            with pytest.raises(ProvisionError, match="provision_lock_required"):
                pool.submit(store.check_only).result()
    assert store.check_only() == other.check_only()


def test_prepared_acquisition_is_reported_as_blocked_without_adoption(setup):
    root, binding, _, _ = setup
    store = initialize(setup)
    enrollment_id = store.check_only().enrollment_store_id
    ledger = SupervisorStore.open(root / "state/ledger")
    try:
        with ledger.lock():
            ledger.prepare_acquisition(
                enrollment_store_id=enrollment_id,
                issue_request_id=binding.issue_request_id,
                provisioner_id=binding.provisioner_id,
                origin=binding.origin,
                plan_id=uuid4(),
                plan_hash="a" * 64,
            )
    finally:
        ledger.close()
    before = snapshot(root)
    check = reopen(setup).check_only()
    assert check.configured_local and check.execution_blocked_local
    assert snapshot(root) == before


def child_script(root, binding):
    return (
        "import os,sys; from pathlib import Path; "
        "from bees_host.provisioning import root_store as m; "
        "from bees_host.provisioning.enrollment import EnrollmentBinding; "
        f"m._native_root=lambda:Path({str(root)!r}); "
        f"binding=EnrollmentBinding.model_validate_json({binding.model_dump_json()!r}); "
    )


def child_cipher_script(cipher):
    if os.name == "nt":
        return "from bees_host.security import DPAPICipher; cipher=DPAPICipher(); "
    import base64

    # Cifra e chave exclusivas da fixture; nunca credenciais reais do usuário.
    key = cipher.cipher._signing_key + cipher.cipher._encryption_key
    return (
        "from bees_host.security import FernetCipher; "
        f"cipher=FernetCipher({base64.urlsafe_b64encode(key).decode()!r}); "
    )


@pytest.mark.parametrize("after", ["identity.json", "owner.lock", "composition.json"])
def test_abrupt_initialize_crash_preserves_anchor_and_refuses_retry(setup, after):
    root, binding, bootstrap, cipher = setup
    code = (
        child_script(root, binding)
        + child_cipher_script(cipher)
        + (
            "original=m.create\n"
            "def fail(path,data):\n"
            " original(path,data)\n"
            f" if path.name=={after!r}: os._exit(71)\n"
            "m.create=fail\n"
            f"m.RootStore.initialize(binding=binding,bootstrap=Path({str(bootstrap)!r}),cipher=cipher)\n"
        )
    )
    child = subprocess.run([sys.executable, "-c", code], capture_output=True, timeout=30)
    assert child.returncode == 71, child.stderr.decode()
    assert (root / "identity.json").exists()
    before = snapshot(root)
    # A composition íntegra é o último selo; crash após seu fsync é final válido.
    if after == "composition.json":
        assert reopen(setup).check_only().configured_local
    else:
        with pytest.raises(ProvisionError):
            reopen(setup)
    with pytest.raises(ProvisionError, match="provision_state_already_present"):
        initialize(setup)
    assert snapshot(root) == before


def test_abrupt_crash_after_bootstrap_consumption_keeps_partial_state_blocked(setup):
    root, binding, bootstrap, cipher = setup
    code = (
        child_script(root, binding)
        + child_cipher_script(cipher)
        + (
            "original=m.EnrollmentStore.initialize\n"
            "def fail(*args,**kwargs):\n"
            " original(*args,**kwargs)\n"
            " os._exit(72)\n"
            "m.EnrollmentStore.initialize=fail\n"
            f"m.RootStore.initialize(binding=binding,bootstrap=Path({str(bootstrap)!r}),cipher=cipher)\n"
        )
    )
    child = subprocess.run([sys.executable, "-c", code], capture_output=True, timeout=30)
    assert child.returncode == 72, child.stderr.decode()
    assert not bootstrap.exists() and (root / "state/enrollment/credentials.bin").exists()
    assert not (root / "composition.json").exists()
    before = snapshot(root)
    with pytest.raises(ProvisionError):
        reopen(setup)
    with pytest.raises(ProvisionError, match="provision_state_already_present"):
        initialize(setup)
    assert snapshot(root) == before


def test_concurrent_initializer_cannot_replace_anchor_or_consume_bootstrap(setup, monkeypatch):
    root, binding, bootstrap, cipher = setup
    entered, proceed = threading.Event(), threading.Event()
    original_directory = module._directory

    def pause(path):
        original_directory(path)
        if path == root:
            entered.set()
            assert proceed.wait(15)

    monkeypatch.setattr(module, "_directory", pause)
    code = (
        child_script(root, binding)
        + child_cipher_script(cipher)
        + (
            "from bees_host.provisioning.contracts import ProvisionError\n"
            "try:\n"
            f" bootstrap=Path({str(bootstrap)!r})\n"
            " m.RootStore.initialize(binding=binding,bootstrap=bootstrap,cipher=cipher)\n"
            "except ProvisionError as error:\n"
            " sys.exit(74 if str(error)=='provision_state_already_present' else 75)\n"
        )
    )
    with ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(initialize, setup)
        try:
            assert entered.wait(15)
            original_bootstrap = bootstrap.read_bytes()
            child = subprocess.run([sys.executable, "-c", code], capture_output=True, timeout=15)
            assert child.returncode == 74, child.stderr.decode()
            assert bootstrap.read_bytes() == original_bootstrap
        finally:
            proceed.set()
        store = first.result()
    assert not bootstrap.exists() and store.check_only().configured_local


def test_process_lock_is_exclusive_and_released_after_exit(setup):
    root, binding, _, _ = setup
    store = initialize(setup)
    code = child_script(root, binding) + (
        "from bees_host.provisioning.enrollment import _exclusive\n"
        "try:\n"
        " with _exclusive(m._native_root()): print('acquired')\n"
        "except Exception: sys.exit(73)\n"
    )
    with store.lock():
        child = subprocess.run([sys.executable, "-c", code], capture_output=True, timeout=15)
        assert child.returncode == 73
    child = subprocess.run([sys.executable, "-c", code], capture_output=True, timeout=15)
    assert child.returncode == 0 and child.stdout.strip() == b"acquired"
