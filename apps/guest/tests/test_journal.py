import json
import os
import sqlite3
import subprocess
import sys
from dataclasses import replace
from uuid import uuid4

import pytest

from bees_guest import security
from bees_guest.errors import GuestError
from bees_guest.journal import Journal
from bees_guest.protocol import Binding
from bees_guest.security import Identity

STATUS = {"desktop_session": False, "chromium": True, "writer": True, "workspace": True}


@pytest.fixture
def state(tmp_path, monkeypatch):
    # Laboratório transportável Windows/Linux; produção exige árvore root privada.
    monkeypatch.setattr(security, "check_private", lambda *_args, **_kwargs: None)
    identity = Identity(
        Binding(*(str(uuid4()) for _ in range(4)), generation=1), str(uuid4()), "a" * 64, 2761
    )
    journal = Journal.initialize(tmp_path / "receipts.sqlite3", identity)
    nonce = str(uuid4())
    journal.activate(nonce)
    request = identity.binding.fields() | {
        "protocol": 1,
        "type": "health_request",
        "guest_id": identity.guest_id,
        "request_id": str(uuid4()),
        "nonce": str(uuid4()),
        "session_nonce": nonce,
    }
    yield journal, request, identity
    journal.close()


def test_replay_is_old_observation_and_monotonic_new_request(state):
    journal, request, _ = state
    first = journal.health(request, session_nonce=request["session_nonce"], probe=lambda: STATUS)
    replay = journal.health(
        request,
        session_nonce=request["session_nonce"],
        probe=lambda: pytest.fail("replay refreshed"),
    )
    assert first["sequence"] == replay["sequence"] == 1
    assert first["cached"] is False and replay == first | {"cached": True}
    second = journal.health(
        request | {"request_id": str(uuid4()), "nonce": str(uuid4())},
        session_nonce=request["session_nonce"],
        probe=lambda: STATUS,
    )
    assert second["sequence"] == 2 and not second["cached"]
    assert journal.connection.execute("PRAGMA journal_mode").fetchone() == ("delete",)
    assert journal.connection.execute("PRAGMA synchronous").fetchone() == (2,)


def test_request_conflict_nonce_reuse_and_invalid_probe_fail_closed(state):
    journal, request, _ = state
    first = journal.health(request, session_nonce=request["session_nonce"], probe=lambda: STATUS)
    assert first["type"] == "health"
    for changed in (request | {"nonce": str(uuid4())}, request | {"request_id": str(uuid4())}):
        outcome = journal.health(
            changed,
            session_nonce=request["session_nonce"],
            probe=lambda: pytest.fail("conflict ran probe"),
        )
        assert outcome["code"] == "request_conflict"
    unknown = request | {"request_id": str(uuid4()), "nonce": str(uuid4())}
    outcome = journal.health(
        unknown, session_nonce=request["session_nonce"], probe=lambda: STATUS | {"key": "secret"}
    )
    assert outcome["code"] == "request_unknown"
    assert "secret" not in json.dumps(outcome)
    assert (
        journal.health(
            unknown,
            session_nonce=request["session_nonce"],
            probe=lambda: pytest.fail("unknown repeated"),
        )
        == outcome
    )


def test_restart_replay_and_generation_identity_cannot_change_remotely(state):
    journal, request, identity = state
    journal.health(request, session_nonce=request["session_nonce"], probe=lambda: STATUS)
    reopened = Journal(journal.path, identity)
    try:
        result = reopened.health(
            request,
            session_nonce=request["session_nonce"],
            probe=lambda: pytest.fail("restart refreshed"),
        )
        assert result["cached"] is True and result["sequence"] == 1
        for binding in (
            replace(identity.binding, generation=2),
            replace(identity.binding, vm_id=str(uuid4())),
        ):
            with pytest.raises(GuestError, match="binding_mismatch"):
                Journal(journal.path, replace(identity, binding=binding))
    finally:
        reopened.close()


def test_cross_connection_fence_before_and_during_probe(state):
    journal, request, identity = state
    other = Journal(journal.path, identity)
    try:

        def probe():
            other.activate(str(uuid4()))
            return STATUS

        result = journal.health(request, session_nonce=request["session_nonce"], probe=probe)
        assert result["code"] == "session_fenced"
        result = journal.health(
            request, session_nonce=request["session_nonce"], probe=lambda: pytest.fail("fenced ran")
        )
        assert result["code"] == "session_fenced"
        with pytest.raises(GuestError, match="session_fenced"):
            other.activate(request["session_nonce"])
        assert journal.connection.execute("SELECT status FROM receipts").fetchone() == ("unknown",)
    finally:
        other.close()


def test_crash_real_after_prepare_restart_does_not_repeat(state):
    journal, request, identity = state
    script = """
import os,json
from bees_guest import security
from bees_guest.protocol import Binding
from bees_guest.security import Identity
from bees_guest.journal import Journal
security.check_private=lambda *args,**kwargs:None
config=json.loads(os.environ["GUEST_TEST_IDENTITY"])
identity=Identity(Binding(**config["binding"]),config["guest_id"],"a"*64,2761)
journal=Journal(os.environ["GUEST_TEST_DB"],identity)
request=json.loads(os.environ["GUEST_TEST_REQUEST"])
journal.health(request,session_nonce=request["session_nonce"],probe=lambda:os._exit(23))
"""
    env = os.environ | {
        "PYTHONPATH": str(__import__("pathlib").Path(__file__).parents[1] / "src"),
        "GUEST_TEST_DB": str(journal.path),
        "GUEST_TEST_REQUEST": json.dumps(request),
        "GUEST_TEST_IDENTITY": json.dumps(
            {"binding": identity.binding.fields(), "guest_id": identity.guest_id}
        ),
    }
    child = subprocess.run([sys.executable, "-c", script], env=env, capture_output=True, timeout=10)
    assert child.returncode == 23, child.stderr.decode()
    assert journal.connection.execute("SELECT status FROM receipts").fetchone() == ("prepared",)
    reopened = Journal(journal.path, identity)
    try:
        result = reopened.health(
            request,
            session_nonce=request["session_nonce"],
            probe=lambda: pytest.fail("crash repeated probe"),
        )
        assert result["code"] == "request_unknown"
        assert reopened.connection.execute("SELECT status FROM receipts").fetchone() == ("unknown",)
    finally:
        reopened.close()


def test_unrecognized_database_is_preserved(tmp_path, monkeypatch, state):
    monkeypatch.setattr(security, "check_private", lambda *_args, **_kwargs: None)
    path = tmp_path / "foreign.sqlite3"
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE preserved(id INTEGER)")
    connection.close()
    with pytest.raises(GuestError, match="journal_missing"):
        Journal(path, state[2])
    with pytest.raises(GuestError, match="journal_already_present"):
        Journal.initialize(path, state[2])
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT name FROM sqlite_master").fetchall() == [("preserved",)]


@pytest.mark.parametrize("lost", ["database", "marker", "both"])
def test_lost_state_fails_without_recreating(tmp_path, state, lost):
    path = tmp_path / "loss.sqlite3"
    Journal.initialize(path, state[2]).close()
    marker = Journal.marker_path(path)
    if lost in ("database", "both"):
        path.unlink()
    if lost in ("marker", "both"):
        marker.unlink()
    with pytest.raises(GuestError, match="journal_missing"):
        Journal(path, state[2])
    assert path.exists() == (lost == "marker")
    assert marker.exists() == (lost == "database")
    # O operador não pode resetar uma instalação parcial como se fosse primeira criação.
    if lost != "both":
        with pytest.raises(GuestError, match="journal_already_present"):
            Journal.initialize(path, state[2])


def test_partial_initialize_and_corrupt_database_are_closed(tmp_path, state):
    path = tmp_path / "partial.sqlite3"
    Journal.marker_path(path).write_bytes(b"partial creation")
    with pytest.raises(GuestError, match="journal_already_present"):
        Journal.initialize(path, state[2])
    assert not path.exists()
    corrupt = tmp_path / "corrupt.sqlite3"
    Journal.initialize(corrupt, state[2]).close()
    corrupt.write_bytes(b"private payload should never escape into errors")
    with pytest.raises(GuestError) as failure:
        Journal(corrupt, state[2])
    assert failure.value.code == "journal_invalid"
    assert str(failure.value) == "journal_invalid"
    assert failure.value.__suppress_context__


def test_marker_cannot_be_swapped_between_databases(tmp_path, state):
    first = tmp_path / "one.sqlite3"
    second = tmp_path / "two.sqlite3"
    for path in (first, second):
        Journal.initialize(path, state[2]).close()
    Journal.marker_path(first).write_bytes(Journal.marker_path(second).read_bytes())
    with pytest.raises(GuestError, match="binding_mismatch"):
        Journal(first, state[2])


def test_database_lost_between_private_check_and_open_is_not_recreated(state, monkeypatch):
    journal, _, identity = state
    path = journal.path
    journal.close()
    connect = sqlite3.connect

    def disappear_before_open(*args, **kwargs):
        path.unlink()
        return connect(*args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", disappear_before_open)
    with pytest.raises(GuestError, match="journal_unavailable"):
        Journal(path, identity)
    assert not path.exists()
    assert Journal.marker_path(path).exists()


def test_session_fencing_from_real_second_process(state):
    journal, request, identity = state
    script = """
import json,os
from bees_guest import security
from bees_guest.protocol import Binding
from bees_guest.security import Identity
from bees_guest.journal import Journal
security.check_private=lambda *args,**kwargs:None
config=json.loads(os.environ["GUEST_TEST_IDENTITY"])
identity=Identity(Binding(**config["binding"]),config["guest_id"],"a"*64,2761)
journal=Journal(os.environ["GUEST_TEST_DB"],identity)
journal.activate(os.environ["GUEST_TEST_NEW_SESSION"])
journal.close()
"""
    env = os.environ | {
        "PYTHONPATH": str(__import__("pathlib").Path(__file__).parents[1] / "src"),
        "GUEST_TEST_DB": str(journal.path),
        "GUEST_TEST_NEW_SESSION": str(uuid4()),
        "GUEST_TEST_IDENTITY": json.dumps(
            {
                "binding": identity.binding.fields(),
                "guest_id": identity.guest_id,
            }
        ),
    }
    child = subprocess.run([sys.executable, "-c", script], env=env, capture_output=True, timeout=10)
    assert child.returncode == 0, child.stderr.decode()
    result = journal.health(
        request,
        session_nonce=request["session_nonce"],
        probe=lambda: pytest.fail("stale probed"),
    )
    assert result["code"] == "session_fenced"
