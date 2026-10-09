"""Composição fixa/ACL/locks reais em fixtures; hardware, kit e transporte falsos."""

import inspect
import shutil
import socket
import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from uuid import uuid4

import pytest
from bees_host.provisioning import acquisition as acquisition_module
from bees_host.provisioning import runtime as module
from bees_host.provisioning.acquisition import Acquisition
from bees_host.provisioning.assets import KIT_FILES, AssetsBinding, AssetsStore, WorkspaceStore
from bees_host.provisioning.contracts import OPERATIONS, ProvisionError
from bees_host.provisioning.journal import create
from bees_host.provisioning.root_store import RootStore
from bees_host.provisioning.runtime import ProvisionerRuntime
from bees_host.provisioning.supervisor_store import SupervisorStore
from test_provisioning_assets import prepare_workspace, set_hardware_acl
from test_provisioning_root_plans import RootAuthority, coherent_claim
from test_provisioning_root_store import child_cipher_script, child_script, initialize, snapshot
from test_provisioning_root_store import setup as root_setup  # noqa: F401
from test_provisioning_supervisor import Backend


@pytest.fixture
def base(request):
    setup = request.getfixturevalue("root_setup")
    root = initialize(setup)
    return setup, root


def configured(base):
    setup, root = base
    _, binding, _, cipher = setup
    assets = ProvisionerRuntime.initialize_assets(binding=binding, cipher=cipher)
    for name in KIT_FILES:
        create(assets.kit_directory / name, b"explicit fixture bytes")
    resource_binding = AssetsBinding.from_root_check(root.check_only())
    workspace = prepare_workspace(resource_binding)
    runtime = ProvisionerRuntime.open(binding=binding, cipher=cipher)
    return runtime, assets, workspace


def rows(root, table):
    connection = sqlite3.connect(
        (root / "state/ledger/supervisor.sqlite3").as_uri() + "?mode=ro", uri=True
    )
    connection.row_factory = sqlite3.Row
    try:
        return [dict(row) for row in connection.execute(f"SELECT * FROM {table}")]
    finally:
        connection.close()


def remove_fixture_resource(root, path):
    """Só apaga siblings fixos dentro da base privada exclusiva deste teste."""
    base = root.parent.resolve()
    target = path.resolve()
    assert base.name.startswith(".bees-bridge-test-") and root.name == "bees-provisioner"
    assert path in {
        root.with_name("bees-provisioner-assets"),
        root.with_name("bees-provisioner-hardware"),
    }
    assert target.is_relative_to(base) and target.parent == base
    assert target.name == path.name
    shutil.rmtree(target)


class Transport(RootAuthority):
    def __init__(self, claim, root):
        super().__init__(claim)
        self.calls, self.root = [], root
        self.lost = False
        self.before_return = None
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.closed = True

    def claim(self, plan_id, plan_hash, owner_id, request_id):
        acquired = rows(self.root, "acquisitions")
        assert len(acquired) == 1 and acquired[0]["status"] == "prepared"
        assert acquired[0]["request_id"] == str(request_id)
        assert acquired[0]["owner_id"] == str(owner_id)
        assert acquired[0]["plan_hash"] == plan_hash and acquired[0]["plan_id"] == str(plan_id)
        self.calls.append(request_id)
        self.claim_value = self.claim_value.model_copy(update={"owner_id": owner_id})
        if self.lost:
            raise OSError("controlled lost response")
        if self.before_return:
            self.before_return()
        return self.claim_value

    # Authority usa .claim como estado; este adaptador preserva seu método HTTP.
    def assert_current(self, claim):
        if self.revoked:
            raise ProvisionError("provision_claim_stale")
        return self.claim_value

    def begin_dispatch(self, claim, operation, request_id):
        self.claim = self.claim_value
        try:
            return super().begin_dispatch(claim, operation, request_id)
        finally:
            self.claim_value = self.claim
            del self.claim

    def record_receipt(self, claim, effect_id, request_id, result):
        self.claim = self.claim_value
        try:
            return super().record_receipt(claim, effect_id, request_id, result)
        finally:
            self.claim_value = self.claim
            del self.claim

    def renew(self, claim, request_id):
        self.claim = self.claim_value
        try:
            return super().renew(claim, request_id)
        finally:
            self.claim_value = self.claim
            del self.claim

    def mark_unknown(self, claim, effect_id, request_id):
        self.claim = self.claim_value
        try:
            return super().mark_unknown(claim, effect_id, request_id)
        finally:
            self.claim_value = self.claim
            del self.claim


@pytest.fixture
def fixture(base, monkeypatch):
    setup, root_store = base
    root, binding, _, _ = setup
    runtime, assets, workspace = configured(base)
    claim = coherent_claim(binding)
    transport = Transport(claim, root)
    transport.claim_value = transport.claim
    del transport.claim
    backend = Backend(claim)
    factories, preflights, verifies = [], [], []
    original_preflight = backend.preflight

    def backend_factory(path):
        assert path == workspace
        return backend

    def preflight():
        preflights.append(True)
        return original_preflight()

    backend.preflight = preflight

    class Template:
        def __init__(self, path):
            assert path == assets.kit_directory

        def verify(self, plan):
            verifies.append(plan.plan_id)
            return assets.kit_directory / "debian-13.7.0-amd64-netinst.iso"

    def factory(*args, **kwargs):
        assert rows(root, "acquisitions")[0]["status"] == "prepared"
        factories.append(True)
        return transport

    monkeypatch.setattr(module, "HyperVBackend", backend_factory)
    monkeypatch.setattr(module, "LocalTemplate", Template)
    monkeypatch.setattr(acquisition_module, "HTTPAuthority", factory)
    return (
        setup,
        root_store,
        runtime,
        assets,
        workspace,
        claim.plan,
        transport,
        backend,
        (factories, preflights, verifies),
    )


def test_closed_runtime_api_has_no_paths_or_executor_injection():
    for method in (
        ProvisionerRuntime.open,
        ProvisionerRuntime.initialize_assets,
        ProvisionerRuntime.run,
    ):
        assert not {"root", "path", "directory", "backend", "template", "authority"} & set(
            inspect.signature(method).parameters
        )
    with pytest.raises(ProvisionError, match="provision_runtime_explicit_required"):
        ProvisionerRuntime()


def test_open_missing_assets_never_initializes_or_connects(base, monkeypatch):
    setup, _ = base
    root, binding, _, cipher = setup
    before = snapshot(root)

    def forbidden(*args, **kwargs):
        pytest.fail("open tentou inicializar, executar ou rede")

    for target, name in (
        (AssetsStore, "initialize"),
        (module, "HyperVBackend"),
        (module, "LocalTemplate"),
        (socket, "getaddrinfo"),
        (subprocess, "Popen"),
    ):
        monkeypatch.setattr(target, name, forbidden)
    with pytest.raises(ProvisionError):
        ProvisionerRuntime.open(binding=binding, cipher=cipher)
    assert snapshot(root) == before and not root.with_name("bees-provisioner-assets").exists()


def test_explicit_assets_prepare_is_separate_and_does_not_create_hardware(base):
    setup, _ = base
    root, binding, _, cipher = setup
    before = snapshot(root)
    assets = ProvisionerRuntime.initialize_assets(binding=binding, cipher=cipher)
    assert not assets.check_only().kit_present_local
    assert not root.with_name("bees-provisioner-hardware").exists()
    assert snapshot(root) == before
    with pytest.raises(ProvisionError):
        ProvisionerRuntime.open(binding=binding, cipher=cipher)


def test_single_handoff_holds_all_native_locks_through_command_close(fixture):
    setup, root_store, runtime, assets, _, plan, transport, backend, counters = fixture
    root, binding, _, cipher = setup
    resource_binding = AssetsBinding.from_root_check(root_store.check_only())
    proofs = []

    def held():
        proofs.append(True)
        for factory in (
            lambda: RootStore.open(binding=binding, cipher=cipher),
            lambda: AssetsStore.open(binding=resource_binding),
            lambda: WorkspaceStore.open(binding=resource_binding),
        ):
            with pytest.raises(ProvisionError, match="owner_running"):
                factory()
        with pytest.raises(ProvisionError, match="owner_running"):
            with SupervisorStore.open_execution_locked(root / "state/ledger"):
                pytest.fail("Outro dono do ledger entrou")

    transport.before_return = held
    backend.on_ready = held
    backend.before_finish = held
    original_start = backend.start

    def start(*args):
        command = original_start(*args)
        original_close = command.close

        def close():
            held()
            original_close()

        command.close = close
        return command

    backend.start = start
    assert runtime.run(plan).verified
    assert len(proofs) >= 13 and backend.effects == list(OPERATIONS)
    assert transport.closed and not backend.alive
    assert counters[0] == [True] and len(transport.calls) == 1
    assert not root_store.check_only().execution_blocked_local
    preflights_before_replay = len(counters[1])
    with pytest.raises(ProvisionError):
        runtime.run(plan)
    assert len(transport.calls) == 1 and backend.effects == list(OPERATIONS)
    assert len(counters[1]) == preflights_before_replay


def test_open_is_readonly_even_with_complete_resources(fixture, monkeypatch):
    setup, _, _, _, workspace, _, _, _, _ = fixture
    root, binding, _, cipher = setup
    before = (
        snapshot(root),
        snapshot(root.with_name("bees-provisioner-assets")),
        snapshot(workspace),
    )

    def forbidden(*args, **kwargs):
        pytest.fail("open tentou preflight, transporte ou geração")

    for target, name in (
        (module, "HyperVBackend"),
        (module, "LocalTemplate"),
        (Acquisition, "_run_locked"),
        (socket, "getaddrinfo"),
        (subprocess, "Popen"),
    ):
        monkeypatch.setattr(target, name, forbidden)
    ProvisionerRuntime.open(binding=binding, cipher=cipher)
    assert before == (
        snapshot(root),
        snapshot(root.with_name("bees-provisioner-assets")),
        snapshot(workspace),
    )


@pytest.mark.parametrize("fault", ["cancel", "wrong_host", "invalid_plan", "invalid_cancel"])
def test_bad_request_is_refused_before_preflight_network_or_claim(fixture, fault):
    setup, _, runtime, _, _, plan, transport, backend, counters = fixture
    root, _, _, _ = setup
    cancelled = None
    if fault == "cancel":
        cancelled = Event()
        cancelled.set()
    elif fault == "wrong_host":
        plan = plan.model_copy(update={"host_id": uuid4()})
    elif fault == "invalid_plan":
        plan = plan.model_dump()
    else:
        cancelled = object()
    with pytest.raises(ProvisionError):
        runtime.run(plan, cancelled=cancelled)
    assert not transport.calls and not backend.effects and not any(counters)
    assert not rows(root, "acquisitions")


@pytest.mark.parametrize("missing", ["assets", "workspace", "kit"])
def test_resource_loss_after_open_refused_before_preflight(fixture, missing):
    setup, _, runtime, assets, workspace, plan, transport, backend, counters = fixture
    root, _, _, _ = setup
    if missing == "assets":
        remove_fixture_resource(root, root.with_name("bees-provisioner-assets"))
    elif missing == "workspace":
        remove_fixture_resource(root, workspace)
    else:
        for file in assets.kit_directory.iterdir():
            file.unlink()
    with pytest.raises(ProvisionError):
        runtime.run(plan)
    assert not any(counters) and not transport.calls and not backend.effects


def test_lost_claim_response_is_quarantined_and_reopen_does_not_adopt(fixture):
    setup, root_store, runtime, _, _, plan, transport, backend, _ = fixture
    _, binding, _, cipher = setup
    transport.lost = True
    with pytest.raises(ProvisionError, match="acquisition_unknown"):
        runtime.run(plan)
    assert root_store.check_only().execution_blocked_local
    replacement = ProvisionerRuntime.open(binding=binding, cipher=cipher)
    with pytest.raises(ProvisionError, match="reconciliation_required"):
        replacement.run(plan)
    assert len(transport.calls) == 1 and not backend.effects


def test_cancel_after_effect_preserves_unknown_and_closes_child_under_locks(fixture):
    _, root_store, runtime, _, _, plan, transport, backend, _ = fixture
    cancelled = Event()
    backend.before_finish = cancelled.set
    with pytest.raises(ProvisionError):
        runtime.run(plan, cancelled=cancelled)
    assert root_store.check_only().execution_blocked_local
    assert backend.effects == ["create_vhd"] and not backend.alive and transport.closed


def test_concurrent_or_reentrant_run_cannot_acquire_another_owner(fixture):
    _, _, runtime, _, _, plan, transport, backend, _ = fixture

    def concurrent():
        with pytest.raises(ProvisionError, match="owner_running"):
            runtime.run(plan)
        with ThreadPoolExecutor(max_workers=1) as pool:
            with pytest.raises(ProvisionError, match="owner_running"):
                pool.submit(runtime.run, plan).result()

    backend.on_ready = concurrent
    assert runtime.run(plan).verified
    assert len(transport.calls) == 1


def test_assets_initialization_refuses_completed_history_even_if_old_assets_lost(fixture):
    setup, _, runtime, _, workspace, plan, _, _, _ = fixture
    root, binding, _, cipher = setup
    assert runtime.run(plan).verified
    remove_fixture_resource(root, root.with_name("bees-provisioner-assets"))
    remove_fixture_resource(root, workspace)
    before = snapshot(root)
    with pytest.raises(ProvisionError, match="virgin_required"):
        ProvisionerRuntime.initialize_assets(binding=binding, cipher=cipher)
    assert snapshot(root) == before and not root.with_name("bees-provisioner-assets").exists()


def test_orphan_hardware_directory_is_not_adopted_before_preflight(fixture):
    _, _, runtime, _, workspace, plan, transport, backend, counters = fixture
    orphan = workspace / str(uuid4())
    orphan.mkdir(mode=0o700)
    set_hardware_acl(orphan)
    with pytest.raises(ProvisionError, match="workspace_orphan"):
        runtime.run(plan)
    assert not any(counters) and not transport.calls and not backend.effects and orphan.exists()


def test_assets_prepare_refuses_an_existing_hardware_root_even_for_virgin_control(base):
    setup, root_store = base
    root, binding, _, cipher = setup
    resource_binding = AssetsBinding.from_root_check(root_store.check_only())
    prepare_workspace(resource_binding)
    with pytest.raises(ProvisionError, match="workspace_already_present"):
        ProvisionerRuntime.initialize_assets(binding=binding, cipher=cipher)
    assert not root.with_name("bees-provisioner-assets").exists()


def test_cancel_after_claim_is_persisted_without_creating_a_journal_or_effect(fixture):
    setup, root_store, runtime, _, _, plan, transport, backend, _ = fixture
    root, _, _, _ = setup
    cancelled = Event()
    transport.before_return = cancelled.set
    with pytest.raises(ProvisionError, match="acquisition_unknown"):
        runtime.run(plan, cancelled=cancelled)
    assert root_store.check_only().execution_blocked_local
    assert not (root / "state/plans" / str(plan.plan_id)).exists()
    assert len(transport.calls) == 1 and not backend.effects


def test_real_template_rejects_fixture_bytes_before_any_claim(fixture, monkeypatch):
    setup, _, runtime, _, _, plan, transport, backend, counters = fixture
    root, _, _, _ = setup
    from bees_host.provisioning.image import LocalTemplate

    monkeypatch.setattr(module, "LocalTemplate", LocalTemplate)
    with pytest.raises(ProvisionError, match="image_invalid"):
        runtime.run(plan)
    assert counters[1] == [True] and not transport.calls and not backend.effects
    assert not rows(root, "acquisitions")


def test_abrupt_process_crash_after_prepare_releases_locks_but_never_adopts_attempt(fixture):
    setup, root_store, _, _, _, plan, transport, backend, counters = fixture
    root, binding, _, cipher = setup
    code = (
        child_script(root, binding)
        + child_cipher_script(cipher)
        + (
            "from bees_host.provisioning import runtime as r,acquisition as a\n"
            "from bees_host.provisioning.contracts import Plan\n"
            "class Backend:\n"
            " def __init__(self,path): pass\n"
            " def preflight(self): pass\n"
            "class Template:\n"
            " def __init__(self,path): self.path=path\n"
            " def verify(self,plan): return self.path/'debian-13.7.0-amd64-netinst.iso'\n"
            "def crash(*args,**kwargs): os._exit(77)\n"
            "r.HyperVBackend=Backend; r.LocalTemplate=Template; a.HTTPAuthority=crash\n"
            f"plan=Plan.model_validate_json({plan.model_dump_json()!r})\n"
            "r.ProvisionerRuntime.open(binding=binding,cipher=cipher).run(plan)\n"
        )
    )
    child = subprocess.run([sys.executable, "-c", code], capture_output=True, timeout=60)
    assert child.returncode == 77, child.stderr.decode()
    assert root_store.check_only().execution_blocked_local
    prior = rows(root, "acquisitions")
    assert len(prior) == 1 and prior[0]["status"] == "prepared"
    replacement = ProvisionerRuntime.open(binding=binding, cipher=cipher)
    with pytest.raises(ProvisionError, match="reconciliation_required"):
        replacement.run(plan)
    assert rows(root, "acquisitions") == prior
    assert not any(counters) and not transport.calls and not backend.effects
