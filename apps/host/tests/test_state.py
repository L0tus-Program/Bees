import json
import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from bees_host.contracts import Bootstrap
from bees_host.errors import HostError
from bees_host.security import DPAPICipher, FernetCipher, check_private, native_cipher
from bees_host.state import StateStore
from cryptography.fernet import Fernet
from pydantic import ValidationError


@pytest.fixture
def store(tmp_path):
    return StateStore(tmp_path / "private", FernetCipher(Fernet.generate_key().decode()))


def bootstrap(store, **updates):
    path = store.directory / "bootstrap.json"
    value = {
        "origin": "http://localhost:8080",
        "installation_id": str(uuid4()),
        "invite_id": str(uuid4()),
        "invite_token": "bi_" + "X" * 43,
        "expires_at": (datetime.now(UTC) + timedelta(minutes=5)).isoformat(),
    } | updates
    path.write_text(json.dumps(value), encoding="utf-8")
    check_private(path, protect=True)
    return path, value


def test_bootstrap_removed_and_identity_encrypted_before_restart(store):
    path, value = bootstrap(store)
    state = store.initialize(path)
    assert not path.exists()
    assert state.host_credential.get_secret_value().startswith("bh_")
    assert len(state.host_credential.get_secret_value()) == 46
    encrypted = store.path.read_bytes()
    assert value["invite_token"].encode() not in encrypted
    assert state.host_credential.get_secret_value().encode() not in encrypted
    assert "credentials.bin" == store.path.name
    assert store.initialize(None) == state
    assert state.host_credential.get_secret_value() not in repr(state)


def test_cipher_failure_keeps_bootstrap_and_does_not_write_plaintext(store, monkeypatch):
    path, _ = bootstrap(store)

    def broken(value):
        raise HostError("credential_protection_unavailable")

    monkeypatch.setattr(store.cipher, "encrypt", broken)
    with pytest.raises(HostError, match="credential_protection_unavailable"):
        store.initialize(path)
    assert path.exists() and not store.path.exists()


@pytest.mark.parametrize(
    "origin",
    [
        "http://api.example.com",
        "http://192.168.1.1:8080",
        "file:///tmp/token",
        "http://user:pass@localhost:8080",
        "http://localhost:8080/?key=x",
        "http://localhost:8080/#key=x",
        "http://localhost:8080/path",
        "http://localhost:0",
        "http://localhost:65536",
        "http://LOCALHOST:8080",
        "http://localhost:8080\n",
        "http://localhost.evil:8080",
        "http://127.0.0.2:8080",
    ],
)
def test_remote_ambiguous_or_secret_bearing_origin_rejected(origin):
    with pytest.raises(ValidationError):
        Bootstrap(
            origin=origin,
            installation_id=uuid4(),
            invite_id=uuid4(),
            invite_token="bi_" + "X" * 43,
            expires_at=datetime.now(UTC) + timedelta(minutes=5),
        )


def test_expired_invite_rejected_without_network_or_state(store):
    path, _ = bootstrap(store, expires_at=(datetime.now(UTC) - timedelta(seconds=1)).isoformat())
    with pytest.raises(HostError, match="bootstrap_invalid"):
        store.initialize(path)
    assert not store.path.exists()


def test_other_installation_is_not_silently_replaced(store):
    first, _ = bootstrap(store)
    original = store.initialize(first)
    second, _ = bootstrap(store)
    with pytest.raises(HostError, match="installation_conflict"):
        store.initialize(second, new_pair=True)
    assert store.load() == original


def test_revoked_requires_explicit_new_pair_and_fresh_invite(store):
    path, _ = bootstrap(store)
    original = store.initialize(path)
    original = original.model_copy(update={"revoked": True})
    store.save(original)
    fresh, _ = bootstrap(store, installation_id=str(original.installation_id))
    with pytest.raises(HostError, match="host_revoked"):
        store.initialize(fresh)
    replacement = store.initialize(fresh, new_pair=True)
    assert replacement.host_id != original.host_id
    assert replacement.host_credential != original.host_credential
    assert not replacement.revoked
    with pytest.raises(HostError, match="bootstrap_required"):
        store.initialize(None, new_pair=True)


def test_corrupt_state_is_not_overwritten(store):
    store.path.write_bytes(b"corrupto")
    check_private(store.path, protect=True)
    with pytest.raises(HostError, match="credential_protection_unavailable"):
        store.load()
    assert store.path.read_bytes() == b"corrupto"


def test_unprotected_bootstrap_file_fails_closed(store):
    path, _ = bootstrap(store)
    if os.name == "nt":
        # Cria arquivo que herdou a ACL, mas não possui DACL protegida própria.
        path.unlink()
        path.write_text("{}")
    else:
        path.chmod(0o644)
    with pytest.raises(HostError, match="private_path_required"):
        store.initialize(path)


def test_unprotected_parent_is_not_fixed_implicitly(tmp_path):
    directory = tmp_path / "unsafe"
    directory.mkdir()
    if os.name != "nt":
        directory.chmod(0o755)
    with pytest.raises(HostError, match="private_path_required"):
        StateStore(directory, FernetCipher(Fernet.generate_key().decode()))


def test_symbolic_state_directory_rejected(tmp_path):
    actual = tmp_path / "actual"
    actual.mkdir()
    link = tmp_path / "link"
    try:
        link.symlink_to(actual, target_is_directory=True)
    except OSError:
        pytest.skip("Conta sem permissão para criar symlink.")
    with pytest.raises(HostError, match="private_path_required"):
        StateStore(link, FernetCipher(Fernet.generate_key().decode()))


def test_same_state_directory_rejects_second_instance(store):
    with store.lock():
        with pytest.raises(HostError, match="host_already_running"):
            with store.lock():
                pytest.fail("Segunda instância obteve trava")
    with store.lock():
        pass


@pytest.mark.skipif(os.name != "nt", reason="DPAPI nativa Windows")
def test_dpapi_current_user_roundtrip_and_private_acl(tmp_path):
    cipher = DPAPICipher()
    raw = b"credencial nativa de teste"
    protected = cipher.encrypt(raw)
    assert protected != raw and cipher.decrypt(protected) == raw
    directory = tmp_path / "dpapi"
    store = StateStore(directory, cipher)
    path, _ = bootstrap(store)
    state = store.initialize(path)
    assert store.load() == state
    check_private(directory, directory=True)
    check_private(store.path)
    with pytest.raises(HostError, match="credential_protection_unavailable"):
        cipher.decrypt(b"sem dpapi")


@pytest.mark.skipif(os.name == "nt", reason="Proteção POSIX exige chave externa")
def test_posix_external_key_required(monkeypatch):
    monkeypatch.delenv("BEES_HOST_STATE_KEY", raising=False)
    with pytest.raises(HostError, match="credential_protection_unavailable"):
        native_cipher()
    monkeypatch.setenv("BEES_HOST_STATE_KEY", Fernet.generate_key().decode())
    assert native_cipher().decrypt(native_cipher().encrypt(b"teste")) == b"teste"
