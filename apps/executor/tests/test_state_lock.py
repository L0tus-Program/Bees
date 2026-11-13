"""Uma única CLI pode possuir o estado, inclusive durante espera por convite."""

import json
import os
import subprocess
import sys
import time

import pytest
from test_executor_gateway import Server

from bees_core.executors import ExecutorService
from bees_executor.client import exchange
from bees_executor.journal import Journal
from bees_executor.state import StateLockError, state_lock


def environment():
    return os.environ | {
        "BEES_EXECUTOR_INVITE": "",
        "BEES_EXECUTOR_INVITE_FILE": "",
        "PYTHONIOENCODING": "utf-8",
        "PYTHONPATH": os.pathsep.join(p for p in sys.path if p),
    }


def wait_for(predicate, process, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        assert process.poll() is None
        if predicate():
            return
        time.sleep(0.05)
    pytest.fail("Processo não alcançou o checkpoint esperado.")


def stop(process):
    if process is not None:
        if process.poll() is None:
            process.kill()
        process.communicate(timeout=10)


@pytest.mark.parametrize("kind", ["personal", "computer_runtime"])
@pytest.mark.parametrize("paired", [False, True])
def test_cli_excludes_second_process_and_recovers_after_crash(tmp_path, monkeypatch, kind, paired):
    server = Server(tmp_path)
    owner = contender = None
    authentications, exchanges = [], []
    original_auth, original_exchange = ExecutorService.authenticate, ExecutorService.exchange

    def authenticate(self, *args, **kwargs):
        authentications.append(True)
        return original_auth(self, *args, **kwargs)

    def tracked_exchange(self, *args, **kwargs):
        exchanges.append(True)
        return original_exchange(self, *args, **kwargs)

    try:
        service = ExecutorService(server.database)
        state = tmp_path / "executor"
        state.mkdir()
        path = state / "credential.json"
        if paired:
            invite = service.invite({"kind": kind, "name": "Original"})
            credential = exchange(server.url, invite["token"], state, expected_kind=kind)
            credential_before = path.read_bytes()
            Journal(state / "journal.sqlite3").close()
        pending = service.invite({"kind": kind, "name": "Não consumir em paralelo"})
        invite_file = tmp_path / "invite.json"
        invite_file.write_text(json.dumps({"token": pending["token"]}))
        invite_before = invite_file.read_bytes()
        monkeypatch.setattr(ExecutorService, "authenticate", authenticate)
        monkeypatch.setattr(ExecutorService, "exchange", tracked_exchange)
        arguments = (
            ["personal"]
            if kind == "personal"
            else ["computer", "--computers", str(tmp_path / "computers"), "--mode", "single"]
        )

        def start(output, *, invite=False, alias=False):
            return subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "bees_executor.cli",
                    *arguments,
                    "--api",
                    server.url,
                    "--state",
                    str(state / ".." / "executor" if alias else state),
                    *(["--invite-file", str(invite_file)] if invite else []),
                ],
                stdout=output,
                stderr=subprocess.STDOUT,
                env=environment(),
            )

        owner_log = tmp_path / "owner.log"
        with owner_log.open("wb") as output:
            owner = start(output)
        if paired:
            wait_for(lambda: service.list(kind)[0]["online"], owner)
        else:
            wait_for(lambda: "Aguardando convite" in owner_log.read_text(encoding="utf-8"), owner)
        auth_before = len(authentications)

        # Um comando ativo não pode ser reclassificado por outra inicialização.
        journal = Journal(state / "journal.sqlite3")
        journal.begin("active-command", "files.write")
        journal.close()
        journal_before = (state / "journal.sqlite3").read_bytes()
        contender = start(subprocess.PIPE, invite=True, alias=True)
        try:
            output, _ = contender.communicate(timeout=8)
        except subprocess.TimeoutExpired:
            pytest.fail("Segunda CLI iniciou sobre estado já em uso.")
        diagnostic = output.decode()
        assert contender.returncode != 0
        assert "executor_state_in_use" in diagnostic
        assert "Traceback" not in diagnostic
        assert pending["token"] not in diagnostic
        assert server.url not in diagnostic
        assert str(state) not in diagnostic
        assert (state / "journal.sqlite3").read_bytes() == journal_before
        assert invite_file.read_bytes() == invite_before
        assert len(service.list(kind)) == int(paired)
        assert exchanges == []
        assert len(authentications) == auth_before
        if paired:
            assert path.read_bytes() == credential_before
            assert credential["credential"] not in diagnostic
        else:
            assert not path.exists()

        # O SO libera a trava ao matar o processo; não apagar arquivo residual.
        stop(owner)
        owner = None
        assert (state / "executor.lock").exists()
        stop(contender)
        contender = start(subprocess.PIPE, invite=True)
        wait_for(lambda: len(authentications) > auth_before, contender)
        wait_for(lambda: service.list(kind)[0]["online"], contender)
        assert len(service.list(kind)) == 1
        assert len(exchanges) == int(not paired)
        if paired:
            assert service.list(kind)[0]["id"] == credential["executor_id"]
            assert path.read_bytes() == credential_before
        else:
            assert path.exists()
        assert invite_file.read_bytes() == invite_before
    finally:
        stop(contender)
        stop(owner)
        server.stop()


@pytest.mark.parametrize("failure", [False, True])
def test_lock_release_preserves_file_and_excludes_same_process(tmp_path, failure):
    with state_lock(tmp_path):
        with pytest.raises(StateLockError, match="^executor_state_in_use$"):
            with state_lock(tmp_path):
                pytest.fail("Estado admitiu dois donos.")
        with state_lock(tmp_path / "separate"):
            pass
    before = (tmp_path / "executor.lock").stat()
    try:
        with state_lock(tmp_path):
            if failure:
                raise ValueError("Falha no corpo não é erro de trava.")
    except ValueError:
        assert failure
    with state_lock(tmp_path):
        after = (tmp_path / "executor.lock").stat()
        assert (before.st_dev, before.st_ino) == (after.st_dev, after.st_ino)


def test_lock_io_error_is_sanitized(tmp_path):
    (tmp_path / "executor.lock").mkdir()
    with pytest.raises(StateLockError, match="^executor_state_unavailable$"):
        with state_lock(tmp_path):
            pytest.fail("Trava inválida foi aceita.")
