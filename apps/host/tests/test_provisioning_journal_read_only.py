"""Reader de formato1 com SQLite/locks/crash reais; hardware permanece falso."""

import json
import sqlite3
import subprocess
import sys
import threading
from contextlib import closing
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from bees_host.provisioning.contracts import OPERATIONS, ProvisionError, ReceiptResult
from bees_host.provisioning.journal import Journal, create
from bees_host.provisioning.runner import Runner
from test_guest_bridge_tls import private_bridge_directory
from test_provisioning_runner import FakeAuthority, FakeBackend, claim_fixture
from test_provisioning_supervisor_read_only import fingerprint


@pytest.fixture
def fixture():
    with private_bridge_directory() as directory:
        claim = claim_fixture()
        journal = Journal.initialize(directory / "plan", claim)
        authority, backend = FakeAuthority(claim), FakeBackend(claim)
        runner = Runner(
            journal,
            authority,
            backend,
            SimpleNamespace(verify=lambda plan: directory / "fixture.iso"),
        )
        try:
            yield journal, runner, authority, backend
        finally:
            journal.close()


def phase(journal, status):
    with journal.lock():
        row = journal.prepare("create_vhd")
        if status == "prepared":
            return
        journal.transition("create_vhd", "prepared", "authority_started")
        if status == "authority_started":
            return
        journal.transition(
            "create_vhd", "authority_started", "authorized", effect_request_id=uuid4()
        )
        if status == "authorized":
            return
        journal.transition(
            "create_vhd", "authorized", "dispatch_started", pid=123, start_ticks=123456789
        )
        if status == "dispatch_started":
            return
        journal.transition(
            "create_vhd",
            "dispatch_started",
            "result_observed",
            result=ReceiptResult(vm_id=None, verified=True),
        )
        if status == "result_observed":
            return
        assert status == "confirmed" and row["request_id"]
        journal.transition("create_vhd", "result_observed", "confirmed")


def test_mode_ro_schema_marker_empty_history_native_lock_and_no_file_changes(fixture, monkeypatch):
    journal, _, _, _ = fixture
    before = fingerprint(journal.directory)
    import bees_host.provisioning.journal as module

    connect, uris = module.sqlite3.connect, []

    def observed(target, **kwargs):
        uris.append((target, kwargs))
        return connect(target, **kwargs)

    monkeypatch.setattr(module.sqlite3, "connect", observed)
    reader = Journal.open_read_only(journal.directory)
    try:
        assert len(uris) == 1 and uris[0][0].endswith("?mode=ro")
        assert "immutable" not in uris[0][0] and uris[0][1]["uri"] is True
        assert reader.journal_id == journal.journal_id and reader.claim == journal.claim
        with reader.lock():
            reader.assert_locked()
            assert reader.operations() == {}
            with pytest.raises(sqlite3.OperationalError, match="readonly"):
                reader.connection.execute("UPDATE binding SET journal_id='changed'")
            with pytest.raises(ProvisionError, match="provision_lock_required"):
                reader.close()
    finally:
        reader.close()
    assert fingerprint(journal.directory) == before


@pytest.mark.parametrize(
    "status",
    [
        "prepared",
        "authority_started",
        "authorized",
        "dispatch_started",
        "result_observed",
        "confirmed",
    ],
)
@pytest.mark.parametrize("unknown", [False, True])
def test_phase_tuples_and_unknown_evidence_are_read_without_repair_or_retry(
    fixture, status, unknown
):
    journal, _, _, _ = fixture
    phase(journal, status)
    if unknown:
        with journal.lock():
            journal.unknown("create_vhd")
    expected = journal.operations()
    before = fingerprint(journal.directory)
    reader = Journal.open_read_only(journal.directory)
    try:
        with reader.lock():
            assert reader.operations() == expected
            assert reader.claim == journal.claim
    finally:
        reader.close()
    assert fingerprint(journal.directory) == before


def test_complete_real_runner_sequence_has_closed_receipts_and_evolving_claim(fixture):
    journal, runner, _, backend = fixture
    for _ in OPERATIONS:
        runner.execute_next()
    expected = journal.operations()
    before = fingerprint(journal.directory)
    reader = Journal.open_read_only(journal.directory)
    try:
        with reader.lock():
            assert reader.claim.status == "confirmed"
            assert reader.operations() == expected
            assert all(row["status"] == "confirmed" for row in expected.values())
    finally:
        reader.close()
    assert backend.effects == list(OPERATIONS) and fingerprint(journal.directory) == before


@pytest.mark.parametrize("last_status", ["result_observed", "unknown"])
def test_canonical_claim_confirmed_with_local_last_receipt_unresolved_is_valid_crash_window(
    fixture, last_status
):
    journal, runner, _, _ = fixture
    for _ in OPERATIONS:
        runner.execute_next()
    with closing(sqlite3.connect(journal.path)) as connection:
        connection.execute(
            "UPDATE operations SET status=? WHERE operation='verify'", (last_status,)
        )
        connection.commit()
    before = fingerprint(journal.directory)
    reader = Journal.open_read_only(journal.directory)
    try:
        with reader.lock():
            assert reader.claim.status == "confirmed"
            assert reader.operations()["verify"]["status"] == last_status
    finally:
        reader.close()
    assert fingerprint(journal.directory) == before


@pytest.mark.parametrize("status", ["claimed", "dispatch_started", "outcome_unknown", "aborted"])
def test_expired_claim_and_terminal_unknown_are_historical_data_not_authorization(fixture, status):
    journal, _, _, _ = fixture
    phase(journal, "dispatch_started")
    with journal.lock():
        journal.unknown("create_vhd")
        journal.update_claim(
            journal.claim.model_copy(
                update={
                    "status": status,
                    "lease_expires_at": datetime.now(UTC) - timedelta(seconds=1),
                }
            )
        )
    before = fingerprint(journal.directory)
    reader = Journal.open_read_only(journal.directory)
    try:
        with reader.lock():
            assert reader.claim.status == status and reader.claim.lease_expires_at < datetime.now(
                UTC
            )
            assert reader.operations()["create_vhd"]["status"] == "unknown"
    finally:
        reader.close()
    assert fingerprint(journal.directory) == before


def test_claim_is_current_on_lock_and_advances_without_changing_immutable_binding(fixture):
    journal, _, _, _ = fixture
    reader = Journal.open_read_only(journal.directory)
    try:
        old = reader.claim
        with journal.lock():
            journal.update_claim(
                old.model_copy(
                    update={
                        "revision": old.revision + 1,
                        "lease_expires_at": old.lease_expires_at + timedelta(seconds=30),
                        "status": "dispatch_started",
                    }
                )
            )
        before = fingerprint(journal.directory)
        with reader.lock():
            assert reader.claim == journal.claim and reader.claim.revision == old.revision + 1
            assert reader.operations() == {}
        assert fingerprint(journal.directory) == before
    finally:
        reader.close()


def test_mutators_and_transaction_refuse_read_only_before_sql(fixture):
    journal, _, _, _ = fixture
    before = fingerprint(journal.directory)
    reader = Journal.open_read_only(journal.directory)
    try:
        with reader.lock():
            trace = []
            reader.connection.set_trace_callback(trace.append)
            calls = [
                lambda: reader.prepare("create_vhd"),
                lambda: reader.unknown("create_vhd"),
                lambda: reader.transition("create_vhd", "prepared", "unknown"),
                lambda: reader.update_claim(journal.claim),
            ]
            for call in calls:
                with pytest.raises(ProvisionError, match="^provision_read_only$"):
                    call()
            with pytest.raises(ProvisionError, match="^provision_read_only$"):
                with reader.transaction():
                    pytest.fail("transação readonly")
            assert trace == [] and not reader.connection.in_transaction
    finally:
        reader.close()
    assert fingerprint(journal.directory) == before


@pytest.mark.parametrize(
    "extra",
    [
        "journal.sqlite3-journal",
        "journal.sqlite3-wal",
        "journal.sqlite3-shm",
        "staged.json",
        "foreign.txt",
    ],
)
def test_partial_or_extra_files_refused_before_sqlite_and_preserved(fixture, monkeypatch, extra):
    journal, _, _, _ = fixture
    create(journal.directory / extra, b"private partial evidence")
    before = fingerprint(journal.directory)
    import bees_host.provisioning.journal as module

    def forbidden(*args, **kwargs):
        pytest.fail("SQLite abriu conjunto parcial")

    monkeypatch.setattr(module.sqlite3, "connect", forbidden)
    with pytest.raises(ProvisionError, match="provision_state_invalid"):
        Journal.open_read_only(journal.directory)
    assert fingerprint(journal.directory) == before


@pytest.mark.parametrize("name", ["identity.json", "owner.lock", "journal.sqlite3"])
def test_loss_does_not_recreate_state(fixture, name):
    journal, _, _, _ = fixture
    journal.close()
    (journal.directory / name).unlink()
    before = fingerprint(journal.directory)
    with pytest.raises(ProvisionError, match="provision_state_missing"):
        Journal.open_read_only(journal.directory)
    assert fingerprint(journal.directory) == before


def test_closed_wal_without_sidecars_refused_before_sqlite(fixture, monkeypatch):
    journal, _, _, _ = fixture
    journal.close()
    with closing(sqlite3.connect(journal.path)) as connection:
        assert connection.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
    assert journal.path.read_bytes()[18:20] == b"\x02\x02"
    assert not (journal.directory / "journal.sqlite3-wal").exists()
    assert not (journal.directory / "journal.sqlite3-shm").exists()
    before = fingerprint(journal.directory)
    import bees_host.provisioning.journal as module

    def forbidden(*args, **kwargs):
        pytest.fail("SQLite poderia criar sidecars WAL")

    monkeypatch.setattr(module.sqlite3, "connect", forbidden)
    with pytest.raises(ProvisionError, match="provision_state_invalid"):
        Journal.open_read_only(journal.directory)
    assert fingerprint(journal.directory) == before


def test_private_hotjournal_after_abrupt_crash_is_never_recovered(fixture, monkeypatch):
    journal, _, _, _ = fixture
    directory = journal.directory
    journal.close()
    code = """
import os,sys,sqlite3
from pathlib import Path
from bees_host.security import check_private
directory=Path(sys.argv[1])
connection=sqlite3.connect(directory/'journal.sqlite3',isolation_level=None)
connection.execute('PRAGMA journal_mode=DELETE')
connection.execute('PRAGMA synchronous=FULL')
connection.execute('PRAGMA cache_size=1')
connection.execute('BEGIN IMMEDIATE')
connection.execute("UPDATE binding SET journal_id='uncommitted'")
connection.execute('CREATE TABLE spill(value BLOB)')
for _ in range(32):connection.execute('INSERT INTO spill VALUES(?)',(b'x'*8192,))
sidecar=directory/'journal.sqlite3-journal'
check_private(sidecar,protect=True)
if sidecar.read_bytes()[:8]!=bytes.fromhex('d9d505f920a163d7'):os._exit(9)
os._exit(31)
"""
    process = subprocess.run(
        [sys.executable, "-c", code, str(directory)], capture_output=True, timeout=10, check=False
    )
    assert process.returncode == 31 and not process.stdout and not process.stderr
    before = fingerprint(directory)
    import bees_host.provisioning.journal as module

    def forbidden(*args, **kwargs):
        pytest.fail("SQLite recuperou journal no check-only")

    monkeypatch.setattr(module.sqlite3, "connect", forbidden)
    with pytest.raises(ProvisionError, match="provision_state_invalid"):
        Journal.open_read_only(directory)
    assert fingerprint(directory) == before


@pytest.mark.parametrize(
    "sql",
    [
        "PRAGMA user_version=99",
        "PRAGMA application_id=1",
        "CREATE TABLE foreign_table(value TEXT)",
        "DELETE FROM binding",
        "UPDATE binding SET journal_id='bad'",
        "UPDATE binding SET id=2",
    ],
)
def test_schema_integrity_version_and_binding_fail_closed_without_repair(fixture, sql):
    journal, _, _, _ = fixture
    with closing(sqlite3.connect(journal.path)) as connection:
        connection.execute("PRAGMA ignore_check_constraints=ON")
        connection.execute(sql)
        connection.commit()
    before = fingerprint(journal.directory)
    with pytest.raises(ProvisionError, match="provision_state_invalid"):
        Journal.open_read_only(journal.directory)
    assert fingerprint(journal.directory) == before


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE operations SET status='prepared'",
        "UPDATE operations SET effect_request_id=NULL",
        "UPDATE operations SET pid=NULL",
        "UPDATE operations SET start_ticks=NULL",
        "UPDATE operations SET pid=0",
        "UPDATE operations SET start_ticks=1.5",
        "UPDATE operations SET result_json=NULL",
        "UPDATE operations SET publish_id=request_id",
        "UPDATE operations SET request_id='bad'",
        "UPDATE operations SET operation='verify'",
        "UPDATE operations SET result_json='{}'",
        'UPDATE operations SET result_json=\'{"vm_id":null,"verified":false}\'',
        'UPDATE operations SET result_json=\'{"vm_id":null,"verified":true,"extra":1}\'',
        'UPDATE operations SET result_json=\'{"vm_id":null,"verified":false,"verified":true}\'',
    ],
)
def test_operation_phase_pid_result_uuid_and_prefix_incoherence_rejected(fixture, sql):
    journal, _, _, _ = fixture
    phase(journal, "confirmed")
    with closing(sqlite3.connect(journal.path)) as connection:
        connection.execute("PRAGMA ignore_check_constraints=ON")
        connection.execute(sql)
        connection.commit()
    before = fingerprint(journal.directory)
    with pytest.raises(ProvisionError, match="provision_state_invalid"):
        Journal.open_read_only(journal.directory)
    assert fingerprint(journal.directory) == before


@pytest.mark.parametrize(
    "field,value",
    [
        ("vm_id", str(uuid4())),
        ("cpu_count", 4),
        ("memory_bytes", 2048 * 1024**2),
        ("disk_bytes", 20 * 1024**3),
        ("powered_off", False),
        ("network_none", False),
        ("image_iso_sha256", "a" * 64),
    ],
)
def test_terminal_receipt_resources_and_vm_id_must_match_history_and_plan(fixture, field, value):
    journal, runner, _, _ = fixture
    for _ in OPERATIONS:
        runner.execute_next()
    receipt = json.loads(journal.operations()["verify"]["result_json"])
    receipt[field] = value
    with closing(sqlite3.connect(journal.path)) as connection:
        connection.execute(
            "UPDATE operations SET result_json=? WHERE operation='verify'", (json.dumps(receipt),)
        )
        connection.commit()
    before = fingerprint(journal.directory)
    with pytest.raises(ProvisionError, match="provision_state_invalid"):
        Journal.open_read_only(journal.directory)
    assert fingerprint(journal.directory) == before


def test_native_reader_lock_contends_between_real_processes_and_guards_owner_thread(fixture):
    journal, _, _, _ = fixture
    reader = Journal.open_read_only(journal.directory)
    before = fingerprint(journal.directory)
    code = """
import sys
from pathlib import Path
from bees_host.provisioning.journal import Journal
from bees_host.provisioning.contracts import ProvisionError
reader=Journal.open_read_only(Path(sys.argv[1]))
try:
 with reader.lock():sys.exit(9)
except ProvisionError as error:sys.exit(0 if str(error)=='provision_owner_running' else 10)
finally:reader.close()
"""
    try:
        with reader.lock():
            process = subprocess.run(
                [sys.executable, "-c", code, str(journal.directory)],
                capture_output=True,
                timeout=10,
                check=False,
            )
            assert process.returncode == 0 and not process.stdout and not process.stderr
            failures = []

            def foreign():
                try:
                    reader.assert_locked()
                except BaseException as error:
                    failures.append(error)

            thread = threading.Thread(target=foreign)
            thread.start()
            thread.join(timeout=5)
            assert not thread.is_alive() and len(failures) == 1
            assert (
                type(failures[0]) is ProvisionError
                and str(failures[0]) == "provision_lock_required"
            )
            reader.assert_locked()
    finally:
        reader.close()
    assert fingerprint(journal.directory) == before


@pytest.mark.parametrize("moment", ["after_connect", "final_query"])
def test_sidecar_race_before_first_and_after_last_query_blocks_and_closes(
    fixture, monkeypatch, moment
):
    journal, _, _, _ = fixture
    import bees_host.provisioning.journal as module

    connect, connections, trace = module.sqlite3.connect, [], []
    sidecar = journal.directory / "journal.sqlite3-shm"

    def observed(target, **kwargs):
        connection = connect(target, **kwargs)
        connections.append(connection)

        def queried(statement):
            trace.append(statement)
            if moment == "final_query" and statement == "SELECT * FROM operations":
                create(sidecar, b"private partial evidence")

        connection.set_trace_callback(queried)
        if moment == "after_connect":
            create(sidecar, b"private partial evidence")
        return connection

    monkeypatch.setattr(module.sqlite3, "connect", observed)
    with pytest.raises(ProvisionError, match="provision_state_invalid"):
        Journal.open_read_only(journal.directory)
    assert len(connections) == 1
    if moment == "after_connect":
        assert trace == []
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        connections[0].execute("SELECT 1")
    before = fingerprint(journal.directory)
    assert sidecar.read_bytes() == b"private partial evidence"
    assert fingerprint(journal.directory) == before


@pytest.mark.parametrize("target", ["claim", "result"])
def test_deeply_nested_json_returns_closed_error_without_mutating_evidence(fixture, target):
    journal, _, _, _ = fixture
    phase(journal, "confirmed")
    nested = "[" * 1200 + "0" + "]" * 1200
    with closing(sqlite3.connect(journal.path)) as connection:
        if target == "claim":
            value = journal.claim.model_dump_json()[:-1] + ',"extra":' + nested + "}"
            connection.execute("UPDATE binding SET claim_json=?", (value,))
        else:
            value = '{"vm_id":null,"verified":true,"extra":' + nested + "}"
            connection.execute("UPDATE operations SET result_json=?", (value,))
        connection.commit()
    before = fingerprint(journal.directory)
    with pytest.raises(ProvisionError, match="^provision_state_invalid$"):
        Journal.open_read_only(journal.directory)
    assert fingerprint(journal.directory) == before
