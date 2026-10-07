"""HTTP/API/core/journal reais; backend de hardware falso, sem criar VM."""

import json
import os
import socket
import socketserver
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import pytest
from bees_host.provisioning import http_authority
from bees_host.provisioning.contracts import ProvisionError
from bees_host.provisioning.http_authority import HTTPAuthority
from bees_host.provisioning.runner import Runner
from pydantic import SecretStr
from test_provisioning_interoperability import Template, revoke
from test_provisioning_interoperability import integration as integration

from bees_core.provisioning import OPERATIONS


@contextmanager
def api_process(directory: Path, *, drop_receipt: bool = False):
    """Filho próprio, sem credencial em argv/ambiente; fault injection só nesta fixture."""
    with socket.socket() as reserved:
        reserved.bind(("127.0.0.1", 0))
        port = reserved.getsockname()[1]
    origin = f"http://127.0.0.1:{port}"
    log = directory / "http-fixture.log"
    with log.open("wb") as output:
        child = subprocess.Popen(
            [
                sys.executable,
                str(Path(__file__).resolve()),
                "--serve",
                str(directory),
                str(port),
                *(["--drop-receipt"] if drop_receipt else []),
            ],
            stdin=subprocess.DEVNULL,
            stdout=output,
            stderr=subprocess.STDOUT,
            cwd=directory,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        try:
            deadline = time.monotonic() + 15
            with httpx.Client(trust_env=False, timeout=0.3) as probe:
                while time.monotonic() < deadline:
                    if child.poll() is not None:
                        pytest.fail("Servidor descartável encerrou antes do teste.")
                    try:
                        if probe.get(origin + "/api/v1/health").status_code == 200:
                            break
                    except httpx.HTTPError:
                        pass
                    time.sleep(0.05)
                else:
                    pytest.fail("Servidor descartável não ficou disponível.")
            yield origin, child
        finally:
            if child.poll() is None:
                child.terminate()
            child.wait(timeout=10)


@contextmanager
def remote(integration, *, drop_receipt=False):
    core, _, _, claim, _, local, _ = integration
    with api_process(core.database.path.parent, drop_receipt=drop_receipt) as (origin, child):
        with HTTPAuthority(
            origin,
            SecretStr(local.token),
            installation_id=claim.installation_id,
            host_id=claim.host_id,
            provisioner_id=claim.provisioner_id,
        ) as authority:
            yield authority, child


def test_http_actual_runner_completes_closed_sequence_without_ready_environment(integration):
    core, _, plan, _, journal, _, hardware = integration
    with remote(integration) as (authority, _):
        runner = Runner(journal, authority, hardware, Template())
        for operation in OPERATIONS:
            assert runner.execute_next().verified
            assert hardware.effects[-1] == operation
    assert journal.claim.status == "confirmed"
    assert core.get(UUID(plan["plan_id"]))["status"] == "hardware_verified"
    with core.database.transaction(write=False) as connection:
        assert connection.execute("SELECT status FROM environments").get == "provisioning"
        assert connection.execute("SELECT count(*) FROM provisioning_effects").get == 6


def test_http_revocation_before_go_prevents_effect_and_keeps_unknown(integration):
    core, _, plan, _, journal, _, hardware = integration
    hardware.before_go = lambda: revoke(core, plan)
    with remote(integration) as (authority, _):
        runner = Runner(journal, authority, hardware, Template())
        with pytest.raises(ProvisionError):
            runner.execute_next()
        assert hardware.effects == []
        assert journal.operations()["create_vhd"]["status"] == "unknown"
        with pytest.raises(ProvisionError, match="reconciliation_required"):
            runner.execute_next()
    assert hardware.effects == []


def test_http_failed_response_after_committed_receipt_never_repeats(integration):
    core, _, _, _, journal, _, hardware = integration
    with remote(integration, drop_receipt=True) as (authority, _):
        runner = Runner(journal, authority, hardware, Template())
        with pytest.raises(ProvisionError):
            runner.execute_next()
        assert hardware.effects == ["create_vhd"]
        with core.database.transaction(write=False) as connection:
            assert connection.execute("SELECT status FROM provisioning_effects").get == "confirmed"
        assert journal.operations()["create_vhd"]["status"] == "unknown"
        with pytest.raises(ProvisionError, match="reconciliation_required"):
            runner.execute_next()
    assert hardware.effects == ["create_vhd"]


def test_http_dispatch_replay_cannot_grant_second_go(integration):
    core, _, _, claim, _, _, hardware = integration
    request_id = uuid4()
    with remote(integration) as (authority, _):
        first = authority.begin_dispatch(claim, "create_vhd", request_id)
        replay = authority.begin_dispatch(first.claim, "create_vhd", request_id)
    assert first.dispatch_allowed is True and first.cached is False
    assert replay.dispatch_allowed is False and replay.cached is True
    assert first.effect_request_id == replay.effect_request_id
    with core.database.transaction(write=False) as connection:
        assert connection.execute("SELECT count(*) FROM provisioning_effects").get == 1
    assert hardware.effects == []


def test_http_renew_preserves_binding_and_replay_does_not_renew_again(integration):
    _, _, _, claim, _, _, _ = integration
    request_id = uuid4()
    with remote(integration) as (authority, _):
        renewed = authority.renew(claim, request_id)
        replay = authority.renew(renewed, request_id)
        current = authority.assert_current(replay)
    assert renewed.claim_id == claim.claim_id
    assert renewed.owner_id == claim.owner_id and renewed.generation == claim.generation
    assert renewed.revision > claim.revision
    assert renewed.lease_expires_at == replay.lease_expires_at == current.lease_expires_at
    assert current.revision == replay.revision


def test_http_server_loss_before_intent_blocks_go(integration):
    _, _, _, _, journal, _, hardware = integration
    with remote(integration) as (authority, child):
        child.terminate()
        child.wait(timeout=10)
        with pytest.raises(ProvisionError):
            Runner(journal, authority, hardware, Template()).execute_next()
    assert journal.operations() == {}
    assert hardware.effects == []


def test_http_claim_replay_keeps_owner_and_rejects_second_owner(integration):
    core, _, _, claim, _, _, _ = integration
    with core.database.transaction(write=False) as connection:
        request_id = UUID(
            connection.execute("SELECT client_request_id FROM provisioning_claims").get
        )
    with remote(integration) as (authority, _):
        replay = authority.claim(claim.plan_id, claim.plan_hash, claim.owner_id, request_id)
        assert replay.claim_id == claim.claim_id
        assert replay.generation == claim.generation
        with pytest.raises(ProvisionError):
            authority.claim(claim.plan_id, claim.plan_hash, uuid4(), uuid4())
    with core.database.transaction(write=False) as connection:
        assert connection.execute("SELECT count(*) FROM provisioning_claims").get == 1


def test_http_unknown_remains_quarantined_and_never_grants_dispatch(integration):
    core, _, _, claim, _, _, hardware = integration
    with remote(integration) as (authority, _):
        permit = authority.begin_dispatch(claim, "create_vhd", uuid4())
        request_id = uuid4()
        unknown = authority.mark_unknown(permit.claim, permit.effect_request_id, request_id)
        replay = authority.mark_unknown(permit.claim, permit.effect_request_id, request_id)
        assert unknown.status == replay.status == "outcome_unknown"
        assert unknown.revision == replay.revision
        with pytest.raises(ProvisionError):
            authority.assert_current(unknown)
    with core.database.transaction(write=False) as connection:
        assert (
            connection.execute("SELECT status FROM provisioning_effects").get == "outcome_unknown"
        )
    assert hardware.effects == []


@pytest.mark.parametrize("phase", ["headers", "body"])
def test_http_deadline_cancels_slow_drip_without_waiting_for_inactivity(monkeypatch, phase):
    """Cada fragmento chega antes do read timeout; o prazo total deve cancelar o socket."""
    ids = dict(installation_id=uuid4(), host_id=uuid4(), provisioner_id=uuid4())
    body = json.dumps(
        {**{key: str(value) for key, value in ids.items()}, "revision": 1, "status": "active"}
    ).encode()
    stop = threading.Event()

    class Drip(socketserver.BaseRequestHandler):
        def handle(self):
            self.request.settimeout(2)
            self.request.recv(8192)
            try:
                if phase == "headers":
                    prefix = b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nX-Slow: "
                    data = b"x" * 100
                else:
                    prefix = (
                        b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                        + b"Content-Length: "
                        + str(len(body)).encode()
                        + b"\r\n\r\n"
                    )
                    data = body
                self.request.sendall(prefix)
                for value in data:
                    if stop.wait(0.05):
                        break
                    self.request.sendall(bytes([value]))
            except OSError:
                pass

    with socketserver.ThreadingTCPServer(("127.0.0.1", 0), Drip) as server:
        thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=0.05))
        thread.start()
        monkeypatch.setattr(http_authority, "REQUEST_BUDGET", 0.3)
        try:
            with HTTPAuthority(
                f"http://127.0.0.1:{server.server_address[1]}", SecretStr("bp_" + "a" * 43), **ids
            ) as authority:
                started = time.monotonic()
                with pytest.raises(ProvisionError, match="connection_unavailable"):
                    authority.session()
                assert time.monotonic() - started < 1.0
        finally:
            stop.set()
            server.shutdown()
            thread.join(timeout=2)
            assert not thread.is_alive()


def serve(directory: Path, port: int, *, drop_receipt: bool):
    """Servidor real de teste; a falha substitui uma resposta após o commit canônico."""
    import uvicorn
    from cryptography.fernet import Fernet
    from starlette.responses import JSONResponse

    from bees_api.app import create_app
    from bees_api.config import Settings

    app = create_app(
        Settings(
            data_dir=directory,
            port=port,
            web_dist=directory / "no-web",
            vault_key=SecretStr(Fernet.generate_key().decode()),
        )
    )
    if drop_receipt:

        class DropReceipt:
            def __init__(self, app):
                self.app, self.dropped = app, False

            async def __call__(self, scope, receive, send):
                if (
                    scope["type"] != "http"
                    or scope.get("path") != "/api/v1/provisioner/runtime/receipt"
                    or self.dropped
                ):
                    return await self.app(scope, receive, send)
                self.dropped = True

                async def discard(message):
                    pass

                await self.app(scope, receive, discard)
                await JSONResponse({"fixture": "response_lost"}, status_code=503)(
                    scope, receive, send
                )

        app.add_middleware(DropReceipt)
    uvicorn.run(app, host="127.0.0.1", port=port, access_log=False, log_level="warning")


if __name__ == "__main__":
    assert len(sys.argv) in {4, 5} and sys.argv[1] == "--serve"
    serve(Path(sys.argv[2]), int(sys.argv[3]), drop_receipt="--drop-receipt" in sys.argv[4:])
