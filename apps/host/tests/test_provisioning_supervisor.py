"""Controle real/journals privados; hardware falso, sem VM ou concessão real."""

import threading
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from bees_host.provisioning.contracts import OPERATIONS, ProvisionError
from bees_host.provisioning.journal import Journal
from bees_host.provisioning.runner import Runner
from bees_host.provisioning.supervisor import Supervisor
from bees_host.provisioning.supervisor_store import SupervisorStore
from test_guest_bridge_tls import private_bridge_directory
from test_provisioning_runner import FakeAuthority, FakeBackend, claim_fixture


class Authority(FakeAuthority):
    def __init__(self, claim):
        super().__init__(claim)
        self.renewals = []
        self.unknowns = []
        self.lose_renewal = False
        self.bad_renewal = False

    def renew(self, claim, request_id):
        self.renewals.append(request_id)
        self.claim = self.claim.model_copy(
            update={
                "revision": self.claim.revision + 1,
                "lease_expires_at": datetime.now(UTC) + timedelta(seconds=60),
            }
        )
        if self.lose_renewal:
            raise OSError("private lost renewal response")
        if self.bad_renewal:
            return self.claim.model_copy(update={"generation": self.claim.generation + 1})
        return self.claim

    def mark_unknown(self, claim, effect_id, request_id):
        self.unknowns.append((effect_id, request_id))
        if self.revoked:
            raise OSError("private revoked credential")
        self.claim = self.claim.model_copy(
            update={"revision": self.claim.revision + 1, "status": "outcome_unknown"}
        )
        return self.claim


class Backend(FakeBackend):
    def __init__(self, claim):
        super().__init__(claim)
        self.before_finish = None
        self.guards = 0

    def start(self, *args):
        command = super().start(*args)
        finish = command.finish

        def supervised_finish(*, guard=None):
            if self.before_finish:
                self.before_finish()
            if guard is not None:
                self.guards += 1
                guard()
            return finish()

        command.finish = supervised_finish
        return command


@pytest.fixture
def fixture():
    with private_bridge_directory() as directory:
        claim = claim_fixture().model_copy(
            update={"lease_expires_at": datetime.now(UTC) + timedelta(seconds=60)}
        )
        journal = Journal.initialize(directory / "plan", claim)
        store = SupervisorStore.initialize(
            directory / "host", installation_id=claim.installation_id, host_id=claim.host_id
        )
        authority, backend = Authority(claim), Backend(claim)
        template = SimpleNamespace(verify=lambda plan: directory / "fixture.iso")
        supervisor = Supervisor(Runner(journal, authority, backend, template), store)
        try:
            yield supervisor, authority, backend, journal, store
        finally:
            journal.close()
            store.close()


def test_sequence_holds_both_locks_and_completes_once(fixture):
    supervisor, authority, backend, journal, store = fixture

    def locked():
        with pytest.raises(ProvisionError):
            with journal.lock():
                pytest.fail("second plan owner")
        with pytest.raises(ProvisionError):
            with store.lock():
                pytest.fail("second host owner")

    backend.on_ready = locked
    assert supervisor.run().verified
    assert backend.effects == list(OPERATIONS) and backend.guards == 6
    assert journal.claim.status == "confirmed" and not backend.alive
    assert store.runs()[0]["status"] == "completed"
    with pytest.raises(ProvisionError, match="reconciliation_required"):
        supervisor.run()
    replacement = Supervisor(supervisor.runner, store)
    with pytest.raises(ProvisionError):
        replacement.run()
    assert backend.effects == list(OPERATIONS)


@pytest.mark.parametrize("where", ["before_run", "ready", "finish"])
def test_stop_never_grants_next_effect(fixture, where):
    supervisor, authority, backend, journal, store = fixture
    if where == "before_run":
        supervisor.cancelled.set()
    elif where == "ready":
        backend.on_ready = supervisor.cancelled.set
    else:
        backend.before_finish = supervisor.cancelled.set
    with pytest.raises(ProvisionError):
        supervisor.run()
    assert backend.effects == (["create_vhd"] if where == "finish" else [])
    assert not backend.alive
    assert store.runs()[0]["status"] == ("stopped" if where == "before_run" else "unknown")
    if where != "before_run":
        assert journal.operations()["create_vhd"]["status"] == "unknown"
        assert len(authority.unknowns) == 1
    with pytest.raises(ProvisionError):
        Supervisor(supervisor.runner, store).run()


def test_renewal_during_wait_is_durable_and_owned(fixture, monkeypatch):
    supervisor, authority, backend, journal, store = fixture

    monkeypatch.setattr("bees_host.provisioning.supervisor.RENEW_MARGIN", 65)

    def lease_nears_end():
        supervisor._next_check = 0

    backend.before_finish = lease_nears_end
    assert supervisor.run().verified
    assert len(authority.renewals) >= 6
    assert len(set(authority.renewals)) == len(authority.renewals)
    assert all(row["status"] == "confirmed" for row in store.requests())
    assert journal.claim.status == "confirmed"


@pytest.mark.parametrize("failure", ["lost", "fenced", "revoked", "expired", "budget"])
def test_wait_failure_stops_child_and_blocks_chain(fixture, failure, monkeypatch):
    supervisor, authority, backend, journal, store = fixture

    monkeypatch.setattr("bees_host.provisioning.supervisor.RENEW_MARGIN", 65)

    def fail():
        supervisor._next_check = 0
        if failure == "revoked":
            authority.revoked = True
        elif failure == "budget":
            supervisor._deadline = 0
        else:
            if failure == "expired":
                authority.claim = authority.claim.model_copy(
                    update={"lease_expires_at": datetime.now(UTC) - timedelta(seconds=1)}
                )
            authority.lose_renewal = failure == "lost"
            authority.bad_renewal = failure == "fenced"

    backend.before_finish = fail
    with pytest.raises(ProvisionError, match="^provision_operation_unknown$") as caught:
        supervisor.run()
    assert "private" not in str(caught.value)
    assert backend.effects == ["create_vhd"] and not backend.alive
    assert journal.operations()["create_vhd"]["status"] == "unknown"
    assert store.runs()[0]["status"] == "unknown"
    renewals = len(authority.renewals)
    with pytest.raises(ProvisionError):
        Supervisor(supervisor.runner, store).run()
    assert len(authority.renewals) == renewals
    if failure in {"lost", "fenced"}:
        assert any(
            row["kind"] == "renew" and row["status"] == "pending" for row in store.requests()
        )


def test_lost_receipt_does_not_reconcile_or_resume_effect(fixture):
    supervisor, authority, backend, journal, store = fixture
    authority.lose_receipt = True
    with pytest.raises(ProvisionError):
        supervisor.run()
    assert supervisor.reconcile() == "matched"
    assert store.runs()[0]["status"] == "unknown"
    assert journal.operations()["create_vhd"]["status"] == "unknown"
    assert backend.effects == ["create_vhd"]
    backend.alive = True
    with pytest.raises(ProvisionError, match="provision_owner_running"):
        supervisor.reconcile()
    assert backend.effects == ["create_vhd"]


def test_interruption_before_go_preserves_intent_and_closes_child(fixture):
    supervisor, _, backend, journal, store = fixture

    def interrupted():
        raise KeyboardInterrupt

    backend.on_ready = interrupted
    with pytest.raises(ProvisionError):
        supervisor.run()
    assert not backend.effects and not backend.alive
    assert journal.operations()["create_vhd"]["status"] == "unknown"
    assert store.runs()[0]["status"] == "unknown"


def test_stale_existing_journal_cannot_be_resumed_with_new_ledger(fixture):
    supervisor, authority, backend, journal, store = fixture
    supervisor.runner.execute_next()
    with pytest.raises(ProvisionError):
        supervisor.run()
    assert backend.effects == ["create_vhd"] and store.runs()[0]["status"] == "unknown"


def test_cancel_signal_is_per_supervisor(fixture):
    supervisor, _, _, _, store = fixture
    other = Supervisor(supervisor.runner, store, cancelled=threading.Event())
    supervisor.cancelled.set()
    assert not other.cancelled.is_set()


def test_stop_during_last_journal_write_prevents_go(fixture, monkeypatch):
    supervisor, _, backend, journal, _ = fixture
    update = journal.update_claim

    def cancelled_after_write(claim):
        update(claim)
        row = journal.operations().get("create_vhd")
        if row and row["status"] == "dispatch_started":
            supervisor.cancelled.set()

    monkeypatch.setattr(journal, "update_claim", cancelled_after_write)
    with pytest.raises(ProvisionError):
        supervisor.run()
    assert not backend.effects and not backend.alive
    assert journal.operations()["create_vhd"]["status"] == "unknown"


def test_unproved_child_shutdown_blocks_all_later_operations(fixture, monkeypatch):
    supervisor, _, backend, journal, store = fixture
    start = backend.start

    def cannot_close(*args):
        command = start(*args)

        def close():
            raise ProvisionError("provision_quiescence_unproved")

        command.close = close
        return command

    monkeypatch.setattr(backend, "start", cannot_close)
    with pytest.raises(ProvisionError):
        supervisor.run()
    assert backend.effects == ["create_vhd"]
    assert store.runs()[0]["status"] == "unknown"
    assert journal.operations()["create_vhd"]["status"] == "confirmed"
    with pytest.raises(ProvisionError, match="provision_owner_running"):
        supervisor.reconcile()
    with pytest.raises(ProvisionError):
        Supervisor(supervisor.runner, store).run()
    # Fixture sem subprocesso real; nenhum filho do SO precisa ser encerrado.
    backend.alive = False


def test_private_handoff_requires_owner_and_does_not_consume_supervisor(fixture):
    supervisor, authority, backend, journal, store = fixture
    with pytest.raises(ProvisionError, match="provision_lock_required"):
        supervisor._run_locked()
    assert not supervisor._used and supervisor._deadline is None
    with store.lock():
        failures = []

        def foreign():
            try:
                supervisor._run_locked()
            except BaseException as error:
                failures.append(error)

        thread = threading.Thread(target=foreign)
        thread.start()
        thread.join(timeout=5)
        assert not thread.is_alive()
        assert len(failures) == 1 and str(failures[0]) == "provision_lock_required"
        assert not supervisor._used and store.runs() == []
        assert supervisor._run_locked().verified
        store.assert_locked()
    assert backend.effects == list(OPERATIONS)


@pytest.mark.parametrize("failure", [None, "before_run", "lost_receipt"])
def test_v2_handoff_keeps_acquisition_lock_and_finishes_atomic(fixture, failure):
    previous, _, _, old_journal, store = fixture
    new_journal = None
    try:
        with store.lock():
            store.migrate_for_acquisition()
            old = old_journal.claim
            ticket = store.prepare_acquisition(
                enrollment_store_id=uuid4(),
                issue_request_id=uuid4(),
                provisioner_id=old.provisioner_id,
                origin="http://127.0.0.1:8080",
                plan_id=old.plan_id,
                plan_hash=old.plan_hash,
            )
            claim = old.model_copy(update={"owner_id": ticket.owner_id})
            store.accept_acquisition(ticket, claim)
            new_journal = Journal.initialize(old_journal.directory.parent / "acquired", claim)
            authority, backend = Authority(claim), Backend(claim)
            supervisor = Supervisor(
                Runner(new_journal, authority, backend, previous.runner.template), store
            )

            def same_owner():
                store.assert_locked()
                assert store.acquisitions()[0]["status"] == "running"
                with pytest.raises(ProvisionError):
                    with store.lock():
                        pytest.fail("segundo dono do host")

            backend.on_ready = same_owner
            if failure == "before_run":
                supervisor.cancelled.set()
            elif failure == "lost_receipt":
                authority.lose_receipt = True
            if failure is None:
                assert supervisor._run_locked(acquisition=ticket).verified
                status = "completed"
            else:
                with pytest.raises(ProvisionError):
                    supervisor._run_locked(acquisition=ticket)
                status = "stopped" if failure == "before_run" else "unknown"
            store.assert_locked()
            assert store.acquisitions()[0]["status"] == store.runs()[0]["status"] == status
            assert store.acquisitions()[0]["revision"] == 4
            assert supervisor._deadline is not None and supervisor._used
            assert not backend.alive
            if failure == "lost_receipt":
                assert len(authority.unknowns) == 1 and store.requests()[0]["status"] == "confirmed"
        with store.lock():
            assert store.acquisitions()[0]["status"] == status
    finally:
        if new_journal is not None:
            new_journal.close()
