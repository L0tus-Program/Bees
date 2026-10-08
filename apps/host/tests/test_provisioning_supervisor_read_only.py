"""Check-only com SQLite/ACL/locks reais; nenhum repair, rede ou efeito de hardware."""

import hashlib
import json
import sqlite3
import subprocess
import sys
import threading
from contextlib import closing
from uuid import uuid4

import pytest
from bees_host.provisioning.contracts import ProvisionError
from bees_host.provisioning.journal import create
from bees_host.provisioning.supervisor_store import SupervisorStore
from test_guest_bridge_tls import private_bridge_directory
from test_provisioning_acquisition_store import acquire, prepare
from test_provisioning_runner import claim_fixture


def fingerprint(directory):
    result = {
        path.name: (hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_mtime_ns)
        for path in directory.iterdir()
    }
    result["."] = ("directory", directory.stat().st_mtime_ns)
    return result


@pytest.fixture(params=[1, 2])
def fixture(request):
    with private_bridge_directory() as base:
        claim = claim_fixture()
        initialize = (
            SupervisorStore.initialize
            if request.param == 1
            else SupervisorStore.initialize_for_acquisition
        )
        store = initialize(
            base / "host", installation_id=claim.installation_id, host_id=claim.host_id
        )
        try:
            yield store, claim
        finally:
            store.close()


def test_read_only_mode_schema_bindings_lock_and_unchanged_files(fixture, monkeypatch):
    store, claim = fixture
    before = fingerprint(store.directory)
    import bees_host.provisioning.supervisor_store as module

    connect = module.sqlite3.connect
    uris = []

    def observed(target, **kwargs):
        uris.append((target, kwargs))
        return connect(target, **kwargs)

    monkeypatch.setattr(module.sqlite3, "connect", observed)
    check = SupervisorStore.open_read_only(store.directory)
    try:
        assert len(uris) == 1 and uris[0][0].endswith("?mode=ro")
        assert "immutable" not in uris[0][0] and uris[0][1]["uri"] is True
        assert check.version == store.version and check.store_id == store.store_id
        assert check.installation_id == claim.installation_id and check.host_id == claim.host_id
        with check.lock():
            check.assert_locked()
            assert check.runs() == check.requests() == []
            assert check.execution_blocked_local() is False
            if check.version == 2:
                assert check.acquisitions() == []
            with pytest.raises(sqlite3.OperationalError, match="readonly"):
                check.connection.execute("UPDATE binding SET store_id='changed'")
            with pytest.raises(ProvisionError, match="provision_lock_required"):
                check.close()
        assert fingerprint(store.directory) == before
    finally:
        check.close()
    assert fingerprint(store.directory) == before


def test_mutations_and_migration_refuse_read_only_before_sql(fixture):
    store, claim = fixture
    before = fingerprint(store.directory)
    check = SupervisorStore.open_read_only(store.directory)
    try:
        with check.lock():
            trace = []
            check.connection.set_trace_callback(trace.append)
            operations = [
                lambda: check.begin(claim),
                lambda: check.finish(claim.claim_id, "completed"),
                lambda: check.request(claim.claim_id, "renew"),
                lambda: check.confirm(uuid4(), claim),
                lambda: prepare(check, claim),
                lambda: check.accept_acquisition(None, claim),
                lambda: check.fail_acquisition(None),
                check.migrate_for_acquisition,
            ]
            for operation in operations:
                with pytest.raises(ProvisionError, match="^provision_read_only$"):
                    operation()
            assert trace == [] and not check.connection.in_transaction
    finally:
        check.close()
    assert fingerprint(store.directory) == before


@pytest.mark.parametrize(
    "sidecar",
    [
        "supervisor.sqlite3-journal",
        "supervisor.sqlite3-wal",
        "supervisor.sqlite3-shm",
        "staged.json",
        "supervisor.sqlite3-staged",
    ],
)
def test_partial_sidecars_refused_before_sqlite_connect_without_changes(
    fixture, monkeypatch, sidecar
):
    store, _ = fixture
    create(store.directory / sidecar, b"private partial evidence")
    before = fingerprint(store.directory)
    import bees_host.provisioning.supervisor_store as module

    def forbidden(*args, **kwargs):
        pytest.fail("SQLite abriu estado parcial")

    monkeypatch.setattr(module.sqlite3, "connect", forbidden)
    with pytest.raises(ProvisionError, match="provision_state_invalid"):
        SupervisorStore.open_read_only(store.directory)
    assert fingerprint(store.directory) == before


@pytest.mark.parametrize("sidecar", ["supervisor.sqlite3-journal", "supervisor.sqlite3-wal"])
def test_sidecar_race_after_connect_closes_without_query_or_recovery(fixture, monkeypatch, sidecar):
    store, _ = fixture
    import bees_host.provisioning.supervisor_store as module

    connect = module.sqlite3.connect
    connections, trace = [], []

    def raced(*args, **kwargs):
        connection = connect(*args, **kwargs)
        connection.set_trace_callback(trace.append)
        connections.append(connection)
        create(store.directory / sidecar, b"private partial evidence")
        return connection

    monkeypatch.setattr(module.sqlite3, "connect", raced)
    with pytest.raises(ProvisionError, match="provision_state_invalid"):
        SupervisorStore.open_read_only(store.directory)
    assert trace == [] and len(connections) == 1
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        connections[0].execute("SELECT 1")
    assert (store.directory / sidecar).read_bytes() == b"private partial evidence"


@pytest.mark.parametrize("operation", ["lock", "runs", "requests", "blocked"])
def test_sidecars_created_after_open_are_refused_by_guards(fixture, operation):
    store, _ = fixture
    check = SupervisorStore.open_read_only(store.directory)
    try:
        if operation == "blocked":
            before = fingerprint(store.directory)
            with check.lock():
                sidecar = store.directory / "supervisor.sqlite3-journal"
                create(sidecar, b"private evidence")
                before[sidecar.name] = (
                    hashlib.sha256(sidecar.read_bytes()).hexdigest(),
                    sidecar.stat().st_mtime_ns,
                )
                before["."] = ("directory", store.directory.stat().st_mtime_ns)
                with pytest.raises(ProvisionError, match="provision_state_invalid"):
                    check.execution_blocked_local()
        else:
            create(store.directory / "supervisor.sqlite3-journal", b"private evidence")
            before = fingerprint(store.directory)
            with pytest.raises(ProvisionError, match="provision_state_invalid"):
                if operation == "lock":
                    with check.lock():
                        pytest.fail("lock aceitou estado parcial")
                else:
                    getattr(check, operation)()
        assert fingerprint(store.directory) == before
    finally:
        check.close()


@pytest.mark.parametrize("status", ["running", "unknown", "stopped", "completed"])
def test_check_reads_quarantine_history_without_releasing_or_migrating(fixture, status):
    store, claim = fixture
    with store.lock():
        if store.version == 2:
            ticket, claim = acquire(store, claim)
            store.begin(claim, acquisition=ticket)
        else:
            store.begin(claim)
        if status != "running":
            store.finish(claim.claim_id, status)
    before = fingerprint(store.directory)
    check = SupervisorStore.open_read_only(store.directory)
    try:
        with check.lock():
            assert check.runs()[0]["status"] == status
            assert check.execution_blocked_local() == (status != "completed")
            assert check.version == store.version
            if check.version == 2:
                assert check.acquisitions()[0]["status"] == status
    finally:
        check.close()
    assert fingerprint(store.directory) == before


def test_read_only_global_quarantine_not_limited_by_public_snapshot_page():
    with private_bridge_directory() as base:
        claim = claim_fixture()
        store = SupervisorStore.initialize(
            base / "host", installation_id=claim.installation_id, host_id=claim.host_id
        )
        try:
            with store.lock():
                store.begin(claim)
                store.finish(claim.claim_id, "unknown")
                row = store.runs()[0]
                columns = tuple(row)
                sql = "INSERT INTO runs VALUES(" + ",".join("?" for _ in columns) + ")"
                # Histórico amplo válido no schema1; o unknown antigo fica fora
                # da primeira página, mas continua sendo guarda global.
                for _ in range(101):
                    values = dict(row)
                    values.update(
                        claim_id=str(uuid4()),
                        owner_id=str(uuid4()),
                        plan_id=str(uuid4()),
                        status="completed",
                    )
                    store.connection.execute(sql, tuple(values[column] for column in columns))
            before = fingerprint(store.directory)
            check = SupervisorStore.open_read_only(store.directory)
            try:
                with check.lock():
                    assert check.runs(limit=1)[0]["status"] == "completed"
                    assert check.execution_blocked_local() is True
            finally:
                check.close()
            assert fingerprint(store.directory) == before
        finally:
            store.close()


def test_real_private_hotjournal_crash_never_enters_sqlite_recovery(fixture, monkeypatch):
    store, _ = fixture
    directory = store.directory
    store.close()
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
        [sys.executable, "-c", code, str(directory)],
        capture_output=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 31 and not result.stdout and not result.stderr
    assert (directory / "supervisor.sqlite3-journal").read_bytes()[:8] == bytes.fromhex(
        "d9d505f920a163d7"
    )
    before = fingerprint(directory)
    import bees_host.provisioning.supervisor_store as module

    def forbidden(*args, **kwargs):
        pytest.fail("SQLite tentou recuperar journal durante check-only")

    monkeypatch.setattr(module.sqlite3, "connect", forbidden)
    with pytest.raises(ProvisionError, match="provision_state_invalid"):
        SupervisorStore.open_read_only(directory)
    assert fingerprint(directory) == before


@pytest.mark.parametrize(
    "sql",
    [
        "PRAGMA user_version=99",
        "UPDATE binding SET store_id='bad'",
        "CREATE TABLE surprise(value TEXT)",
        "DROP INDEX requests_by_claim",
    ],
)
def test_corrupt_schema_binding_or_version_closes_read_only_without_repair(
    fixture, monkeypatch, sql
):
    store, _ = fixture
    with closing(sqlite3.connect(store.path)) as writable:
        writable.execute(sql)
        writable.commit()
    before = fingerprint(store.directory)
    import bees_host.provisioning.supervisor_store as module

    connect, opened = module.sqlite3.connect, []

    def observed(*args, **kwargs):
        connection = connect(*args, **kwargs)
        opened.append(connection)
        return connection

    monkeypatch.setattr(module.sqlite3, "connect", observed)
    with pytest.raises(ProvisionError, match="provision_state_invalid"):
        SupervisorStore.open_read_only(store.directory)
    assert len(opened) == 1
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        opened[0].execute("SELECT 1")
    assert fingerprint(store.directory) == before


def test_native_read_only_lock_contends_with_writer_and_foreign_thread(fixture):
    store, _ = fixture
    check = SupervisorStore.open_read_only(store.directory)
    before = fingerprint(store.directory)
    try:
        with check.lock():
            with pytest.raises(ProvisionError, match="provision_owner_running"):
                with store.lock():
                    pytest.fail("dono escritor simultâneo")
            failures = []

            def foreign():
                try:
                    check.execution_blocked_local()
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
            check.assert_locked()
    finally:
        check.close()
    assert fingerprint(store.directory) == before


def test_marker_changed_after_open_is_not_a_valid_current_binding(fixture):
    store, _ = fixture
    check = SupervisorStore.open_read_only(store.directory)
    try:
        marker = store.directory / "identity.json"
        changed = json.loads(marker.read_bytes())
        changed["host_id"] = str(uuid4())
        marker.write_text(json.dumps(changed), encoding="utf-8")
        before = fingerprint(store.directory)
        with pytest.raises(ProvisionError, match="provision_state_invalid"):
            with check.lock():
                pytest.fail("binding anterior aceito")
        assert fingerprint(store.directory) == before
    finally:
        check.close()


@pytest.mark.parametrize("filename", ["identity.json", "owner.lock", "supervisor.sqlite3"])
def test_missing_required_files_never_recreated_or_connected(fixture, monkeypatch, filename):
    store, _ = fixture
    store.close()
    (store.directory / filename).unlink()
    before = fingerprint(store.directory)
    import bees_host.provisioning.supervisor_store as module

    def forbidden(*args, **kwargs):
        pytest.fail("SQLite abriu ledger incompleto")

    monkeypatch.setattr(module.sqlite3, "connect", forbidden)
    with pytest.raises(ProvisionError, match="provision_state_missing"):
        SupervisorStore.open_read_only(store.directory)
    assert fingerprint(store.directory) == before


@pytest.mark.parametrize("content", [b"", b"private corrupt database"])
def test_corrupt_database_bytes_preserved_by_read_only(fixture, content):
    store, _ = fixture
    store.close()
    store.path.write_bytes(content)
    before = fingerprint(store.directory)
    with pytest.raises(ProvisionError, match="provision_state_invalid"):
        SupervisorStore.open_read_only(store.directory)
    assert fingerprint(store.directory) == before


def test_read_only_guard_after_final_sql_read_refuses_new_sidecar(fixture):
    store, _ = fixture
    check = SupervisorStore.open_read_only(store.directory)
    try:
        sidecar = store.directory / "supervisor.sqlite3-shm"

        def appear(statement):
            if statement == "SELECT * FROM requests":
                create(sidecar, b"private concurrent partial evidence")

        check.connection.set_trace_callback(appear)
        with pytest.raises(ProvisionError, match="provision_state_invalid"):
            with check.lock():
                pytest.fail("guarda final aceitou sidecar")
        before = fingerprint(store.directory)
        check.connection.set_trace_callback(None)
        assert sidecar.read_bytes() == b"private concurrent partial evidence"
        check.close()
        assert fingerprint(store.directory) == before
    finally:
        check.close()


def test_clean_wal_database_without_sidecars_refused_before_connect(fixture, monkeypatch):
    store, _ = fixture
    store.close()
    with closing(sqlite3.connect(store.path)) as writable:
        assert writable.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
    assert store.path.read_bytes()[18:20] == b"\x02\x02"
    assert not (store.directory / "supervisor.sqlite3-wal").exists()
    assert not (store.directory / "supervisor.sqlite3-shm").exists()
    before = fingerprint(store.directory)
    import bees_host.provisioning.supervisor_store as module

    def forbidden(*args, **kwargs):
        pytest.fail("SQLite abriu formato WAL e poderia criar sidecars")

    monkeypatch.setattr(module.sqlite3, "connect", forbidden)
    with pytest.raises(ProvisionError, match="provision_state_invalid"):
        SupervisorStore.open_read_only(store.directory)
    assert fingerprint(store.directory) == before
