"""Corrupção local não equivale à primeira instalação nem autoriza reparear."""

import json
import os
import subprocess
import sys
import time
from uuid import uuid4

import pytest
from test_executor_gateway import Server

from bees_core.executors import ExecutorService
from bees_executor.client import CredentialStateError, exchange, load_credential
from bees_executor.journal import Journal


@pytest.mark.parametrize(
    "raw",
    [b"null", b"[]", b'"private-secret"', b"42", b"true", b"{", b"", b"\xff"],
)
def test_invalid_file_is_preserved(tmp_path, raw):
    path = tmp_path / "credential.json"
    path.write_bytes(raw)
    with pytest.raises(CredentialStateError, match="^executor_credential_invalid$"):
        load_credential(tmp_path)
    assert path.read_bytes() == raw


@pytest.mark.parametrize(
    "field,value",
    [
        ("executor_id", None),
        ("executor_id", "not-a-uuid"),
        ("executor_id", "ABCDEFAB-1234-4234-8234-123456789ABC"),
        ("credential", None),
        ("credential", "be_short"),
        ("credential", 42),
        ("kind", None),
        ("kind", []),
        ("kind", "unknown"),
        ("api", None),
        ("api", ""),
        ("api", "/"),
    ],
)
def test_incomplete_identity_is_preserved(tmp_path, field, value):
    payload = {
        "executor_id": str(uuid4()),
        "credential": "be_" + "A" * 43,
        "kind": "personal",
        "api": "http://127.0.0.1:8080",
    }
    payload[field] = value
    path = tmp_path / "credential.json"
    path.write_text(json.dumps(payload))
    before = path.read_bytes()
    with pytest.raises(CredentialStateError):
        load_credential(tmp_path)
    assert path.read_bytes() == before


def test_only_absent_file_allows_pairing(tmp_path):
    assert load_credential(tmp_path) is None
    (tmp_path / "credential.json").mkdir()
    with pytest.raises(CredentialStateError):
        load_credential(tmp_path)


@pytest.mark.parametrize("error", [FileNotFoundError, PermissionError, OSError])
def test_read_failure_does_not_become_absent(tmp_path, monkeypatch, error):
    path = tmp_path / "credential.json"
    path.write_text("null")

    def fail(*args, **kwargs):
        raise error("private path")

    monkeypatch.setattr(type(path), "read_text", fail)
    with pytest.raises(CredentialStateError):
        load_credential(tmp_path)


@pytest.mark.skipif(sys.platform == "win32", reason="Symlink POSIX sem elevação.")
def test_broken_link_is_not_absent(tmp_path):
    (tmp_path / "credential.json").symlink_to(tmp_path / "missing")
    with pytest.raises(CredentialStateError):
        load_credential(tmp_path)
    assert (tmp_path / "credential.json").is_symlink()


@pytest.mark.parametrize("kind", ["personal", "computer_runtime"])
def test_valid_saved_identity(tmp_path, kind):
    payload = {
        "executor_id": str(uuid4()),
        "credential": "be_" + "A" * 43,
        "kind": kind,
        "api": "http://127.0.0.1:8080/",
    }
    (tmp_path / "credential.json").write_text(json.dumps(payload))
    assert load_credential(tmp_path) == payload


@pytest.mark.skipif(sys.platform == "win32", reason="Processo POSIX simples.")
@pytest.mark.parametrize("kind", ["personal", "computer_runtime"])
@pytest.mark.parametrize("raw", [b"null", b"{"])
def test_cli_blocks_corruption_and_recovers_original_identity(tmp_path, monkeypatch, kind, raw):
    server = Server(tmp_path)
    process = None
    exchanges, authentications = [], []
    original_exchange, original_auth = ExecutorService.exchange, ExecutorService.authenticate

    def tracked_exchange(self, *args, **kwargs):
        exchanges.append(True)
        return original_exchange(self, *args, **kwargs)

    def tracked_auth(self, *args, **kwargs):
        authentications.append(True)
        return original_auth(self, *args, **kwargs)

    try:
        service = ExecutorService(server.database)
        invite = service.invite({"kind": kind, "name": "Estado original"})
        state = tmp_path / "executor"
        credential = exchange(server.url, invite["token"], state, expected_kind=kind)
        path = state / "credential.json"
        original_bytes = path.read_bytes()
        journal = Journal(state / "journal.sqlite3")
        # Um comando interrompido não pode virar unknown antes do diagnóstico.
        journal.begin("interrupted-command", "files.write")
        journal.close()
        journal_before = (state / "journal.sqlite3").read_bytes()
        pending = service.invite({"kind": kind, "name": "Não consumir"})
        invite_file = tmp_path / "invite.json"
        invite_file.write_text(json.dumps({"token": pending["token"]}))
        invite_before = invite_file.read_bytes()
        monkeypatch.setattr(ExecutorService, "exchange", tracked_exchange)
        monkeypatch.setattr(ExecutorService, "authenticate", tracked_auth)
        path.write_bytes(raw)
        arguments = (
            ["personal", "--root", f"Docs={tmp_path}"]
            if kind == "personal"
            else ["computer", "--computers", str(tmp_path / "computers"), "--mode", "single"]
        )

        def start():
            return subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "bees_executor.cli",
                    *arguments,
                    "--api",
                    server.url,
                    "--state",
                    str(state),
                    "--invite-file",
                    str(invite_file),
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                env=os.environ
                | {
                    "BEES_EXECUTOR_INVITE": "",
                    "PYTHONPATH": os.pathsep.join(p for p in sys.path if p),
                },
            )

        process = start()
        try:
            output, _ = process.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            pytest.fail("Estado corrompido permitiu iniciar ou reparear o executor.")
        diagnostic = output.decode()
        assert process.returncode != 0
        assert "executor_credential_invalid" in diagnostic
        assert "Traceback" not in diagnostic
        assert credential["credential"] not in diagnostic
        assert pending["token"] not in diagnostic
        assert server.url not in diagnostic
        assert exchanges == authentications == []
        assert path.read_bytes() == raw
        assert (state / "journal.sqlite3").read_bytes() == journal_before
        assert invite_file.read_bytes() == invite_before
        assert not (state / "files").exists()
        assert not (tmp_path / "computers").exists()
        assert len(service.list(kind)) == 1

        # Restauração explícita do arquivo original; reinício mantém o cadastro.
        path.write_bytes(original_bytes)
        process = start()
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline and process.poll() is None:
            if service.list(kind)[0]["online"]:
                break
            time.sleep(0.1)
        assert process.poll() is None
        assert service.list(kind)[0]["online"]
        assert service.list(kind)[0]["id"] == credential["executor_id"]
        assert len(service.list(kind)) == 1
        assert exchanges == []
        assert authentications == [True]
        assert path.read_bytes() == original_bytes
        assert invite_file.read_bytes() == invite_before
    finally:
        if process is not None:
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=10)
        server.stop()
