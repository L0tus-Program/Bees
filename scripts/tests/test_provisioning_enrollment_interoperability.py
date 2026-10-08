"""Inscrição cifrada/core/API reais e somente session; nenhum claim ou hardware."""

import json
import os
import secrets
import subprocess
import sys
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from bees_host.provisioning.contracts import ProvisionError
from bees_host.provisioning.enrollment import EnrollmentBinding, EnrollmentStore
from bees_host.provisioning.http_authority import HTTPAuthority
from bees_host.provisioning.journal import create
from bees_host.security import DPAPICipher, FernetCipher, check_private
from cryptography.fernet import Fernet
from test_provisioning_http_interoperability import api_process

from bees_core.provisioning import ProvisioningError, ProvisioningService
from bees_core.security.hosts import HostService
from bees_core.security.identity import IdentityService
from bees_core.storage.database import Database


@pytest.fixture
def registration(tmp_path):
    database = Database(tmp_path / "bees.sqlite3")
    database.initialize()
    identity = IdentityService(database)
    identity.setup(identity.issue_bootstrap(), "Inscrição de teste", "senha descartável 123456")
    hosts = HostService(database)
    invite = hosts.issue_invite(uuid4())
    host = hosts.pair(
        dict(
            installation_id=invite.installation_id,
            invite_token=invite.invite_token,
            host_id=uuid4(),
            host_credential="bh_" + secrets.token_urlsafe(32),
            client_request_id=uuid4(),
        )
    )
    host = hosts.confirm(
        UUID(host["host_id"]),
        dict(
            expected_revision=host["revision"],
            fingerprint=host["fingerprint"],
            client_request_id=uuid4(),
        ),
    )
    core = ProvisioningService(database)
    request_id = uuid4()
    issued = core.issue_provisioner(
        UUID(host["host_id"]),
        dict(expected_host_revision=host["revision"], client_request_id=request_id),
    )
    with tempfile.TemporaryDirectory(prefix="bees-enrollment-interop-", dir=Path.home()) as temp:
        private_root = Path(temp)
        check_private(private_root, directory=True, protect=True)
        cipher = DPAPICipher() if os.name == "nt" else FernetCipher(Fernet.generate_key().decode())
        yield core, host, issued, request_id, private_root, cipher


def prepare_bootstrap(registration, origin):
    _, _, issued, request_id, private_root, _ = registration
    expected = EnrollmentBinding(
        origin=origin,
        installation_id=issued.installation_id,
        host_id=issued.host_id,
        provisioner_id=issued.provisioner_id,
        issue_request_id=request_id,
    )
    bootstrap = private_root / "bootstrap.json"
    data = dict(
        format=1,
        **expected.model_dump(mode="json"),
        provisioner_credential=issued.credential.get_secret_value(),
        expires_at=(datetime.now(UTC) + timedelta(minutes=10)).isoformat(),
    )
    create(bootstrap, json.dumps(data, allow_nan=False).encode())
    return expected, bootstrap


def authority_from(store):
    state = store.load()
    return HTTPAuthority(
        state.origin,
        state.provisioner_credential,
        installation_id=state.installation_id,
        host_id=state.host_id,
        provisioner_id=state.provisioner_id,
    )


def no_hardware(core):
    with core.database.transaction(write=False) as connection:
        for table in ("provisioning_claims", "provisioning_effects", "host_jobs"):
            assert connection.execute("SELECT count(*) FROM " + table).get == 0


def test_private_issue_enroll_reload_session_and_revoke_without_hardware(registration):
    core, host, issued, _, private_root, cipher = registration
    with api_process(core.database.path.parent) as (origin, _):
        expected, bootstrap = prepare_bootstrap(registration, origin)
        store = EnrollmentStore.initialize(
            private_root / "registration", bootstrap, binding=expected, cipher=cipher
        )
        assert not bootstrap.exists()
        public = store.check_only()
        assert public.configured_local is True and public.provisioner_id == issued.provisioner_id
        assert issued.credential.get_secret_value() not in public.model_dump_json()
        with authority_from(store) as authority:
            assert authority.session().status == "active"
        reopened = EnrollmentStore.open(store.directory, binding=expected, cipher=cipher)
        assert reopened.check_only() == public
        no_hardware(core)
        revoked = core.revoke_provisioner(
            issued.provisioner_id,
            dict(expected_revision=1, client_request_id=uuid4()),
            host_id=UUID(host["host_id"]),
        )
        assert revoked["status"] == "revoked"
        # Integridade offline não afirma que a credencial ainda é aceita pelo serviço.
        assert reopened.check_only() == public
        with authority_from(reopened) as authority:
            with pytest.raises(ProvisionError, match="credentials_invalid"):
                authority.session()
        no_hardware(core)


def test_failed_delivery_keeps_private_issue_and_partial_state_without_reissue(
    registration, monkeypatch
):
    core, host, issued, request_id, private_root, cipher = registration
    expected, bootstrap = prepare_bootstrap(registration, "http://localhost:8080")
    original_unlink = Path.unlink

    def unavailable(path, *args, **kwargs):
        if path == bootstrap:
            raise PermissionError("fixture")
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", unavailable)
    directory = private_root / "partial"
    with pytest.raises(ProvisionError):
        EnrollmentStore.initialize(directory, bootstrap, binding=expected, cipher=cipher)
    assert bootstrap.exists() and directory.exists()
    with pytest.raises(ProvisionError):
        EnrollmentStore.open(directory, binding=expected, cipher=cipher)
    with pytest.raises(ProvisioningError, match="credential_already_issued"):
        core.issue_provisioner(
            UUID(host["host_id"]),
            dict(expected_host_revision=host["revision"], client_request_id=request_id),
        )
    assert core.list_provisioners(issued.host_id)[0]["status"] == "active"
    no_hardware(core)
    # A confirmação humana de revogação continua possível após perda da entrega.
    core.revoke_provisioner(
        issued.provisioner_id,
        dict(expected_revision=1, client_request_id=uuid4()),
        host_id=issued.host_id,
    )
    assert core.list_provisioners(issued.host_id)[0]["status"] == "revoked"
    no_hardware(core)


def test_wrong_installation_cannot_consume_operator_bootstrap(registration):
    core, _, _, _, private_root, cipher = registration
    expected, bootstrap = prepare_bootstrap(registration, "http://localhost:8080")
    wrong = expected.model_copy(update={"installation_id": uuid4()})
    directory = private_root / "wrong"
    with pytest.raises(ProvisionError):
        EnrollmentStore.initialize(directory, bootstrap, binding=wrong, cipher=cipher)
    assert bootstrap.exists() and not directory.exists()
    no_hardware(core)


def test_offline_check_does_not_resolve_dns_connect_or_change_files(registration, monkeypatch):
    import socket

    core, _, _, _, private_root, cipher = registration
    expected, bootstrap = prepare_bootstrap(registration, "http://localhost:8080")

    def forbidden(*args, **kwargs):
        raise AssertionError("Inscrição offline tentou fazer rede.")

    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    store = EnrollmentStore.initialize(
        private_root / "offline", bootstrap, binding=expected, cipher=cipher
    )
    before = {p.name: p.read_bytes() for p in store.directory.iterdir()}
    assert store.check_only().configured_local is True
    reopened = EnrollmentStore.open(store.directory, binding=expected, cipher=cipher)
    assert reopened.check_only() == store.check_only()
    assert {p.name: p.read_bytes() for p in store.directory.iterdir()} == before
    no_hardware(core)


def test_real_process_crash_after_bootstrap_delete_never_opens_partial_state(registration):
    core, _, _, _, private_root, _ = registration
    expected, bootstrap = prepare_bootstrap(registration, "http://localhost:8080")
    directory = private_root / "crashed"
    environment = os.environ.copy()
    if os.name == "nt":
        cipher = DPAPICipher()
    else:
        # Chave externa própria desta fixture, jamais argv ou saída do processo.
        key = Fernet.generate_key().decode()
        environment["BEES_HOST_STATE_KEY"] = key
        cipher = FernetCipher(key)
    child = """
import os, sys
from pathlib import Path
from bees_host.provisioning import enrollment
binding = enrollment.EnrollmentBinding.model_validate_json(sys.argv[3])
create = enrollment.create
def crash_after_seal(path, data):
    create(path, data)
    if path.name == 'credentials.bin':
        os._exit(23)
enrollment.create = crash_after_seal
enrollment.EnrollmentStore.initialize(Path(sys.argv[1]), Path(sys.argv[2]), binding=binding)
sys.exit(24)
"""
    result = subprocess.run(
        [sys.executable, "-c", child, str(directory), str(bootstrap), expected.model_dump_json()],
        env=environment,
        capture_output=True,
        timeout=15,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        check=False,
    )
    assert result.returncode == 23 and not result.stdout and not result.stderr
    assert not bootstrap.exists()
    evidence = {p.name: p.read_bytes() for p in directory.iterdir()}
    assert "staged.bin" in evidence and "credentials.bin" in evidence
    with pytest.raises(ProvisionError):
        EnrollmentStore.open(directory, binding=expected, cipher=cipher)
    with pytest.raises(ProvisionError):
        EnrollmentStore.initialize(directory, bootstrap, binding=expected, cipher=cipher)
    assert {p.name: p.read_bytes() for p in directory.iterdir()} == evidence
    no_hardware(core)
