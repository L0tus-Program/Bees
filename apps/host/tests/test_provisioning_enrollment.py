"""Inscrição descartável: cifra/ACL/locks/crash reais, sem rede ou hardware."""

import json
import os
import socket
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from bees_host.provisioning import enrollment as module
from bees_host.provisioning.contracts import ProvisionError, canonical
from bees_host.provisioning.enrollment import (
    EnrollmentBinding,
    EnrollmentStore,
)
from bees_host.provisioning.journal import create
from bees_host.security import DPAPICipher, FernetCipher
from cryptography.fernet import Fernet
from test_guest_bridge_private import allow_everyone
from test_guest_bridge_tls import private_bridge_directory

SECRET = "bp_" + "a" * 43


def cipher_fixture():
    if os.name == "nt":
        return DPAPICipher()
    return FernetCipher(Fernet.generate_key().decode())


def bootstrap_value(binding, now):
    return {
        "format": 1,
        **binding.model_dump(mode="json"),
        "provisioner_credential": SECRET,
        "expires_at": (now + timedelta(minutes=10)).isoformat(),
    }


@pytest.fixture
def fixture():
    with private_bridge_directory() as base:
        binding = EnrollmentBinding(
            origin="http://localhost:8080",
            installation_id=uuid4(),
            host_id=uuid4(),
            provisioner_id=uuid4(),
            issue_request_id=uuid4(),
        )
        now = datetime.now(UTC)
        bootstrap = base / "bootstrap.json"
        create(bootstrap, canonical(bootstrap_value(binding, now)))
        yield base, binding, bootstrap, cipher_fixture(), now


def initialize(fixture, **changes):
    base, binding, bootstrap, cipher, now = fixture
    values = {"binding": binding, "cipher": cipher, "now": now}
    values.update(changes)
    return EnrollmentStore.initialize(base / "enrollment", bootstrap, **values)


def snapshot(directory):
    return {path.name: (path.read_bytes(), path.stat().st_mtime_ns) for path in directory.iterdir()}


def test_native_enrollment_reload_and_check_are_immutable_offline(fixture, monkeypatch, caplog):
    def no_network(*args, **kwargs):
        pytest.fail("Inscrição tentou rede ou DNS")

    monkeypatch.setattr(socket, "getaddrinfo", no_network)
    monkeypatch.setattr(socket.socket, "connect", no_network)
    base, binding, bootstrap, cipher, _ = fixture
    store = initialize(fixture)
    assert not bootstrap.exists()
    assert set(snapshot(store.directory)) == module.FINAL_FILES
    original = snapshot(store.directory)
    state = store.load()
    assert state.provisioner_credential.get_secret_value() == SECRET
    assert SECRET not in repr(state) and SECRET not in str(state)
    assert SECRET not in repr(store)
    for path in store.directory.iterdir():
        assert SECRET.encode() not in path.read_bytes()
    restored = EnrollmentStore.open(base / "enrollment", binding=binding, cipher=cipher)
    assert restored.load() == state
    assert restored.check_only().model_dump(mode="json") == {
        "store_id": str(state.store_id),
        **{name: str(getattr(binding, name)) for name in module.ID_FIELDS},
        "configured_local": True,
    }
    assert snapshot(store.directory) == original
    assert SECRET not in caplog.text
    with pytest.raises(ProvisionError, match="provision_state_already_present"):
        initialize(fixture)
    assert snapshot(store.directory) == original


@pytest.mark.parametrize("name", module.ID_FIELDS + ("origin",))
def test_expected_binding_checked_before_root_or_bootstrap_mutation(fixture, name):
    base, binding, bootstrap, _, _ = fixture
    changed = binding.model_dump()
    changed[name] = "https://localhost:9090" if name == "origin" else uuid4()
    before = bootstrap.read_bytes()
    with pytest.raises(ProvisionError, match="provision_enrollment_binding_invalid"):
        initialize(fixture, binding=EnrollmentBinding(**changed))
    assert bootstrap.read_bytes() == before and not (base / "enrollment").exists()


@pytest.mark.parametrize("name", module.ID_FIELDS + ("origin",))
def test_expected_binding_checked_on_every_open(fixture, name):
    _, binding, _, cipher, _ = fixture
    store = initialize(fixture)
    changed = binding.model_dump()
    changed[name] = "https://localhost:9090" if name == "origin" else uuid4()
    before = snapshot(store.directory)
    with pytest.raises(ProvisionError, match="provision_enrollment_binding_invalid"):
        EnrollmentStore.open(store.directory, binding=EnrollmentBinding(**changed), cipher=cipher)
    assert snapshot(store.directory) == before


@pytest.mark.parametrize(
    "changes",
    [
        {"format": True},
        {"format": 2},
        {"extra": "untrusted"},
        {"provisioner_credential": "bh_" + "a" * 43},
        {"provisioner_credential": "bp_" + "a" * 42},
        {"provisioner_credential": "bp_" + "a" * 44},
        {"provisioner_credential": "bp_" + "a" * 42 + "+"},
        {"expires_at": "2026-10-08T10:00:00"},
        *[{name: str(UUID(int=0))} for name in module.ID_FIELDS],
    ],
)
def test_closed_bootstrap_contract_never_creates_or_deletes(fixture, changes):
    base, binding, bootstrap, _, now = fixture
    value = bootstrap_value(binding, now)
    value.update(changes)
    bootstrap.write_bytes(canonical(value))
    original = bootstrap.read_bytes()
    with pytest.raises(ProvisionError) as caught:
        initialize(fixture)
    assert SECRET not in str(caught.value) and str(base) not in str(caught.value)
    assert caught.value.__cause__ is None
    assert bootstrap.read_bytes() == original
    assert not (base / "enrollment").exists()


@pytest.mark.parametrize(
    "origin",
    [
        "http://example.com",
        "http://127.0.0.2:8080",
        "http://LOCALHOST:8080",
        "HTTP://localhost:8080",
        "http://localhost:08080",
        "http://localhost:0",
        "http://localhost:65536",
        "http://localhost:8080/",
        "http://localhost:8080/path",
        "http://localhost:8080?",
        "http://localhost:8080#",
        "http://localhost:8080?query=1",
        "http://localhost:8080#fragment",
        "http://user@localhost:8080",
        "http://user:password@localhost:8080",
        "http://localhost:8080\n",
        " http://localhost:8080",
        "ftp://localhost:8080",
    ],
)
def test_origin_is_canonical_loopback_offline(fixture, origin):
    base, binding, bootstrap, _, now = fixture
    value = bootstrap_value(binding, now)
    value["origin"] = origin
    bootstrap.write_bytes(canonical(value))
    with pytest.raises(ProvisionError):
        initialize(fixture)
    assert bootstrap.exists() and not (base / "enrollment").exists()


@pytest.mark.parametrize("origin", ["http://localhost", "https://127.0.0.1", "https://[::1]:443"])
def test_canonical_loopback_variants(fixture, origin):
    _, binding, bootstrap, _, now = fixture
    changed = EnrollmentBinding(**{**binding.model_dump(), "origin": origin})
    bootstrap.write_bytes(canonical(bootstrap_value(changed, now)))
    assert initialize(fixture, binding=changed).load().origin == origin


@pytest.mark.parametrize("seconds", [0, -1, 901, 1000000])
def test_bootstrap_expiration_has_bounded_ttl(fixture, seconds):
    base, binding, bootstrap, _, now = fixture
    value = bootstrap_value(binding, now)
    value["expires_at"] = (now + timedelta(seconds=seconds)).isoformat()
    bootstrap.write_bytes(canonical(value))
    with pytest.raises(ProvisionError, match="provision_enrollment_expired"):
        initialize(fixture)
    assert bootstrap.exists() and not (base / "enrollment").exists()


@pytest.mark.parametrize("elapsed", [600, 601, -1])
def test_expiration_or_monotonic_regression_before_unlink_blocks_sealing(
    fixture, monkeypatch, elapsed
):
    base, binding, bootstrap, cipher, _ = fixture
    ticks = iter([100.0, 100.0 + elapsed])
    monkeypatch.setattr(module.time, "monotonic", lambda: next(ticks))
    with pytest.raises(ProvisionError, match="provision_enrollment_expired"):
        initialize(fixture)
    assert bootstrap.exists() and (base / "enrollment" / "staged.bin").exists()
    assert not (base / "enrollment" / "credentials.bin").exists()
    before = snapshot(base / "enrollment")
    with pytest.raises(ProvisionError):
        EnrollmentStore.open(base / "enrollment", binding=binding, cipher=cipher)
    assert snapshot(base / "enrollment") == before


def test_default_clock_rechecks_wall_time_before_unlink(fixture, monkeypatch):
    base, _, bootstrap, _, now = fixture

    class Clock(datetime):
        @classmethod
        def now(cls, zone):
            return now + timedelta(minutes=11)

    # O primeiro now real é aplicado antes de trocar a fonte, durante cifragem.
    cipher = fixture[3]
    original_encrypt = cipher.encrypt

    def advance(data):
        monkeypatch.setattr(module, "datetime", Clock)
        return original_encrypt(data)

    monkeypatch.setattr(cipher, "encrypt", advance)
    with pytest.raises(ProvisionError, match="provision_enrollment_expired"):
        initialize(fixture, now=None)
    assert bootstrap.exists() and (base / "enrollment" / "staged.bin").exists()
    assert not (base / "enrollment" / "credentials.bin").exists()


@pytest.mark.parametrize("now", [datetime(2026, 10, 8), "2026-10-08", 0])
def test_invalid_clock_is_rejected_before_state(fixture, now):
    base, _, bootstrap, _, _ = fixture
    with pytest.raises(ProvisionError):
        initialize(fixture, now=now)
    assert bootstrap.exists() and not (base / "enrollment").exists()


@pytest.mark.parametrize(
    "data",
    [
        b"",
        b"\xff",
        b"{}",
        b"[]",
        b'{"format":1,"format":1}',
        b'{"format":NaN}',
        b'{"format":Infinity}',
        b" " * (module.MAX_JSON + 1),
        b"[" * 2000 + b"]" * 2000,
    ],
    ids=["empty", "utf8", "object", "array", "duplicate", "nan", "infinite", "size", "nesting"],
)
def test_bootstrap_json_is_bounded_strict_utf8_unique_and_finite(fixture, data):
    base, _, bootstrap, _, _ = fixture
    bootstrap.write_bytes(data)
    with pytest.raises(ProvisionError):
        initialize(fixture)
    assert bootstrap.read_bytes() == data and not (base / "enrollment").exists()


@pytest.mark.parametrize("name", ["identity.json", "owner.lock", "credentials.bin"])
def test_loss_never_recreates_or_rotates(fixture, name):
    _, binding, _, cipher, _ = fixture
    store = initialize(fixture)
    lost = store.directory / name
    lost.unlink()
    before = snapshot(store.directory)
    with pytest.raises(ProvisionError):
        EnrollmentStore.open(store.directory, binding=binding, cipher=cipher)
    with pytest.raises(ProvisionError, match="provision_state_already_present"):
        initialize(fixture)
    assert not lost.exists() and snapshot(store.directory) == before


@pytest.mark.parametrize("name", ["staged.bin", "extra", "public-status.json", "subdirectory"])
def test_final_store_rejects_extras_without_cleaning(fixture, name):
    store = initialize(fixture)
    create(store.directory / name, b"private extra")
    before = snapshot(store.directory)
    with pytest.raises(ProvisionError, match="provision_enrollment_invalid"):
        store.check_only()
    assert snapshot(store.directory) == before


def test_extra_directory_never_removed_or_traversed(fixture):
    store = initialize(fixture)
    extra = store.directory / "extra-directory"
    extra.mkdir()
    create(extra / "evidence", b"preserve")
    with pytest.raises(ProvisionError, match="provision_enrollment_invalid"):
        store.load()
    assert (extra / "evidence").read_bytes() == b"preserve"


@pytest.mark.parametrize("name", ["identity.json", "owner.lock", "credentials.bin"])
def test_hardlink_and_foreign_acl_fail_closed(fixture, name):
    base, _, _, _, _ = fixture
    store = initialize(fixture)
    path = store.directory / name
    alias = base / "hardlink"
    os.link(path, alias)
    with pytest.raises(ProvisionError, match="provision_private_required"):
        store.load()
    alias.unlink()
    allow_everyone(path)
    with pytest.raises(ProvisionError, match="provision_private_required"):
        store.load()


def test_replaceable_ancestor_rejected_without_protection_repair(fixture):
    base, _, bootstrap, _, _ = fixture
    original = bootstrap.read_bytes()
    allow_everyone(base)
    with pytest.raises(ProvisionError, match="provision_private_required"):
        initialize(fixture)
    assert bootstrap.read_bytes() == original and not (base / "enrollment").exists()


def test_bootstrap_hardlink_and_foreign_acl_never_deleted(fixture):
    base, _, bootstrap, _, _ = fixture
    alias = base / "bootstrap-copy"
    os.link(bootstrap, alias)
    with pytest.raises(ProvisionError, match="provision_private_required"):
        initialize(fixture)
    alias.unlink()
    allow_everyone(bootstrap)
    with pytest.raises(ProvisionError, match="provision_private_required"):
        initialize(fixture)
    assert bootstrap.exists() and not (base / "enrollment").exists()


@pytest.mark.parametrize("artifact", ["identity.json", "credentials.bin"])
@pytest.mark.parametrize("change", ["duplicate", "format", "zero", "extra", "huge", "binding"])
def test_marker_and_encrypted_payload_validation(fixture, artifact, change):
    _, _, _, cipher, _ = fixture
    store = initialize(fixture)
    path = store.directory / artifact
    raw = path.read_bytes() if artifact == "identity.json" else cipher.decrypt(path.read_bytes())
    value = json.loads(raw)
    if change == "duplicate":
        data = raw[:-1] + b',"format":1}'
    elif change == "huge":
        data = b" " * (module.MAX_JSON + 1)
    else:
        value.update(
            {
                "format": {"format": True},
                "zero": {"store_id": str(UUID(int=0))},
                "extra": {"extra": "untrusted"},
                "binding": {"provisioner_id": str(uuid4())},
            }[change]
        )
        data = canonical(value)
    path.write_bytes(data if artifact == "identity.json" else cipher.encrypt(data))
    before = snapshot(store.directory)
    with pytest.raises(ProvisionError):
        store.load()
    assert snapshot(store.directory) == before


def test_ciphertext_store_identity_swap_is_refused(fixture):
    _, _, _, cipher, _ = fixture
    store = initialize(fixture)
    path = store.directory / "credentials.bin"
    value = json.loads(cipher.decrypt(path.read_bytes()))
    value["store_id"] = str(uuid4())
    path.write_bytes(cipher.encrypt(canonical(value)))
    with pytest.raises(ProvisionError, match="provision_enrollment_invalid"):
        store.load()


@pytest.mark.parametrize(
    "ciphertext", [b"", b"corrupt", b"x" * (module.MAX_BLOB + 1)], ids=["empty", "corrupt", "size"]
)
def test_corrupt_or_oversized_ciphertext_never_repaired(fixture, ciphertext):
    store = initialize(fixture)
    path = store.directory / "credentials.bin"
    path.write_bytes(ciphertext)
    with pytest.raises(ProvisionError) as caught:
        store.check_only()
    assert caught.value.__cause__ is None and SECRET not in str(caught.value)
    assert path.read_bytes() == ciphertext


@pytest.mark.parametrize("stage", ["identity.json", "owner.lock", "staged.bin", "credentials.bin"])
def test_write_failure_preserves_partial_evidence_and_blocks_reinitialize(
    fixture, monkeypatch, stage
):
    base, binding, bootstrap, cipher, _ = fixture
    original_create = module.create

    def fail(path, data):
        if path.name == stage:
            raise OSError("private path and secret should be suppressed " + SECRET)
        original_create(path, data)

    monkeypatch.setattr(module, "create", fail)
    with pytest.raises(ProvisionError, match="provision_enrollment_unavailable") as caught:
        initialize(fixture)
    assert SECRET not in str(caught.value) and caught.value.__cause__ is None
    directory = base / "enrollment"
    assert directory.exists()
    assert bootstrap.exists() == (stage != "credentials.bin")
    before = snapshot(directory)
    with pytest.raises(ProvisionError):
        EnrollmentStore.open(directory, binding=binding, cipher=cipher)
    with pytest.raises(ProvisionError, match="provision_state_already_present"):
        initialize(fixture)
    assert snapshot(directory) == before


@pytest.mark.parametrize("name", ["bootstrap.json", "staged.bin"])
def test_unlink_failure_is_quarantined_even_after_bootstrap_consumption(fixture, monkeypatch, name):
    base, binding, bootstrap, cipher, _ = fixture
    original_unlink = Path.unlink
    original_consume = module._consume_bootstrap

    def fail(path, *args, **kwargs):
        if path.name == name:
            raise OSError("private unlink failure " + SECRET)
        return original_unlink(path, *args, **kwargs)

    def consume(path, *args, **kwargs):
        if name == "bootstrap.json":
            raise OSError("private consume failure " + SECRET)
        return original_consume(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "unlink", fail)
        patch.setattr(module, "_consume_bootstrap", consume)
        with pytest.raises(ProvisionError, match="provision_enrollment_unavailable"):
            initialize(fixture)
    directory = base / "enrollment"
    assert (directory / "staged.bin").exists()
    assert bootstrap.exists() == (name == "bootstrap.json")
    before = snapshot(directory)
    with pytest.raises(ProvisionError):
        EnrollmentStore.open(directory, binding=binding, cipher=cipher)
    with pytest.raises(ProvisionError, match="provision_state_already_present"):
        initialize(fixture)
    assert snapshot(directory) == before


def test_bootstrap_sync_failure_never_seals_final(fixture, monkeypatch):
    base, binding, bootstrap, cipher, _ = fixture
    original_sync = module.sync_directory
    calls = 0

    def fail(path):
        nonlocal calls
        if path == base:
            calls += 1
            if calls == 2:
                raise OSError("private sync failure")
        original_sync(path)

    monkeypatch.setattr(module, "sync_directory", fail)
    with pytest.raises(ProvisionError, match="provision_enrollment_unavailable"):
        initialize(fixture)
    assert not bootstrap.exists()
    assert not (base / "enrollment" / "credentials.bin").exists()
    with pytest.raises(ProvisionError):
        EnrollmentStore.open(base / "enrollment", binding=binding, cipher=cipher)


def test_staged_readback_failure_keeps_bootstrap_and_partial_store(fixture, monkeypatch):
    base, _, bootstrap, cipher, _ = fixture
    monkeypatch.setattr(cipher, "decrypt", lambda data: b"{}")
    with pytest.raises(ProvisionError):
        initialize(fixture)
    assert bootstrap.exists() and (base / "enrollment" / "staged.bin").exists()
    assert not (base / "enrollment" / "credentials.bin").exists()


def test_bootstrap_changed_after_staging_is_preserved(fixture, monkeypatch):
    base, binding, bootstrap, cipher, now = fixture
    original_decrypt = cipher.decrypt

    def tamper(data):
        changed = bootstrap_value(binding, now)
        changed["provisioner_id"] = str(uuid4())
        bootstrap.write_bytes(canonical(changed))
        return original_decrypt(data)

    monkeypatch.setattr(cipher, "decrypt", tamper)
    with pytest.raises(ProvisionError, match="provision_enrollment_invalid"):
        initialize(fixture)
    assert bootstrap.exists() and (base / "enrollment" / "staged.bin").exists()
    assert not (base / "enrollment" / "credentials.bin").exists()


def test_untrusted_plaintext_cipher_is_rejected_before_root(fixture):
    class Plaintext:
        def encrypt(self, value):
            return value

        def decrypt(self, value):
            return value

    base, _, bootstrap, _, _ = fixture
    with pytest.raises(ProvisionError, match="provision_enrollment_protection_required"):
        initialize(fixture, cipher=Plaintext())
    assert bootstrap.exists() and not (base / "enrollment").exists()


@pytest.mark.skipif(os.name != "nt", reason="Fernet não substitui DPAPI no Windows")
def test_external_fernet_is_rejected_on_windows(fixture):
    base, _, bootstrap, _, _ = fixture
    with pytest.raises(ProvisionError, match="provision_enrollment_protection_required"):
        initialize(fixture, cipher=FernetCipher(Fernet.generate_key().decode()))
    assert bootstrap.exists() and not (base / "enrollment").exists()


def test_native_lock_blocks_other_instance_process_and_thread(fixture):
    _, binding, _, cipher, _ = fixture
    store = initialize(fixture)
    code = """
import os,sys
from pathlib import Path
from bees_host.provisioning.enrollment import EnrollmentBinding,EnrollmentStore
from bees_host.provisioning.contracts import ProvisionError
from bees_host.security import DPAPICipher,FernetCipher
cipher=DPAPICipher() if os.name=='nt' else FernetCipher(os.environ['BEES_HOST_STATE_KEY'])
try:
 EnrollmentStore.open(Path(sys.argv[1]),binding=EnrollmentBinding.model_validate_json(sys.stdin.read()),cipher=cipher)
except ProvisionError as error:
 sys.exit(0 if str(error)=='provision_owner_running' else 11)
sys.exit(12)
"""
    environment = dict(os.environ)
    if os.name != "nt":
        import base64

        environment["BEES_HOST_STATE_KEY"] = base64.urlsafe_b64encode(
            cipher.cipher._signing_key + cipher.cipher._encryption_key
        ).decode()
    with store.lock():
        assert store.load().provisioner_credential.get_secret_value() == SECRET
        with pytest.raises(ProvisionError, match="provision_owner_running"):
            EnrollmentStore.open(store.directory, binding=binding, cipher=cipher)
        with ThreadPoolExecutor(max_workers=1) as pool:
            with pytest.raises(ProvisionError, match="provision_lock_required"):
                pool.submit(store.check_only).result(timeout=5)
        result = subprocess.run(
            [sys.executable, "-c", code, str(store.directory)],
            input=binding.model_dump_json(),
            text=True,
            env=environment,
            capture_output=True,
            timeout=15,
            check=False,
        )
        assert result.returncode == 0 and not result.stdout and not result.stderr
    assert store.check_only().configured_local is True


@pytest.mark.parametrize("crash_file", ["staged.bin", "credentials.bin"])
def test_real_process_crash_preserves_partial_and_never_resumes(fixture, crash_file):
    base, binding, bootstrap, _, _ = fixture
    key = Fernet.generate_key().decode()
    environment = {**os.environ, "BEES_HOST_STATE_KEY": key}
    code = """
import os,sys
from pathlib import Path
from bees_host.provisioning import enrollment as m
from bees_host.provisioning.enrollment import EnrollmentBinding,EnrollmentStore
from bees_host.security import native_cipher
original=m.create
def crash(path,data):
 original(path,data)
 if path.name==sys.argv[3]: os._exit(31)
m.create=crash
EnrollmentStore.initialize(Path(sys.argv[1]),Path(sys.argv[2]),binding=EnrollmentBinding.model_validate_json(sys.stdin.read()),cipher=native_cipher())
"""
    result = subprocess.run(
        [sys.executable, "-c", code, str(base / "enrollment"), str(bootstrap), crash_file],
        input=binding.model_dump_json(),
        text=True,
        env=environment,
        capture_output=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 31 and not result.stdout and not result.stderr
    assert bootstrap.exists() == (crash_file == "staged.bin")
    assert (base / "enrollment" / "staged.bin").exists()
    cipher = DPAPICipher() if os.name == "nt" else FernetCipher(key)
    before = snapshot(base / "enrollment")
    with pytest.raises(ProvisionError):
        EnrollmentStore.open(base / "enrollment", binding=binding, cipher=cipher)
    with pytest.raises(ProvisionError, match="provision_state_already_present"):
        initialize(fixture)
    assert snapshot(base / "enrollment") == before


def test_two_roots_competing_for_one_bootstrap_only_one_can_seal(fixture, monkeypatch):
    base, binding, bootstrap, cipher, now = fixture
    barrier = threading.Barrier(2)
    original_consume = module._consume_bootstrap

    def synchronized(path, *args, **kwargs):
        if path == bootstrap:
            barrier.wait(timeout=10)
        return original_consume(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(module, "_consume_bootstrap", synchronized)
        with ThreadPoolExecutor(max_workers=2) as pool:
            pending = [
                pool.submit(
                    EnrollmentStore.initialize,
                    base / name,
                    bootstrap,
                    binding=binding,
                    cipher=cipher,
                    now=now,
                )
                for name in ("first", "second")
            ]
            outcomes = []
            for future in pending:
                try:
                    outcomes.append(future.result(timeout=15))
                except ProvisionError:
                    outcomes.append(None)
    assert sum(store is not None for store in outcomes) == 1
    assert not bootstrap.exists()
    for name, store in zip(("first", "second"), outcomes, strict=True):
        directory = base / name
        if store is not None:
            assert store.check_only().configured_local
        else:
            assert (directory / "staged.bin").exists()
            assert not (directory / "credentials.bin").exists()
            with pytest.raises(ProvisionError):
                EnrollmentStore.open(directory, binding=binding, cipher=cipher)


def test_two_processes_compete_at_native_consumption_boundary(fixture):
    base, binding, bootstrap, cipher, _ = fixture
    environment = dict(os.environ)
    if os.name != "nt":
        import base64

        environment["BEES_HOST_STATE_KEY"] = base64.urlsafe_b64encode(
            cipher.cipher._signing_key + cipher.cipher._encryption_key
        ).decode()
    code = """
import os,sys,time
from pathlib import Path
from bees_host.provisioning import enrollment as m
from bees_host.provisioning.enrollment import EnrollmentBinding,EnrollmentStore
from bees_host.provisioning.contracts import ProvisionError
from bees_host.provisioning.journal import create
from bees_host.security import native_cipher
base=Path(sys.argv[1]); name=sys.argv[2]
binding=EnrollmentBinding.model_validate_json(sys.stdin.read())
original=m._consume_bootstrap
def synchronized(*args,**kwargs):
 create(base/(name+'.ready'),b'0')
 deadline=time.monotonic()+15
 while not (base/'go').exists():
  if time.monotonic()>deadline: sys.exit(29)
  time.sleep(.01)
 return original(*args,**kwargs)
m._consume_bootstrap=synchronized
try:
 EnrollmentStore.initialize(base/name,base/'bootstrap.json',binding=binding,cipher=native_cipher())
except ProvisionError:
 sys.exit(19)
sys.exit(0)
"""
    children = []
    try:
        for name in ("first", "second"):
            child = subprocess.Popen(
                [sys.executable, "-c", code, str(base), name],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=environment,
            )
            children.append(child)
            child.stdin.write(binding.model_dump_json())
            child.stdin.close()
            child.stdin = None
        deadline = time.monotonic() + 20
        while not all((base / (name + ".ready")).exists() for name in ("first", "second")):
            assert time.monotonic() < deadline
            assert all(child.poll() is None for child in children)
            time.sleep(0.01)
        create(base / "go", b"0")
        for child in children:
            stdout, stderr = child.communicate(timeout=20)
            assert not stdout and not stderr
        assert sorted(child.returncode for child in children) == [0, 19]
        assert not bootstrap.exists()
        for name, child in zip(("first", "second"), children, strict=True):
            directory = base / name
            if child.returncode == 0:
                assert (
                    EnrollmentStore.open(directory, binding=binding, cipher=cipher)
                    .check_only()
                    .configured_local
                )
            else:
                assert (directory / "staged.bin").exists()
                assert not (directory / "credentials.bin").exists()
                with pytest.raises(ProvisionError):
                    EnrollmentStore.open(directory, binding=binding, cipher=cipher)
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()  # Somente subprocesso de fixture criado neste teste.
            child.communicate(timeout=10)


@pytest.mark.skipif(os.name != "nt", reason="Compartilhamento nativo Windows")
def test_windows_open_reader_prevents_consumption_and_seal(fixture):
    base, _, bootstrap, _, _ = fixture
    original = bootstrap.read_bytes()
    with bootstrap.open("rb"):
        with pytest.raises(ProvisionError, match="provision_enrollment_unavailable"):
            initialize(fixture)
    assert bootstrap.read_bytes() == original
    assert (base / "enrollment/staged.bin").exists()
    assert not (base / "enrollment/credentials.bin").exists()


@pytest.mark.skipif(os.name != "nt", reason="Disposition nativo Windows")
def test_windows_expiration_during_last_private_guard_preserves_bootstrap(fixture, monkeypatch):
    base, _, bootstrap, _, _ = fixture
    original = bootstrap.read_bytes()
    consume = module._windows_consume_bootstrap
    private = module.private
    tick = 100.0
    consuming = False
    guards = 0

    def guarded(path, **kwargs):
        nonlocal tick, guards
        private(path, **kwargs)
        if consuming and path == bootstrap:
            guards += 1
            if guards == 4:  # pré-open + três guardas do HANDLE, incluindo a última.
                tick += 600

    def entered(*args):
        nonlocal consuming
        consuming = True
        return consume(*args)

    monkeypatch.setattr(module.time, "monotonic", lambda: tick)
    monkeypatch.setattr(module, "private", guarded)
    monkeypatch.setattr(module, "_windows_consume_bootstrap", entered)
    with pytest.raises(ProvisionError, match="provision_enrollment_expired"):
        initialize(fixture)
    assert guards == 4
    assert bootstrap.read_bytes() == original
    assert (base / "enrollment/staged.bin").exists()
    assert not (base / "enrollment/credentials.bin").exists()


@pytest.mark.skipif(os.name != "nt", reason="Disposition nativo Windows")
def test_windows_disposition_failure_keeps_source_and_partial(fixture, monkeypatch):
    base, binding, bootstrap, cipher, _ = fixture
    original = bootstrap.read_bytes()

    def fail(handle):
        raise OSError("private disposition failure " + SECRET)

    monkeypatch.setattr(module, "_windows_mark_deleted", fail)
    with pytest.raises(ProvisionError, match="provision_enrollment_unavailable") as error:
        initialize(fixture)
    assert SECRET not in str(error.value)
    assert bootstrap.read_bytes() == original
    assert (base / "enrollment/staged.bin").exists()
    assert not (base / "enrollment/credentials.bin").exists()
    with pytest.raises(ProvisionError):
        EnrollmentStore.open(base / "enrollment", binding=binding, cipher=cipher)


@pytest.mark.skipif(os.name != "nt", reason="Guarda do handle exclusivo Windows")
@pytest.mark.parametrize("change", ["hardlink", "acl", "bytes", "size"])
def test_windows_source_changes_at_consumption_boundary_are_preserved(fixture, monkeypatch, change):
    base, _, bootstrap, _, _ = fixture
    original_consume = module._windows_consume_bootstrap

    def changed(*args):
        if change == "hardlink":
            os.link(bootstrap, base / "alias")
        elif change == "acl":
            allow_everyone(bootstrap)
        elif change == "bytes":
            data = bootstrap.read_bytes().replace(SECRET.encode(), ("bp_" + "b" * 43).encode())
            bootstrap.write_bytes(data)
        else:
            bootstrap.write_bytes(b"{}")
        return original_consume(*args)

    monkeypatch.setattr(module, "_windows_consume_bootstrap", changed)
    with pytest.raises(ProvisionError):
        initialize(fixture)
    assert bootstrap.exists() and (base / "enrollment/staged.bin").exists()
    assert not (base / "enrollment/credentials.bin").exists()


@pytest.mark.skipif(os.name != "nt", reason="Ownership CRT nativo Windows")
def test_windows_consumption_descriptor_is_not_inheritable(fixture, monkeypatch):
    import msvcrt

    original_open = msvcrt.open_osfhandle
    descriptors = []

    def opened(handle, flags):
        descriptor = original_open(handle, flags)
        descriptors.append(descriptor)
        assert flags & os.O_NOINHERIT
        assert not os.get_inheritable(descriptor)
        return descriptor

    monkeypatch.setattr(msvcrt, "open_osfhandle", opened)
    assert initialize(fixture).check_only().configured_local
    assert len(descriptors) == 1
    with pytest.raises(OSError):
        os.fstat(descriptors[0])


@pytest.mark.skipif(os.name != "nt", reason="Crash em disposition/close Windows")
@pytest.mark.parametrize("after_disposition", [False, True])
def test_windows_crash_at_disposition_never_publishes_final(fixture, after_disposition):
    base, binding, bootstrap, cipher, _ = fixture
    code = """
import os,sys
from pathlib import Path
from bees_host.provisioning import enrollment as m
from bees_host.provisioning.enrollment import EnrollmentBinding,EnrollmentStore
from bees_host.security import native_cipher
original=m._windows_mark_deleted
def crash(handle):
 if sys.argv[2]=='after': original(handle)
 os._exit(37)
m._windows_mark_deleted=crash
base=Path(sys.argv[1])
EnrollmentStore.initialize(base/'enrollment',base/'bootstrap.json',binding=EnrollmentBinding.model_validate_json(sys.stdin.read()),cipher=native_cipher())
"""
    result = subprocess.run(
        [sys.executable, "-c", code, str(base), "after" if after_disposition else "before"],
        input=binding.model_dump_json(),
        text=True,
        capture_output=True,
        timeout=20,
        check=False,
    )
    assert result.returncode == 37 and not result.stdout and not result.stderr
    assert bootstrap.exists() == (not after_disposition)
    assert (base / "enrollment/staged.bin").exists()
    assert not (base / "enrollment/credentials.bin").exists()
    before = snapshot(base / "enrollment")
    with pytest.raises(ProvisionError):
        EnrollmentStore.open(base / "enrollment", binding=binding, cipher=cipher)
    with pytest.raises(ProvisionError, match="provision_state_already_present"):
        initialize(fixture)
    assert snapshot(base / "enrollment") == before


@pytest.mark.skipif(os.name == "nt", reason="Chave externa é requisito Linux")
def test_linux_default_requires_explicit_external_key(fixture, monkeypatch):
    base, _, bootstrap, _, _ = fixture
    monkeypatch.delenv("BEES_HOST_STATE_KEY", raising=False)
    with pytest.raises(ProvisionError, match="provision_enrollment_unavailable"):
        initialize(fixture, cipher=None)
    assert bootstrap.exists() and not (base / "enrollment").exists()
    monkeypatch.setenv("BEES_HOST_STATE_KEY", Fernet.generate_key().decode())
    store = initialize(fixture, cipher=None)
    assert store.load().provisioner_credential.get_secret_value() == SECRET


@pytest.mark.skipif(os.name != "nt", reason="DPAPI CurrentUser real Windows")
def test_windows_default_uses_real_dpapi_current_user(fixture):
    store = initialize(fixture, cipher=None)
    assert type(store.cipher) is DPAPICipher
    assert store.load().provisioner_credential.get_secret_value() == SECRET
