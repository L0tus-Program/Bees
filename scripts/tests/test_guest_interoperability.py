"""Host e guest reais sobre TLS loopback; não demonstra bus Hyper-V ou VM.

A guarda Linux/root do journal é substituída somente nesta fixture portátil.
Os testes POSIX do guest validam os arquivos privados separadamente.
"""

import importlib.util
import ssl
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import pytest
from bees_host.guest_bridge.protocol import BridgeError
from bees_host.guest_bridge.session import HealthObservation, SessionFence

from bees_guest import security
from bees_guest.errors import GuestError
from bees_guest.journal import Journal
from bees_guest.protocol import Binding
from bees_guest.runtime import GuestClient
from bees_guest.security import Identity

FIXTURE_PATH = Path(__file__).resolve().parents[2] / "apps/host/tests/test_guest_bridge_tls.py"
spec = importlib.util.spec_from_file_location("bees_bridge_tls_fixture", FIXTURE_PATH)
assert spec is not None and spec.loader is not None
tls_fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tls_fixture)


@pytest.fixture
def peers(tmp_path, monkeypatch):
    monkeypatch.setattr(security, "check_private", lambda *args, **kwargs: None)
    with tls_fixture.private_bridge_directory() as directory:
        material = tls_fixture.make_bridge_material(directory / "host")
        host, client_context, pins = material
        client_context.verify_flags |= ssl.VERIFY_X509_STRICT
        guest = Identity(Binding(**host.binding.fields()), host.guest_id, pins["host"], 2761)
        yield material, guest, tmp_path / "guest.sqlite3"


def test_actual_guest_and_host_tls_preserve_sequence_after_restart(peers):
    material, identity, path = peers
    host = material[0]
    fence = SessionFence(host.binding, host.guest_id)
    calls = []

    def probe():
        calls.append(True)
        return dict(desktop_session=False, chromium=True, writer=True, workspace=True)

    Journal.initialize(path, identity).close()
    for expected_sequence in (1, 2):
        journal = Journal(path, identity)
        try:

            def guest(stream, _host, journal=journal):
                assert GuestClient(identity, journal, probe=probe).run(stream, once=True)

            result = tls_fixture.exchange(material, guest, fence=fence)
        finally:
            journal.close()
        assert isinstance(result, HealthObservation)
        assert result.sequence == expected_sequence
        assert result.cached is False
        assert result.observed_monotonic is not None
        assert (result.desktop_session, result.chromium, result.writer, result.workspace) == (
            False,
            True,
            True,
            True,
        )
    assert len(calls) == 2


@pytest.mark.parametrize("change", ["pin", "generation", "job", "guest"])
def test_actual_guest_rejects_wrong_tls_binding_before_probe(peers, change):
    material, identity, path = peers
    if change == "pin":
        identity = replace(identity, relay_cert_sha256="0" * 64)
    elif change == "guest":
        identity = replace(identity, guest_id=str(uuid4()))
    else:
        updates = {"generation": 2} if change == "generation" else {"job_id": str(uuid4())}
        identity = replace(identity, binding=replace(identity.binding, **updates))
    journal = Journal.initialize(path, identity)
    calls = []
    try:

        def guest(stream, _host):
            with pytest.raises(GuestError, match="peer_identity_invalid"):
                GuestClient(identity, journal, probe=lambda: calls.append(True)).run(stream)

        result = tls_fixture.exchange(material, guest)
    finally:
        journal.close()
    assert isinstance(result, BridgeError)
    assert not calls


def test_actual_guest_probe_failure_reaches_host_without_retry(peers):
    material, identity, path = peers
    journal = Journal.initialize(path, identity)
    calls = []

    def failing_probe():
        calls.append(True)
        raise RuntimeError("private fixture detail")

    try:

        def guest(stream, _host):
            assert (
                GuestClient(identity, journal, probe=failing_probe).run(stream, once=True) is False
            )

        result = tls_fixture.exchange(material, guest)
        assert journal.connection.execute("SELECT status FROM receipts").fetchall() == [
            ("unknown",)
        ]
    finally:
        journal.close()
    assert isinstance(result, BridgeError)
    assert str(result) == "bridge_request_unknown"
    assert len(calls) == 1
    assert "private fixture detail" not in str(result)
