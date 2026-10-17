"""Recusas comprovadas sem efeito; SQLite/locks reais, transporte/hardware falsos."""

from uuid import UUID, uuid4

import httpx
import pytest
import test_provisioning_acquisition as acquisition_tests
import test_provisioning_supervisor as supervisor_tests
from bees_host.provisioning.contracts import AuthorityRejectedBeforeEffect, ProvisionError
from bees_host.provisioning.http_authority import REJECTION_CODES
from bees_host.provisioning.journal import SCHEMA_V1, Journal
from bees_host.provisioning.supervisor_store import SupervisorStore
from test_guest_bridge_tls import private_bridge_directory
from test_provisioning_acquisition import restarted
from test_provisioning_http_authority import adapter, raw, response
from test_provisioning_runner import claim_fixture

acquisition_fixture = acquisition_tests.fixture
supervisor_fixture = supervisor_tests.fixture


def rejected(code="provisioning_global_stop", *, cached=False):
    return {"error": {"code": code, "dispatch_allowed": False, "cached": cached}}


def request(authority, claim, path, request_id):
    if path == "/claim":
        return authority.claim(claim.plan_id, claim.plan_hash, claim.owner_id, request_id)
    return authority.begin_dispatch(claim, "create_vhd", request_id)


@pytest.mark.parametrize("path", ["/claim", "/begin"])
@pytest.mark.parametrize("code", sorted(REJECTION_CODES))
def test_closed_rejection_is_distinct_and_same_uuid_never_repeats_http(path, code):
    claim, calls = claim_fixture(), []

    def handler(value):
        calls.append(value)
        return response(rejected(code), status=409)

    with adapter(claim, handler) as authority:
        request_id = uuid4()
        with pytest.raises(AuthorityRejectedBeforeEffect, match="authority_stopped"):
            request(authority, claim, path, request_id)
        with pytest.raises(ProvisionError, match="replay_refused") as error:
            request(authority, claim, path, request_id)
        assert type(error.value) is ProvisionError
    assert len(calls) == 1


@pytest.mark.parametrize("path", ["/claim", "/begin"])
@pytest.mark.parametrize(
    "body,status",
    [
        (rejected(cached=True), 409),
        (rejected("unrecognized_code"), 409),
        ({"error": {"code": "provisioning_global_stop"}}, 409),
        ({**rejected(), "extra": False}, 409),
        ({"error": {**rejected()["error"], "cached": 0}}, 409),
        ({"error": {**rejected()["error"], "dispatch_allowed": 0}}, 409),
        (rejected(), 500),
        (rejected(), 403),
    ],
)
def test_unproven_or_cached_rejection_is_still_uncertain(path, body, status):
    claim, calls = claim_fixture(), []

    def handler(value):
        calls.append(value)
        return response(body, status=status)

    with adapter(claim, handler) as authority:
        with pytest.raises(ProvisionError) as error:
            request(authority, claim, path, uuid4())
        assert type(error.value) is ProvisionError
    assert len(calls) == 1


@pytest.mark.parametrize("failure", ["timeout", "lost", "malformed", "duplicate", "oversize"])
def test_transport_failures_never_become_definitive(failure):
    claim, calls = claim_fixture(), []

    def handler(value):
        calls.append(value)
        if failure == "timeout":
            raise httpx.ReadTimeout("private detail")
        if failure == "lost":
            raise httpx.ReadError("private detail")
        if failure == "duplicate":
            return raw(b'{"error":{},"error":{}}', status=409)
        return raw(b"{" if failure == "malformed" else b"x" * 17000, status=409)

    with adapter(claim, handler) as authority:
        with pytest.raises(ProvisionError) as error:
            authority.begin_dispatch(claim, "create_vhd", uuid4())
        assert type(error.value) is ProvisionError
    assert len(calls) == 1


def test_claim_rejection_stops_without_claim_or_quarantine_and_keeps_uuid(acquisition_fixture):
    coordinator, transport, backend, store, plan, _ = acquisition_fixture

    def refuse():
        raise AuthorityRejectedBeforeEffect("provision_authority_stopped")

    transport.before_return = refuse
    with pytest.raises(AuthorityRejectedBeforeEffect):
        coordinator.run(plan)
    row = store.acquisitions()[0]
    assert row["status"] == "stopped" and row["claim_id"] is None
    assert not backend.effects and not transport.control.unknowns
    with store.lock():
        assert not store.execution_blocked_local()
        store.assert_acquisition_ready()
    # Uma tentativa recusada não se torna retry: plano e UUID permanecem consumidos localmente.
    with pytest.raises(ProvisionError, match="claim_reused"):
        restarted(coordinator).run(plan)
    assert store.acquisitions() == [row] and len(transport.calls) == 1
    reopened = SupervisorStore.open_read_only(store.directory)
    try:
        with reopened.lock():
            assert not reopened.execution_blocked_local()
    finally:
        reopened.close()


@pytest.mark.parametrize("prior_effect", [False, True])
def test_begin_rejection_is_terminal_without_unknown_or_new_effect(
    supervisor_fixture, prior_effect
):
    supervisor, authority, backend, journal, store = supervisor_fixture
    begin = authority.begin_dispatch

    def refuse(claim, operation, request_id):
        if prior_effect and operation == "create_vhd":
            return begin(claim, operation, request_id)
        raise AuthorityRejectedBeforeEffect("provision_authority_stopped")

    authority.begin_dispatch = refuse
    with pytest.raises(ProvisionError, match="supervisor_stopped"):
        supervisor.run()
    assert backend.effects == (["create_vhd"] if prior_effect else [])
    rows = journal.operations()
    operation = "create_vm" if prior_effect else "create_vhd"
    assert rows[operation]["status"] == "rejected"
    assert all(
        rows[operation][key] is None
        for key in ("effect_request_id", "pid", "start_ticks", "result_json")
    )
    assert store.runs()[0]["status"] == "stopped" and not authority.unknowns
    original_id = UUID(rows[operation]["request_id"])
    with pytest.raises(ProvisionError, match="authority_stopped"):
        supervisor.runner.execute_next()
    assert UUID(journal.operations()[operation]["request_id"]) == original_id
    reader = Journal.open_read_only(journal.directory)
    try:
        with reader.lock():
            assert reader.operations()[operation]["status"] == "rejected"
    finally:
        reader.close()


def test_explicit_journal_migration_preserves_ids_and_never_reclassifies_pending():
    with private_bridge_directory() as base:
        journal = Journal.initialize(base / "journal", claim_fixture())
        # Fixture materializa o formato anterior exatamente, sem simular migração automática.
        journal.connection.execute("ALTER TABLE operations RENAME TO new_operations")
        journal.connection.execute(SCHEMA_V1[1])
        journal.connection.execute("DROP TABLE new_operations")
        journal.connection.execute("PRAGMA user_version=1")
        journal.close()
        journal = Journal(base / "journal")
        try:
            assert journal.version == 1
            with journal.lock():
                journal.migrate_for_rejections()
                row = journal.prepare("create_vhd")
                journal.transition("create_vhd", "prepared", "authority_started")
                journal.reject("create_vhd")
            journal.close()
            journal = Journal(base / "journal")
            assert journal.version == 2
            assert journal.operations()["create_vhd"]["request_id"] == row["request_id"]
        finally:
            journal.close()


def test_explicit_ledger_migration_and_stopped_history_stays_quarantined():
    with private_bridge_directory() as base:
        claim = claim_fixture()
        store = SupervisorStore.initialize_for_acquisition(
            base / "ledger", installation_id=claim.installation_id, host_id=claim.host_id
        )
        try:
            assert store.version == 2
            with store.lock():
                store.migrate_for_rejections()
                assert store.version == 3
            store.close()
            store = SupervisorStore.open_read_only(base / "ledger")
            with store.lock():
                assert store.version == 3 and not store.execution_blocked_local()
        finally:
            store.close()


@pytest.mark.parametrize(
    "status,body",
    [
        (409, {"error": {"code": "provisioning_global_stop"}}),
        (409, rejected(cached=True)),
        (409, rejected("unknown_code")),
        (500, rejected()),
        (200, {"invalid": True}),
    ],
)
def test_unproven_begin_quarantines_and_reports_unknown(supervisor_fixture, status, body):
    supervisor, authority, backend, journal, store = supervisor_fixture
    with adapter(journal.claim, lambda value: response(body, status=status)) as http:
        authority.begin_dispatch = http.begin_dispatch
        with pytest.raises(ProvisionError, match="operation_unknown"):
            supervisor.run()
    assert journal.operations()["create_vhd"]["status"] == "unknown"
    assert store.runs()[0]["status"] == "unknown"
    assert not backend.effects and len(authority.unknowns) == 1


@pytest.mark.parametrize("status", ["unknown", "stopped"])
def test_legacy_ledger_cannot_reclassify_stopped_or_unknown_on_migration(status):
    with private_bridge_directory() as base:
        claim = claim_fixture()
        store = SupervisorStore.initialize_for_acquisition(
            base / "ledger", installation_id=claim.installation_id, host_id=claim.host_id
        )
        try:
            with store.lock():
                ticket = store.prepare_acquisition(
                    enrollment_store_id=uuid4(),
                    issue_request_id=uuid4(),
                    provisioner_id=claim.provisioner_id,
                    origin="http://localhost:8080",
                    plan_id=claim.plan_id,
                    plan_hash=claim.plan_hash,
                )
                store.fail_acquisition(ticket, status)
                before = store.acquisitions()
                with pytest.raises(ProvisionError, match="reconciliation_required"):
                    store.migrate_for_rejections()
                assert store.version == 2 and store.acquisitions() == before
                assert store.execution_blocked_local()
        finally:
            store.close()


def test_rejection_migration_rollback_keeps_old_format_and_evidence(monkeypatch):
    from bees_host.provisioning import supervisor_store as module

    with private_bridge_directory() as base:
        claim = claim_fixture()
        store = SupervisorStore.initialize_for_acquisition(
            base / "ledger", installation_id=claim.installation_id, host_id=claim.host_id
        )
        try:
            schema = module.ACQUISITION_SCHEMA_V3
            with store.lock():
                monkeypatch.setattr(module, "ACQUISITION_SCHEMA_V3", (schema[0], "invalid SQL"))
                with pytest.raises(ProvisionError, match="state_unavailable"):
                    store.migrate_for_rejections()
                assert store.version == 2
                assert store.connection.execute("PRAGMA user_version").fetchone()[0] == 2
                monkeypatch.setattr(module, "ACQUISITION_SCHEMA_V3", schema)
                store.migrate_for_rejections()
                assert store.version == 3
        finally:
            store.close()
