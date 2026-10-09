"""Abertura operacional real: lock antes de SQLite RW e nenhuma recuperação."""

import sqlite3
import subprocess
import sys
import threading
from contextlib import closing

import pytest
from bees_host.provisioning import supervisor_store as module
from bees_host.provisioning.contracts import ProvisionError
from bees_host.provisioning.journal import create
from bees_host.provisioning.supervisor_store import SupervisorStore
from test_guest_bridge_tls import private_bridge_directory
from test_provisioning_acquisition_store import acquire
from test_provisioning_runner import claim_fixture
from test_provisioning_supervisor_read_only import fingerprint


@pytest.fixture
def fixture():
    with private_bridge_directory() as base:
        claim = claim_fixture()
        store = SupervisorStore.initialize_for_acquisition(
            base / "ledger", installation_id=claim.installation_id, host_id=claim.host_id
        )
        directory = store.directory
        store.close()
        yield directory, claim


def test_lock_precedes_connect_and_lasts_through_commit_and_close(fixture, monkeypatch):
    directory, claim = fixture
    connect, targets = module.sqlite3.connect, []

    def observed(target, **kwargs):
        targets.append(target)
        # A conexão ainda não existe; o lock nativo já exclui outro escritor.
        with pytest.raises(ProvisionError, match="provision_owner_running"):
            with SupervisorStore.open_execution_locked(directory):
                pytest.fail("Segundo escritor entrou antes de SQLite")
        return connect(target, **kwargs)

    monkeypatch.setattr(module.sqlite3, "connect", observed)
    with SupervisorStore.open_execution_locked(directory) as store:
        assert targets == [store.path.as_uri() + "?mode=rw"]
        store.assert_locked()
        assert store.version == 2 and store._lock_epoch is not None
        ticket, accepted = acquire(store, claim)
        store.begin(accepted, acquisition=ticket)
        store.finish(accepted.claim_id, "completed")
        assert store.acquisitions()[0]["status"] == store.runs()[0]["status"] == "completed"
        connection = store.connection
        with pytest.raises(ProvisionError, match="provision_lock_required"):
            store.close()
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        connection.execute("SELECT 1")
    assert not store._locked and store._lock_epoch is None and not store._tickets
    with SupervisorStore.open_execution_locked(directory) as reopened:
        assert reopened.acquisitions()[0]["status"] == "completed"
        with pytest.raises(ProvisionError, match="provision_reconciliation_required"):
            reopened.accept_acquisition(ticket, accepted)


def test_foreign_thread_refused_and_exception_releases_connection_and_native_lock(fixture):
    directory, _ = fixture
    failures = []
    with pytest.raises(RuntimeError, match="interrupção própria"):
        with SupervisorStore.open_execution_locked(directory) as store:
            connection = store.connection

            def foreign():
                try:
                    store.assert_locked()
                except BaseException as error:
                    failures.append(error)

            thread = threading.Thread(target=foreign)
            thread.start()
            thread.join(timeout=5)
            assert not thread.is_alive() and len(failures) == 1
            assert type(failures[0]) is ProvisionError
            assert str(failures[0]) == "provision_lock_required"
            raise RuntimeError("interrupção própria")
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        connection.execute("SELECT 1")
    with SupervisorStore.open_execution_locked(directory) as reopened:
        assert reopened.plan_inventory() == {}


@pytest.mark.parametrize(
    "sidecar", ["supervisor.sqlite3-journal", "supervisor.sqlite3-wal", "supervisor.sqlite3-shm"]
)
def test_sidecar_refused_before_connect_without_repair(fixture, monkeypatch, sidecar):
    directory, _ = fixture
    create(directory / sidecar, b"private evidence")
    before = fingerprint(directory)

    def forbidden(*args, **kwargs):
        pytest.fail("SQLite abriu estado parcial")

    monkeypatch.setattr(module.sqlite3, "connect", forbidden)
    with pytest.raises(ProvisionError, match="provision_state_invalid"):
        with SupervisorStore.open_execution_locked(directory):
            pytest.fail("Estado parcial aceito")
    assert fingerprint(directory) == before


@pytest.mark.parametrize("sidecar", ["supervisor.sqlite3-journal", "supervisor.sqlite3-wal"])
def test_sidecar_after_connect_closes_before_first_sql(fixture, monkeypatch, sidecar):
    directory, _ = fixture
    connect, connections, trace = module.sqlite3.connect, [], []

    def raced(*args, **kwargs):
        connection = connect(*args, **kwargs)
        connection.set_trace_callback(trace.append)
        connections.append(connection)
        create(directory / sidecar, b"private concurrent evidence")
        return connection

    monkeypatch.setattr(module.sqlite3, "connect", raced)
    with pytest.raises(ProvisionError, match="provision_state_invalid"):
        with SupervisorStore.open_execution_locked(directory):
            pytest.fail("Corrida parcial aceitou abertura")
    assert trace == [] and len(connections) == 1
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        connections[0].execute("SELECT 1")
    assert (directory / sidecar).read_bytes() == b"private concurrent evidence"


def test_clean_wal_without_sidecars_refused_before_connect(fixture, monkeypatch):
    directory, _ = fixture
    with closing(sqlite3.connect(directory / "supervisor.sqlite3")) as connection:
        assert connection.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
    assert {path.name for path in directory.iterdir()} == {
        "identity.json",
        "owner.lock",
        "supervisor.sqlite3",
    }
    before = fingerprint(directory)

    def forbidden(*args, **kwargs):
        pytest.fail("SQLite abriu formato WAL")

    monkeypatch.setattr(module.sqlite3, "connect", forbidden)
    with pytest.raises(ProvisionError, match="provision_state_invalid"):
        with SupervisorStore.open_execution_locked(directory):
            pytest.fail("WAL aceito")
    assert fingerprint(directory) == before


@pytest.mark.parametrize("filename", ["identity.json", "owner.lock", "supervisor.sqlite3"])
def test_missing_file_never_recreated_or_connected(fixture, monkeypatch, filename):
    directory, _ = fixture
    (directory / filename).unlink()
    before = fingerprint(directory)

    def forbidden(*args, **kwargs):
        pytest.fail("SQLite abriu ledger incompleto")

    monkeypatch.setattr(module.sqlite3, "connect", forbidden)
    with pytest.raises(ProvisionError, match="provision_state_missing"):
        with SupervisorStore.open_execution_locked(directory):
            pytest.fail("Estado incompleto aceito")
    assert fingerprint(directory) == before


@pytest.mark.parametrize(
    "sql",
    ["PRAGMA user_version=99", "CREATE TABLE surprise(value TEXT)", "DROP INDEX requests_by_claim"],
)
def test_corrupt_schema_closes_before_effect_without_changes(fixture, sql):
    directory, _ = fixture
    with closing(sqlite3.connect(directory / "supervisor.sqlite3")) as connection:
        connection.execute(sql)
        connection.commit()
    before = fingerprint(directory)
    with pytest.raises(ProvisionError, match="provision_state_invalid"):
        with SupervisorStore.open_execution_locked(directory):
            pytest.fail("Schema alterado aceito")
    assert fingerprint(directory) == before


def test_legacy_refused_without_migration_or_changes():
    with private_bridge_directory() as base:
        claim = claim_fixture()
        store = SupervisorStore.initialize(
            base / "legacy", installation_id=claim.installation_id, host_id=claim.host_id
        )
        directory = store.directory
        store.close()
        before = fingerprint(directory)
        with pytest.raises(ProvisionError, match="provision_acquisition_required"):
            with SupervisorStore.open_execution_locked(directory):
                pytest.fail("Migração implícita")
        assert fingerprint(directory) == before


def test_real_crash_leaves_hot_journal_and_strict_open_never_recovers(fixture, monkeypatch):
    directory, _ = fixture
    code = """
import os,sys,sqlite3
from pathlib import Path
from bees_host.security import check_private
root=Path(sys.argv[1])
connection=sqlite3.connect(root/'supervisor.sqlite3',isolation_level=None)
connection.execute('PRAGMA journal_mode=DELETE')
connection.execute('PRAGMA synchronous=FULL')
connection.execute('PRAGMA cache_size=1')
connection.execute('BEGIN IMMEDIATE')
connection.execute("UPDATE binding SET store_id='uncommitted-private-value'")
connection.execute('CREATE TABLE spill(value BLOB)')
for _ in range(32):connection.execute('INSERT INTO spill VALUES(?)',(b'x'*8192,))
journal=root/'supervisor.sqlite3-journal'
check_private(journal,protect=True)
if journal.read_bytes()[:8]!=bytes.fromhex('d9d505f920a163d7'):os._exit(9)
os._exit(31)
"""
    result = subprocess.run(
        [sys.executable, "-c", code, str(directory)], capture_output=True, timeout=10, check=False
    )
    assert result.returncode == 31 and not result.stdout and not result.stderr
    before = fingerprint(directory)

    def forbidden(*args, **kwargs):
        pytest.fail("SQLite tentou recuperar hot journal")

    monkeypatch.setattr(module.sqlite3, "connect", forbidden)
    with pytest.raises(ProvisionError, match="provision_state_invalid"):
        with SupervisorStore.open_execution_locked(directory):
            pytest.fail("Hot journal aceito")
    assert fingerprint(directory) == before


def test_other_process_refused_before_sqlite_and_lock_released_after_owner_crash(fixture):
    directory, _ = fixture
    before = fingerprint(directory)
    code = """
import os,sys
from pathlib import Path
from bees_host.provisioning.supervisor_store import SupervisorStore
with SupervisorStore.open_execution_locked(Path(sys.argv[1])):
    print('locked',flush=True)
    sys.stdin.readline()
    os._exit(39)
"""
    process = subprocess.Popen(
        [sys.executable, "-c", code, str(directory)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert process.stdout.readline() == "locked\n"
        with pytest.raises(ProvisionError, match="provision_owner_running"):
            with SupervisorStore.open_execution_locked(directory):
                pytest.fail("Segundo processo entrou")
        _, errors = process.communicate(input="crash\n", timeout=10)
        assert process.returncode == 39 and not errors
        with SupervisorStore.open_execution_locked(directory) as store:
            assert store.plan_inventory() == {}
        assert fingerprint(directory) == before
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=10)
