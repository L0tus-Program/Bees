"""O modo escolhido no reinício deve respeitar a identidade persistida."""

import asyncio
import json
import os
import subprocess
import sys
import time

import pytest
from test_executor_gateway import Server

from bees_core.executors import ExecutorService
from bees_executor.client import CredentialBindingError, ExecutorClient, exchange
from bees_executor.journal import Journal


@pytest.mark.parametrize("kind", ["personal", "computer_runtime"])
@pytest.mark.parametrize("saved_kind", [None, "", 42, [], {}, "unknown", "opposite"])
def test_saved_kind_must_match_before_connecting(tmp_path, monkeypatch, kind, saved_kind):
    calls = []
    client = ExecutorClient("http://chosen.test", tmp_path, kind, None)

    def connect(*args, **kwargs):
        calls.append((args, kwargs))
        client.stopped.set()
        raise OSError("stop fixture")

    monkeypatch.setattr("bees_executor.websocket.AuthenticatedConnect", connect)
    credential = {"api": client.api, "credential": "be_" + "A" * 43}
    if saved_kind is not None:
        credential["kind"] = (
            ("computer_runtime" if kind == "personal" else "personal")
            if saved_kind == "opposite"
            else saved_kind
        )
    path = tmp_path / "credential.json"
    path.write_text(json.dumps(credential))
    before = path.read_bytes()
    with pytest.raises(CredentialBindingError, match="^executor_kind_mismatch$"):
        asyncio.run(client.run_forever(credential))
    assert calls == []
    assert client.credential is None
    assert path.read_bytes() == before


@pytest.mark.parametrize("kind", ["personal", "computer_runtime"])
def test_matching_saved_kind_keeps_authenticated_connection(tmp_path, monkeypatch, kind):
    calls = []
    client = ExecutorClient("http://chosen.test", tmp_path, kind, None)

    def connect(*args, **kwargs):
        calls.append((args, kwargs))
        client.stopped.set()
        raise OSError("stop fixture")

    monkeypatch.setattr("bees_executor.websocket.AuthenticatedConnect", connect)
    credential = {"api": client.api, "credential": "be_" + "A" * 43, "kind": kind}
    asyncio.run(client.run_forever(credential))
    assert len(calls) == 1
    assert calls[0][1]["additional_headers"] == {
        "Authorization": "Bearer " + credential["credential"]
    }


@pytest.mark.skipif(sys.platform == "win32", reason="Processo POSIX simples.")
@pytest.mark.parametrize("kind", ["personal", "computer_runtime"])
def test_cli_preserves_saved_kind_and_restarts_in_original_mode(tmp_path, monkeypatch, kind):
    server = Server(tmp_path)
    process = None
    authentications = []
    original = ExecutorService.authenticate

    def authenticate(self, credential):
        authentications.append(credential)
        return original(self, credential)

    monkeypatch.setattr(ExecutorService, "authenticate", authenticate)
    try:
        service = ExecutorService(server.database)
        invite = service.invite({"kind": kind, "name": "Tipo persistido"})
        state = tmp_path / "executor"
        credential = exchange(server.url, invite["token"], state, expected_kind=kind)
        path = state / "credential.json"
        before = path.read_bytes()
        journal = Journal(state / "journal.sqlite3")
        journal.begin("completed-command", "files.read")
        journal.finish("completed-command", {"status": "succeeded", "value": "private-evidence"})
        journal.close()
        journal_before = (state / "journal.sqlite3").read_bytes()
        invite_file = tmp_path / "invite.json"
        invite_file.write_text(json.dumps({"token": "unused-private-invite"}))
        invite_before = invite_file.read_bytes()

        def start(mode):
            arguments = (
                ["--root", f"Docs={tmp_path}"]
                if mode == "personal"
                else ["--computers", str(tmp_path / "computers"), "--mode", "single"]
            )
            return subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "bees_executor.cli",
                    mode,
                    "--api",
                    server.url,
                    "--state",
                    str(state),
                    "--invite-file",
                    str(invite_file),
                    *arguments,
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                env=os.environ
                | {
                    "BEES_EXECUTOR_INVITE": "",
                    "PYTHONPATH": os.pathsep.join(p for p in sys.path if p),
                },
            )

        process = start("computer" if kind == "personal" else "personal")
        try:
            output, _ = process.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            pytest.fail("O modo incompatível continuou ativo em vez de recusar antes de conectar.")
        diagnostic = output.decode("utf-8")
        assert process.returncode != 0
        assert "executor_kind_mismatch" in diagnostic
        assert "Traceback" not in diagnostic
        assert credential["credential"] not in diagnostic
        assert "unused-private-invite" not in diagnostic
        assert server.url not in diagnostic
        assert authentications == []
        assert not service.list(kind)[0]["online"]
        assert path.read_bytes() == before
        assert (state / "journal.sqlite3").read_bytes() == journal_before
        assert invite_file.read_bytes() == invite_before

        process = start("personal" if kind == "personal" else "computer")
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline and process.poll() is None:
            if service.list(kind)[0]["online"]:
                break
            time.sleep(0.1)
        assert process.poll() is None
        assert service.list(kind)[0]["online"]
        assert authentications == [credential["credential"]]
        assert len(service.list(kind)) == 1
        assert service.list(kind)[0]["id"] == credential["executor_id"]
        assert path.read_bytes() == before
        assert (state / "journal.sqlite3").read_bytes() == journal_before
        assert invite_file.read_bytes() == invite_before
    finally:
        if process is not None:
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=10)
        server.stop()
