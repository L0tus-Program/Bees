import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest
from bees_host import cli
from bees_host.contracts import HostState
from bees_host.errors import HostError
from bees_host.runtime import Runtime
from bees_host.security import FernetCipher, check_private
from bees_host.state import StateStore
from cryptography.fernet import Fernet


class Server:
    def __init__(self, state):
        now = datetime.now(UTC).isoformat()
        self.registered = False
        self.session = {
            "host_id": str(state.host_id),
            "installation_id": str(state.installation_id),
            "status": "pending",
            "revision": 1,
            "report_revision": 0,
            "fingerprint": state.expected_fingerprint,
            "created_at": now,
            "paired_at": None,
            "pairing_expires_at": (datetime.now(UTC) + timedelta(minutes=15)).isoformat(),
            "last_seen": None,
            "diagnostic": None,
            "online": False,
            "provisionable": False,
            "confirmable": True,
            "last_report_sequence": 0,
            "last_report_request_id": None,
        }
        self.requests = []
        self.reports = []
        self.credential = state.host_credential.get_secret_value()
        self.lost_pair = False
        self.lost_report = None
        self.revoked = False

    def activate(self):
        self.session.update(
            status="active", confirmable=False, paired_at=datetime.now(UTC).isoformat()
        )

    def __call__(self, request):
        assert request.url.host in ("localhost", "127.0.0.1")
        assert request.headers["Origin"] == "http://localhost:8080"
        self.requests.append(request)
        path = request.url.path.rsplit("/", 1)[1]
        if path == "exchange":
            assert "authorization" not in request.headers
            payload = json.loads(request.content)
            assert payload["host_credential"] == self.credential
            assert payload["invite_token"].startswith("bi_")
            self.registered = True
            if self.lost_pair:
                self.lost_pair = False
                raise httpx.ReadTimeout("corpo de erro secreto", request=request)
        else:
            assert request.headers["Authorization"] == "Bearer " + self.credential
            if self.revoked or not self.registered:
                return httpx.Response(401, json={"error": {"message": self.credential}})
        if path == "report":
            payload = json.loads(request.content)
            self.reports.append(payload)
            if self.lost_report == "before":
                self.lost_report = None
                raise httpx.ReadTimeout("corpo de erro secreto", request=request)
            self.session.update(
                last_report_request_id=payload["client_request_id"],
                last_report_sequence=payload["sequence"],
                report_revision=payload["expected_revision"] + 1,
                last_seen=datetime.now(UTC).isoformat(),
                online=True,
                diagnostic={
                    "driver": payload["driver"],
                    "probe": payload["probe"],
                    "status": "driver_unavailable",
                    "provisionable": False,
                },
            )
            if self.lost_report == "after":
                self.lost_report = None
                raise httpx.ReadTimeout("corpo de erro secreto", request=request)
        return httpx.Response(200, json=self.session)


@pytest.fixture
def setup(tmp_path):
    store = StateStore(tmp_path / "state", FernetCipher(Fernet.generate_key().decode()))
    state = HostState(
        origin="http://localhost:8080",
        installation_id=uuid4(),
        host_id=uuid4(),
        expected_fingerprint="ABCD-EF12-3456-7890",
        host_credential="bh_" + "H" * 43,
        pair_request_id=uuid4(),
        invite_token="bi_" + "I" * 43,
        invite_expires_at=datetime.now(UTC) + timedelta(minutes=5),
    )
    store.save(state)
    server = Server(state)
    return store, server


def probe():
    return "hyperv", {"platform": True, "module": False, "service": False}


def test_pending_only_polls_pairing_and_publishes_public_fingerprint(setup):
    store, server = setup
    runtime = Runtime(
        store,
        transport=httpx.MockTransport(server),
        probe=lambda: pytest.fail("Pending iniciou diagnóstico"),
    )
    try:
        assert runtime.step().status == "pending"
        assert runtime.step().status == "pending"
        assert not server.reports
        public = json.loads((store.directory / "public-status.json").read_bytes())
        assert set(public) == {"host_id", "installation_id", "fingerprint", "status"}
        assert public["fingerprint"] == "ABCD-EF12-3456-7890"
        assert runtime.state.registered and runtime.state.invite_token is None
        assert runtime.client._trust_env is False
    finally:
        runtime.close()


def test_lost_exchange_reconciles_by_credential_after_restart(setup):
    store, server = setup
    server.lost_pair = True
    first = Runtime(store, transport=httpx.MockTransport(server), probe=probe)
    with pytest.raises(HostError, match="connection_unavailable"):
        first.step()
    first.close()
    assert store.load().invite_token is not None
    second = Runtime(store, transport=httpx.MockTransport(server), probe=probe)
    assert second.step().status == "pending"
    second.close()
    assert store.load().registered and store.load().invite_token is None
    assert len([r for r in server.requests if r.url.path.endswith("exchange")]) == 1


@pytest.mark.parametrize("point", ["before", "after"])
def test_lost_report_keeps_payload_then_reconciles_without_new_uuid_or_probe(setup, point):
    store, server = setup
    runtime = Runtime(store, transport=httpx.MockTransport(server), probe=probe)
    runtime.step()
    server.activate()
    server.lost_report = point
    with pytest.raises(HostError, match="connection_unavailable"):
        runtime.step()
    pending = store.load().pending_report
    assert pending is not None
    runtime.close()
    restart = Runtime(
        store,
        transport=httpx.MockTransport(server),
        probe=lambda: pytest.fail("Retry refez o diagnóstico"),
    )
    result = restart.step()
    restart.close()
    assert result.last_report_sequence == 1 and store.load().pending_report is None
    assert all(value == pending.model_dump(mode="json") for value in server.reports)
    assert len(server.reports) == (2 if point == "before" else 1)


def test_active_reports_sequence_and_revocation_stops_without_repair(setup):
    store, server = setup
    runtime = Runtime(store, transport=httpx.MockTransport(server), probe=probe)
    runtime.step()
    server.activate()
    assert runtime.step().last_report_sequence == 1
    assert runtime.step().last_report_sequence == 2
    server.revoked = True
    with pytest.raises(HostError, match="host_unauthorized"):
        runtime.step()
    count = len(server.requests)
    with pytest.raises(HostError, match="host_revoked"):
        runtime.step()
    assert count == len(server.requests)
    assert store.load().revoked
    assert json.loads((store.directory / "public-status.json").read_bytes())["status"] == "revoked"
    runtime.close()


def test_expired_pending_publishes_expired_and_does_not_report(setup):
    store, server = setup
    runtime = Runtime(store, transport=httpx.MockTransport(server), probe=probe)
    runtime.step()
    server.session["confirmable"] = False
    runtime.step()
    assert store.load().expired and not server.reports
    assert json.loads((store.directory / "public-status.json").read_bytes())["status"] == "expired"
    with pytest.raises(HostError, match="pairing_expired"):
        runtime.step()
    runtime.close()


@pytest.mark.parametrize(
    "change",
    [
        {"host_id": str(uuid4())},
        {"installation_id": str(uuid4())},
        {"provisionable": True},
        {"revision": "1"},
        {"arbitrary_command": "delete-files"},
        {"fingerprint": "fake"},
    ],
)
def test_server_cannot_inject_other_identity_capability_or_command(setup, change):
    store, server = setup
    server.session.update(change)
    runtime = Runtime(store, transport=httpx.MockTransport(server), probe=probe)
    with pytest.raises(HostError, match="host_protocol_invalid"):
        runtime.step()
    assert not server.reports
    runtime.close()


def test_redirect_not_followed_and_error_body_not_exposed(setup):
    store, server = setup
    requests = []

    def redirect(request):
        requests.append(request)
        return httpx.Response(
            307, headers={"Location": "http://evil.example/"}, text=server.credential
        )

    runtime = Runtime(store, transport=httpx.MockTransport(redirect), probe=probe)
    with pytest.raises(HostError) as error:
        runtime.step()
    assert len(requests) == 1 and server.credential not in str(error.value)
    runtime.close()


def test_response_size_bounded(setup):
    store, _ = setup
    runtime = Runtime(
        store,
        transport=httpx.MockTransport(lambda request: httpx.Response(200, content=b" " * 16385)),
        probe=probe,
    )
    with pytest.raises(HostError, match="host_protocol_invalid"):
        runtime.step()
    runtime.close()


def test_cli_bootstrap_consumed_before_actual_protocol_and_no_secrets_in_output(
    setup, monkeypatch, capsys
):
    store, server = setup
    state = store.load()
    # Uma identidade nova via bootstrap de fixture, em vez de usar dados padrão.
    store.path.unlink()
    bootstrap = store.directory / "bootstrap.json"
    bootstrap.write_text(
        json.dumps(
            {
                "origin": state.origin,
                "installation_id": str(state.installation_id),
                "invite_id": str(uuid4()),
                "invite_token": "bi_" + "I" * 43,
                "expires_at": (datetime.now(UTC) + timedelta(minutes=5)).isoformat(),
            }
        )
    )
    check_private(bootstrap, protect=True)
    monkeypatch.setattr(cli, "native_cipher", lambda: store.cipher)

    def runtime_factory(store):
        assert not bootstrap.exists() and store.path.exists()
        server = Server(store.load())
        return Runtime(store, transport=httpx.MockTransport(server), probe=probe)

    monkeypatch.setattr(cli, "Runtime", runtime_factory)
    assert (
        cli.main(
            ["--state-dir", str(store.directory), "--bootstrap-file", str(bootstrap), "--once"]
        )
        == 0
    )
    output = capsys.readouterr()
    assert output.out == "" and output.err == ""
    assert store.load().registered


def test_cli_error_message_does_not_leak_remote_body(setup, monkeypatch, capsys):
    store, server = setup
    monkeypatch.setattr(cli, "native_cipher", lambda: store.cipher)
    monkeypatch.setattr(
        cli,
        "Runtime",
        lambda store: Runtime(
            store,
            transport=httpx.MockTransport(
                lambda request: httpx.Response(400, text=server.credential + " bi_" + "I" * 43)
            ),
            probe=probe,
        ),
    )
    assert cli.main(["--state-dir", str(store.directory), "--once"]) == 1
    output = capsys.readouterr()
    assert output.err == "Bees host: host_protocol_invalid\n" and output.out == ""


def test_report_wrong_receipt_keeps_pending_journal(setup):
    store, server = setup

    def handler(request):
        response = server(request)
        if request.url.path.endswith("report"):
            wrong = dict(server.session, last_report_request_id=str(uuid4()))
            return httpx.Response(200, json=wrong)
        return response

    runtime = Runtime(store, transport=httpx.MockTransport(handler), probe=probe)
    runtime.step()
    server.activate()
    with pytest.raises(HostError, match="host_protocol_invalid"):
        runtime.step()
    assert store.load().pending_report is not None
    runtime.close()
