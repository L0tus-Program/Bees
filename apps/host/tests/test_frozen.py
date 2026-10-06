"""Aceite opcional do artefato Windows real, com HTTP/API/estado descartáveis.

BEES_HOST_EXECUTABLE aponta para o bees-host.exe onedir já compilado. Não faz
build, download, publicação, instalação do sistema ou habilitação de hipervisor.
"""

import json
import os
import shutil
import socket
import subprocess
import threading
import time
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from bees_host.security import check_private, native_cipher
from bees_host.state import StateStore
from fastapi.testclient import TestClient

from bees_api.app import create_app
from bees_api.config import Settings
from bees_core.security.hosts import HostService

pytestmark = pytest.mark.skipif(
    os.name != "nt" or not os.environ.get("BEES_HOST_EXECUTABLE"),
    reason="Artefato Windows opt-in: informe BEES_HOST_EXECUTABLE.",
)


@pytest.fixture(scope="module")
def frozen(tmp_path_factory):
    source = Path(os.environ["BEES_HOST_EXECUTABLE"]).absolute()
    assert source.is_file() and source.suffix.lower() == ".exe", "Artefato informado não existe."
    target = tmp_path_factory.mktemp("pacote distribuído ç") / "runtime com espaços á"
    # Copia todos os arquivos onedir, incluindo DLLs/extensões e _internal.
    shutil.copytree(source.parent, target)
    environment = dict(os.environ)
    environment.pop("PYTHONPATH", None)
    environment.pop("PYTHONHOME", None)
    windows = Path(environment["SYSTEMROOT"])
    environment["PATH"] = os.pathsep.join(
        [str(windows / "System32"), str(windows / "System32" / "WindowsPowerShell" / "v1.0")]
    )
    environment["PYTHONNOUSERSITE"] = "1"
    return SimpleNamespace(executable=target / source.name, environment=environment)


@pytest.fixture
def harness(tmp_path):
    context = SimpleNamespace(api=None, drop=None, requests=[], reports=[])

    class Gateway(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            self.forward()

        def do_POST(self):
            self.forward()

        def forward(self):
            size = int(self.headers.get("Content-Length", "0"))
            assert 0 <= size <= 16384
            body = self.rfile.read(size)
            context.requests.append((self.command, self.path))
            response = context.api.request(
                self.command, self.path, headers=dict(self.headers), content=body
            )
            if self.path.endswith("/report"):
                context.reports.append(json.loads(body))
            # Fecha conexão depois de a API confirmar o efeito, antes do receipt no fio.
            if response.status_code == 200 and context.drop and self.path.endswith(context.drop):
                context.drop = None
                self.connection.shutdown(socket.SHUT_RDWR)
                self.connection.close()
                return
            self.send_response(response.status_code)
            for key, value in response.headers.multi_items():
                if key.lower() not in ("content-length", "connection", "transfer-encoding"):
                    self.send_header(key, value)
            self.send_header("Content-Length", str(len(response.content)))
            self.end_headers()
            self.wfile.write(response.content)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Gateway)
    server.daemon_threads = True
    port = server.server_address[1]
    origin = f"http://127.0.0.1:{port}"
    settings = Settings(data_dir=tmp_path / "api", web_dist=tmp_path / "missing", port=port)
    with TestClient(create_app(settings), base_url=origin) as api:
        context.api = api
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with httpx.Client(base_url=origin, trust_env=False, timeout=10) as browser:
                token = api.app.state.identity.issue_bootstrap()
                result = browser.post(
                    "/api/v1/auth/setup",
                    headers={"Origin": origin},
                    json={
                        "bootstrap_token": token,
                        "name": "Pessoa teste EXE",
                        "password": "senha descartável de teste EXE 123456",
                    },
                )
                assert result.status_code == 200
                csrf = browser.get("/api/v1/auth/status").json()["csrf_token"]
                yield SimpleNamespace(
                    origin=origin,
                    api=api,
                    browser=browser,
                    context=context,
                    service=HostService(api.app.state.database),
                    headers={"Origin": origin, "X-Bees-CSRF": csrf},
                )
        finally:
            server.shutdown()
            thread.join(timeout=5)
            server.server_close()


def host_store(tmp_path):
    return StateStore(tmp_path / "estado privado ç", native_cipher())


def write_bootstrap(store, harness, *, expired=False):
    invite = harness.service.issue_invite(uuid4())
    path = store.directory / "bootstrap único á.json"
    payload = {
        "origin": harness.origin,
        "installation_id": str(invite.installation_id),
        "invite_id": str(invite.invite_id),
        "invite_token": invite.invite_token.get_secret_value(),
        "expires_at": (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
        if expired
        else invite.expires_at,
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    check_private(path, protect=True)
    return path


def run(frozen, tmp_path, *args, timeout=25):
    cwd = tmp_path / "trabalho fora do checkout á"
    cwd.mkdir(exist_ok=True)
    return subprocess.run(
        [str(frozen.executable), *map(str, args)],
        cwd=cwd,
        env=frozen.environment,
        capture_output=True,
        timeout=timeout,
        check=False,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )


def assert_quiet(result, expected=0):
    assert result.returncode == expected, result.stderr.decode(errors="replace")
    assert result.stdout == b""
    assert b"bi_" not in result.stderr and b"bh_" not in result.stderr


def approve(harness, store):
    state = store.load()
    dto = harness.service.get(state.host_id)
    public = json.loads((store.directory / "public-status.json").read_bytes())
    assert public["fingerprint"] == dto["fingerprint"] == state.expected_fingerprint
    response = harness.browser.post(
        f"/api/v1/environments/hosts/{state.host_id}/confirm",
        headers=harness.headers,
        json={
            "expected_revision": dto["revision"],
            "fingerprint": public["fingerprint"],
            "client_request_id": str(uuid4()),
        },
    )
    assert response.status_code == 200
    return response.json()


def test_frozen_private_check_outside_checkout_and_no_python(frozen, tmp_path):
    store = host_store(tmp_path)
    result = run(frozen, tmp_path, "--check-private", store.directory, "--directory")
    assert_quiet(result)
    assert not store.path.exists() and list(store.directory.iterdir()) == []
    insecure = store.directory / "inseguro privado.json"
    insecure.write_text("conteúdo não pode aparecer na saída")
    result = run(frozen, tmp_path, "--check-private", insecure)
    assert_quiet(result, 1)
    assert result.stderr.strip() == b"Bees host: private_path_required"
    assert b"inseguro" not in result.stderr


def test_frozen_pair_report_response_loss_restart_and_revocation(frozen, tmp_path, harness):
    store = host_store(tmp_path)
    bootstrap = write_bootstrap(store, harness)
    args = ("--state-dir", store.directory, "--once")
    assert_quiet(run(frozen, tmp_path, *args, "--bootstrap-file", bootstrap))
    assert not bootstrap.exists() and store.load().registered
    active = approve(harness, store)
    harness.context.drop = "/report"
    failed = run(frozen, tmp_path, *args)
    assert_quiet(failed, 1)
    assert failed.stderr.strip() == b"Bees host: connection_unavailable"
    pending = store.load().pending_report
    assert pending is not None
    accepted = harness.service.get(store.load().host_id)
    assert accepted["last_report_sequence"] == 1 and accepted["online"] is True
    assert_quiet(run(frozen, tmp_path, *args))
    assert store.load().pending_report is None
    # Reconciliação por UUID/revisão/sequência, sem reexecutar/repetir o relatório.
    assert len(harness.context.reports) == 1
    assert harness.context.reports[0] == pending.model_dump(mode="json")
    assert_quiet(run(frozen, tmp_path, *args))
    dto = harness.service.get(store.load().host_id)
    assert dto["last_report_sequence"] == 2 and dto["provisionable"] is False
    assert dto["diagnostic"]["driver"] == "hyperv"
    assert set(dto["diagnostic"]["probe"]) == {"platform", "module", "service"}
    assert all(type(value) is bool for value in dto["diagnostic"]["probe"].values())
    response = harness.browser.post(
        f"/api/v1/environments/hosts/{store.load().host_id}/revoke",
        headers=harness.headers,
        json={"expected_revision": active["revision"], "client_request_id": str(uuid4())},
    )
    assert response.status_code == 200
    assert_quiet(run(frozen, tmp_path, *args))
    assert store.load().revoked
    assert json.loads((store.directory / "public-status.json").read_bytes())["status"] == "revoked"
    count = len(harness.context.requests)
    assert_quiet(run(frozen, tmp_path, *args), 1)
    assert len(harness.context.requests) == count


def test_frozen_lost_exchange_reuses_identity_and_never_claims_again(frozen, tmp_path, harness):
    store = host_store(tmp_path)
    bootstrap = write_bootstrap(store, harness)
    args = ("--state-dir", store.directory, "--once")
    harness.context.drop = "/exchange"
    assert_quiet(run(frozen, tmp_path, *args, "--bootstrap-file", bootstrap), 1)
    state = store.load()
    assert not bootstrap.exists() and state is not None and not state.registered
    hosts = harness.service.list()
    assert len(hosts) == 1 and hosts[0]["host_id"] == str(state.host_id)
    assert_quiet(run(frozen, tmp_path, *args))
    assert store.load().host_id == state.host_id and store.load().registered
    assert len(harness.service.list()) == 1
    assert (
        len([request for request in harness.context.requests if request[1].endswith("exchange")])
        == 1
    )
    assert store.load().invite_token is None


def test_frozen_expired_ticket_fails_before_any_request(frozen, tmp_path, harness):
    store = host_store(tmp_path)
    bootstrap = write_bootstrap(store, harness, expired=True)
    count = len(harness.context.requests)
    result = run(
        frozen, tmp_path, "--state-dir", store.directory, "--bootstrap-file", bootstrap, "--once"
    )
    assert_quiet(result, 1)
    assert result.stderr.strip() == b"Bees host: bootstrap_invalid"
    assert len(harness.context.requests) == count and not store.path.exists()
    assert harness.service.list() == []


def test_frozen_insecure_bootstrap_fails_without_repairing_acl(frozen, tmp_path, harness):
    store = host_store(tmp_path)
    bootstrap = store.directory / "bootstrap ACL herdada.json"
    bootstrap.write_text("{}")  # Herda ACL mas não possui DACL protegida própria.
    count = len(harness.context.requests)
    result = run(
        frozen, tmp_path, "--state-dir", store.directory, "--bootstrap-file", bootstrap, "--once"
    )
    assert_quiet(result, 1)
    assert result.stderr.strip() == b"Bees host: private_path_required"
    assert not store.path.exists() and len(harness.context.requests) == count
    assert bootstrap.read_text() == "{}"


def test_frozen_multiple_instances_do_not_pair_or_report_twice(frozen, tmp_path, harness):
    store = host_store(tmp_path)
    bootstrap = write_bootstrap(store, harness)
    cwd = tmp_path / "processo separado á"
    cwd.mkdir()
    process = subprocess.Popen(
        [
            str(frozen.executable),
            "--state-dir",
            str(store.directory),
            "--bootstrap-file",
            str(bootstrap),
        ],
        cwd=cwd,
        env=frozen.environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    try:
        deadline = time.monotonic() + 15
        public = store.directory / "public-status.json"
        while not public.exists() and time.monotonic() < deadline:
            assert process.poll() is None
            time.sleep(0.1)
        assert public.exists()
        count = len(harness.context.requests)
        result = run(frozen, tmp_path, "--state-dir", store.directory, "--once")
        assert_quiet(result, 1)
        assert result.stderr.strip() == b"Bees host: host_already_running"
        assert len(harness.service.list()) == 1 and not harness.context.reports
        assert len(harness.context.requests) == count
    finally:
        process.terminate()
        process.communicate(timeout=10)
