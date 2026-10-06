"""CLI/criptografia/sistema operacional reais contra API e banco descartáveis."""

import json
import sys
from uuid import uuid4

import httpx
from bees_host import cli
from bees_host.contracts import pairing_fingerprint as local_fingerprint
from bees_host.runtime import Runtime
from bees_host.security import check_private, native_cipher
from bees_host.state import StateStore
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from bees_api.app import create_app
from bees_api.config import Settings
from bees_core.security.hosts import HostService, pairing_fingerprint


def test_fingerprint_versions_match_without_core_dependency_in_runtime():
    install, invite, host = uuid4(), uuid4(), uuid4()
    credential = "bh_" + "C" * 43
    assert local_fingerprint(install, invite, host, credential) == pairing_fingerprint(
        install, invite, host, credential
    )


def test_cli_native_pair_confirm_report_restart_revoke_against_real_api(
    tmp_path, monkeypatch, capsys
):
    origin = "http://127.0.0.1:8000"
    settings = Settings(data_dir=tmp_path / "api", web_dist=tmp_path / "missing")
    monkeypatch.setenv("BEES_HOST_STATE_KEY", Fernet.generate_key().decode())
    store = StateStore(tmp_path / "host", native_cipher())
    with TestClient(create_app(settings), base_url=origin) as api:
        issued = api.app.state.identity.issue_bootstrap()
        assert (
            api.post(
                "/api/v1/auth/setup",
                json={
                    "bootstrap_token": issued,
                    "name": "Hospedeiro teste",
                    "password": "senha descartavel de teste 123456",
                },
                headers={"Origin": origin},
            ).status_code
            == 200
        )
        invite = HostService(api.app.state.database).issue_invite(uuid4())
        bootstrap = store.directory / "bootstrap.json"
        bootstrap.write_text(
            json.dumps(
                {
                    "origin": origin,
                    "installation_id": str(invite.installation_id),
                    "invite_id": str(invite.invite_id),
                    "invite_token": invite.invite_token.get_secret_value(),
                    "expires_at": invite.expires_at,
                }
            ),
            encoding="utf-8",
        )
        check_private(bootstrap, protect=True)

        def bridge(request):
            assert not bootstrap.exists()
            response = api.request(
                request.method,
                request.url.path,
                content=request.content,
                headers=dict(request.headers),
            )
            return httpx.Response(response.status_code, content=response.content)

        monkeypatch.setattr(
            cli, "Runtime", lambda state: Runtime(state, transport=httpx.MockTransport(bridge))
        )
        args = ["--state-dir", str(store.directory), "--once"]
        assert cli.main(args + ["--bootstrap-file", str(bootstrap)]) == 0
        public = json.loads((store.directory / "public-status.json").read_bytes())
        assert public["status"] == "pending"
        session = HostService(api.app.state.database).get(store.load().host_id)
        headers = {
            "Origin": origin,
            "X-Bees-CSRF": api.get("/api/v1/auth/status").json()["csrf_token"],
        }
        path = "/api/v1/environments/hosts/" + public["host_id"]
        active = api.post(
            path + "/confirm",
            headers=headers,
            json={
                "expected_revision": session["revision"],
                "client_request_id": str(uuid4()),
                "fingerprint": public["fingerprint"],
            },
        )
        assert active.status_code == 200
        assert cli.main(args) == 0
        after = HostService(api.app.state.database).get(store.load().host_id)
        assert after["last_report_sequence"] == 1 and after["online"]
        assert after["diagnostic"]["provisionable"] is False
        assert all(type(v) is bool for v in after["diagnostic"]["probe"].values())
        assert cli.main(args) == 0
        assert (
            HostService(api.app.state.database).get(store.load().host_id)["last_report_sequence"]
            == 2
        )
        assert (
            api.post(
                path + "/revoke",
                headers=headers,
                json={
                    "expected_revision": active.json()["revision"],
                    "client_request_id": str(uuid4()),
                },
            ).status_code
            == 200
        )
        assert cli.main(args) == 0
        assert store.load().revoked
        assert (
            json.loads((store.directory / "public-status.json").read_bytes())["status"] == "revoked"
        )
        assert capsys.readouterr().out == ""


def test_runtime_import_does_not_load_database_or_control_plane():
    import subprocess

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import bees_host.runtime, sys; "
            "assert not any(name == 'apsw' or name.startswith(('bees_core', 'bees_api')) "
            "for name in sys.modules)",
        ],
        capture_output=True,
        check=False,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr.decode(errors="replace")
