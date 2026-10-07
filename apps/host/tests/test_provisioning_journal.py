import os
import subprocess
import sys
from uuid import uuid4

import pytest
from bees_host.provisioning.contracts import ProvisionError
from bees_host.provisioning.journal import Journal
from bees_host.provisioning.runner import Runner
from test_guest_bridge_tls import private_bridge_directory
from test_provisioning_runner import FakeAuthority, FakeBackend, claim_fixture


def test_lock_between_real_processes_and_crash_releases_owner():
    with private_bridge_directory() as base:
        claim = claim_fixture()
        journal = Journal.initialize(base / str(claim.plan_id), claim)
        try:
            child = """
import sys
from pathlib import Path
from bees_host.provisioning.journal import Journal
from bees_host.provisioning.contracts import ProvisionError
j=Journal(Path(sys.argv[1]))
try:
 with j.lock():
  sys.exit(9)
except ProvisionError as e:
 sys.exit(0 if str(e)=='provision_owner_running' else 10)
"""
            with journal.lock():
                result = subprocess.run(
                    [sys.executable, "-c", child, str(journal.directory)],
                    timeout=10,
                    capture_output=True,
                    check=False,
                )
                assert result.returncode == 0 and not result.stdout and not result.stderr
            child = """
import os,sys
from pathlib import Path
from bees_host.provisioning.journal import Journal
j=Journal(Path(sys.argv[1]))
with j.lock():
 j.prepare('create_vhd')
 j.transition('create_vhd','prepared','authority_started')
 os._exit(31)
"""
            result = subprocess.run(
                [sys.executable, "-c", child, str(journal.directory)],
                timeout=10,
                capture_output=True,
                check=False,
            )
            assert result.returncode == 31 and not result.stdout and not result.stderr
            with journal.lock():
                assert journal.operations()["create_vhd"]["status"] == "authority_started"
            backend = FakeBackend(claim)
            runner = Runner(journal, FakeAuthority(claim), backend, None)
            with pytest.raises(ProvisionError, match="provision_reconciliation_required"):
                runner.execute_next()
            assert not backend.effects
            assert journal.operations()["create_vhd"]["status"] == "unknown"
        finally:
            journal.close()


@pytest.mark.parametrize("name", ["journal.sqlite3", "identity.json", "owner.lock"])
def test_lost_local_state_never_creates_replacement(name):
    with private_bridge_directory() as base:
        journal = Journal.initialize(base / "journal", claim_fixture())
        directory = journal.directory
        journal.close()
        (directory / name).unlink()
        with pytest.raises(ProvisionError, match="provision_state_missing"):
            Journal(directory)
        assert not (directory / name).exists()
        with pytest.raises(ProvisionError, match="provision_state_already_present"):
            Journal.initialize(directory, claim_fixture())


def test_partial_and_corrupt_database_are_not_initialized():
    with private_bridge_directory() as base:
        for content in (b"", b"private corrupt bytes"):
            directory = base / str(uuid4())
            journal = Journal.initialize(directory, claim_fixture())
            journal.close()
            (directory / "journal.sqlite3").write_bytes(content)
            with pytest.raises(ProvisionError, match="^provision_state_invalid$") as caught:
                Journal(directory)
            assert "private" not in str(caught.value)
            assert (directory / "journal.sqlite3").read_bytes() == content


def test_partial_initialization_keeps_marker_and_refuses_retry(monkeypatch):
    import bees_host.provisioning.journal as module

    with private_bridge_directory() as base:
        directory = base / "partial"
        original = module.create

        def interrupted(path, data):
            if path.name == "journal.sqlite3":
                raise OSError("private interruption")
            return original(path, data)

        monkeypatch.setattr(module, "create", interrupted)
        with pytest.raises(ProvisionError, match="provision_state_unavailable"):
            Journal.initialize(directory, claim_fixture())
        assert (directory / "identity.json").exists()
        with pytest.raises(ProvisionError, match="provision_state_missing"):
            Journal(directory)
        with pytest.raises(ProvisionError, match="provision_state_already_present"):
            Journal.initialize(directory, claim_fixture())


def test_journal_is_delete_full_and_mutation_requires_lock():
    with private_bridge_directory() as base:
        journal = Journal.initialize(base / "journal", claim_fixture())
        try:
            assert journal.connection.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
            assert journal.connection.execute("PRAGMA synchronous").fetchone()[0] == 2
            with pytest.raises(ProvisionError, match="provision_lock_required"):
                journal.prepare("create_vhd")
            assert not journal.operations()
            with journal.lock():
                journal.prepare("create_vhd")
                with pytest.raises(ProvisionError, match="provision_reconciliation_required"):
                    journal.prepare("create_vm")
        finally:
            journal.close()


def test_hardlinked_journal_and_insecure_leaf_are_refused():
    from test_guest_bridge_private import allow_everyone

    with private_bridge_directory() as base:
        journal = Journal.initialize(base / "journal", claim_fixture())
        directory = journal.directory
        journal.close()
        os.link(directory / "journal.sqlite3", directory / "copy.sqlite3")
        with pytest.raises(ProvisionError, match="provision_private_required"):
            Journal(directory)
        (directory / "copy.sqlite3").unlink()
        allow_everyone(directory / "journal.sqlite3")
        with pytest.raises(ProvisionError, match="provision_private_required"):
            Journal(directory)
