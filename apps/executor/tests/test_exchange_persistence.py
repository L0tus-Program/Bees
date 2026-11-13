"""Falha local após consumo do convite não é indisponibilidade da API."""

import asyncio
import io
import json
import os
import subprocess
import sys
from types import SimpleNamespace

import pytest
from test_exchange_response import VALID

from bees_executor import client
from bees_executor.cli import _run
from bees_executor.state import state_lock


@pytest.mark.parametrize("existing", [False, True])
@pytest.mark.parametrize("phase", ["open", "write", "replace"])
def test_exchange_preserves_state_on_persistence_failure(tmp_path, monkeypatch, existing, phase):
    path = tmp_path / "credential.json"
    temporary = path.with_suffix(".tmp")
    if existing:
        path.write_bytes(b"original-private-state")
    calls = []

    def response(*args, **kwargs):
        calls.append(True)
        return io.BytesIO(json.dumps(VALID).encode())

    def fail(*args, **kwargs):
        if phase == "write":
            args[1].write("{" + '"private-partial-secret":')
        raise OSError("private-path-and-secret")

    monkeypatch.setattr("urllib.request.OpenerDirector.open", response)
    if phase == "open":
        monkeypatch.setattr(client.os, "open", fail)
    elif phase == "write":
        monkeypatch.setattr(client.json, "dump", fail)
    else:
        monkeypatch.setattr(client.os, "replace", fail)
    # Deve ser uma falha de persistência própria, fora da hierarquia OSError.
    with pytest.raises(
        client.CredentialPersistenceError, match="^executor_credential_save_failed$"
    ) as caught:
        client.exchange("http://localhost", "private-invite", tmp_path)
    assert not isinstance(caught.value, OSError)
    assert calls == [True]
    if existing:
        assert path.read_bytes() == b"original-private-state"
    else:
        assert not path.exists()
    if phase == "open":
        assert not temporary.exists()
    elif phase == "write":
        assert temporary.read_bytes() == b'{"private-partial-secret":'
    else:
        assert json.loads(temporary.read_text()) == VALID | {"api": "http://localhost"}


def test_cli_loop_does_not_retry_consumed_invite_after_save_failure(tmp_path, monkeypatch):
    requests = []

    def response(*args, **kwargs):
        requests.append(True)
        return io.BytesIO(json.dumps(VALID).encode())

    def fail(*args, **kwargs):
        raise PermissionError("private-path-and-secret")

    async def sleep(*args):
        pytest.fail("Falha de persistência foi tratada como rede ou convite recusado.")

    monkeypatch.setattr("urllib.request.OpenerDirector.open", response)
    monkeypatch.setattr(client.os, "replace", fail)
    monkeypatch.setattr(asyncio, "sleep", sleep)
    args = SimpleNamespace(state=tmp_path, api="http://localhost", invite="private-invite")
    with pytest.raises(
        client.CredentialPersistenceError, match="^executor_credential_save_failed$"
    ):
        asyncio.run(_run(args, None, "personal"))
    assert requests == [True]
    assert not (tmp_path / "credential.json").exists()
    assert (tmp_path / "credential.tmp").exists()


@pytest.mark.parametrize("mode", ["personal", "computer"])
def test_cli_sanitizes_save_failure_and_releases_lock(tmp_path, monkeypatch, mode):
    from bees_executor import cli

    def fail(*args):
        try:
            raise PermissionError("private-path-and-secret")
        except OSError:
            raise client.CredentialPersistenceError("executor_credential_save_failed") from None

    monkeypatch.setattr(cli, "_start", fail)
    with pytest.raises(SystemExit) as caught:
        cli.main([mode, "--state", str(tmp_path)])
    diagnostic = str(caught.value)
    assert diagnostic.startswith("executor_credential_save_failed:")
    assert "privado" in diagnostic
    assert "private-path-and-secret" not in diagnostic
    assert str(tmp_path) not in diagnostic
    assert caught.value.__suppress_context__
    with state_lock(tmp_path):
        pass


@pytest.mark.skipif(sys.platform == "win32", reason="Processo POSIX simples.")
@pytest.mark.parametrize("kind", ["personal", "computer_runtime"])
@pytest.mark.parametrize("phase", ["open", "replace"])
def test_cli_stops_after_real_exchange_when_local_write_fails(tmp_path, monkeypatch, kind, phase):
    from test_executor_gateway import Server

    from bees_core.executors import ExecutorService
    from bees_executor.journal import persistent_journal

    attempts, identities = [], []
    original_exchange = ExecutorService.exchange
    state = tmp_path / "executor"
    obstacle = state / ("credential.tmp" if phase == "open" else "credential.json")
    evidence = obstacle / "preserve.txt"

    def tracked_exchange(self, *args, **kwargs):
        attempts.append(True)
        identity = original_exchange(self, *args, **kwargs)
        identities.append(identity)
        if phase == "replace":
            # Criar após a leitura inicial, antes da resposta da API, para testar
            # os.replace real sem tornar o arquivo local inválido no preflight.
            obstacle.mkdir()
            evidence.write_bytes(b"private-evidence")
        return identity

    monkeypatch.setattr(ExecutorService, "exchange", tracked_exchange)
    server = Server(tmp_path)
    process = None
    try:
        service = ExecutorService(server.database)
        invite = service.invite({"kind": kind, "name": "Falha local controlada"})
        invite_file = tmp_path / "invite.json"
        invite_file.write_text(json.dumps(invite), encoding="utf-8")
        invite_before = invite_file.read_bytes()
        journal = persistent_journal(state / "journal.sqlite3")
        journal.begin("confirmed-command", "files.write")
        journal.finish("confirmed-command", {"status": "confirmed", "value": "preserved"})
        journal.close()
        journal_before = (state / "journal.sqlite3").read_bytes()
        # Obstáculo real e portátil no filesystem, sem alterar permissões globais.
        if phase == "open":
            obstacle.mkdir()
            evidence.write_bytes(b"private-evidence")
        arguments = (
            ["personal", "--root", f"Docs={tmp_path}"]
            if kind == "personal"
            else ["computer", "--computers", str(tmp_path / "computers"), "--mode", "single"]
        )
        process = subprocess.Popen(
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
        try:
            output, _ = process.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            pytest.fail("O executor continuou após consumir o convite sem salvar a credencial.")
        diagnostic = output.decode()
        assert process.returncode != 0
        assert "executor_credential_save_failed" in diagnostic
        assert "Traceback" not in diagnostic
        assert invite["token"] not in diagnostic
        assert str(tmp_path) not in diagnostic
        assert server.url not in diagnostic
        assert "API indisponível" not in diagnostic
        assert attempts == [True]
        assert identities[0]["credential"] not in diagnostic
        if phase == "open":
            assert not (state / "credential.json").exists()
        else:
            assert (state / "credential.json").is_dir()
            assert json.loads((state / "credential.tmp").read_text()) == identities[0] | {
                "api": server.url
            }
        assert evidence.read_bytes() == b"private-evidence"
        assert (state / "journal.sqlite3").read_bytes() == journal_before
        assert invite_file.read_bytes() == invite_before
        registered = service.list(kind)
        assert len(registered) == 1
        assert not registered[0]["online"]
    finally:
        if process is not None:
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=10)
        server.stop()
