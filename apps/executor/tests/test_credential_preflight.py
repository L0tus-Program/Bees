"""Configuração incompatível não pode iniciar a recuperação do estado privado."""

import json
import sqlite3
import subprocess
import sys
from uuid import uuid4

import pytest
from test_executor_gateway import Server
from test_state_lock import environment, stop, wait_for

from bees_core.executors import ExecutorService
from bees_executor.cli import main
from bees_executor.client import exchange
from bees_executor.journal import Journal


def snapshot(directory):
    # A trava da CLI pode ser criada antes da guarda; não é recurso do runtime.
    return {
        str(path.relative_to(directory)): path.read_bytes() if path.is_file() else None
        for path in directory.rglob("*")
        if path.name != "executor.lock"
    }


@pytest.mark.parametrize("mode", ["personal", "computer"])
@pytest.mark.parametrize("mismatch", ["api", "kind"])
@pytest.mark.parametrize("journal_state", ["started", "missing", "invalid"])
def test_binding_is_checked_before_any_runtime_preparation(
    tmp_path, monkeypatch, mode, mismatch, journal_state
):
    state = tmp_path / "state"
    state.mkdir()
    kind = "personal" if mode == "personal" else "computer_runtime"
    credential = {
        "executor_id": str(uuid4()),
        "credential": "be_" + "A" * 43,
        "kind": kind,
        "api": "http://original.test",
    }
    if mismatch == "kind":
        credential["kind"] = "computer_runtime" if kind == "personal" else "personal"
    (state / "credential.json").write_text(json.dumps(credential))
    journal_path = state / "journal.sqlite3"
    if journal_state == "started":
        journal = Journal(journal_path)
        journal.begin("interrupted-command", "files.write")
        journal.close()
    elif journal_state == "invalid":
        journal_path.write_bytes(b"private-corrupt-journal")
    invite = tmp_path / "invite.json"
    invite.write_text('{"token": "private-unused-invite"}')
    before = snapshot(tmp_path)
    calls = []

    def forbidden(*args, **kwargs):
        calls.append(True)
        pytest.fail("Configuração incompatível chegou à preparação do runtime.")

    monkeypatch.setattr("bees_executor.cli.persistent_journal", forbidden)
    monkeypatch.setattr("bees_executor.cli.ToolHost", forbidden)
    monkeypatch.setattr("bees_executor.computers.SingleComputers", forbidden)
    monkeypatch.setattr("bees_executor.computers.MultiUserComputers", forbidden)
    monkeypatch.setattr("bees_executor.cli.exchange", forbidden)
    monkeypatch.setattr("bees_executor.cli.ExecutorClient.run_forever", forbidden)
    arguments = [
        mode,
        "--state",
        str(state),
        "--api",
        "http://other.test" if mismatch == "api" else credential["api"],
        "--invite-file",
        str(invite),
    ]
    with pytest.raises(SystemExit, match=f"executor_{mismatch}_mismatch") as caught:
        main(arguments)
    assert calls == []
    assert snapshot(tmp_path) == before
    assert credential["credential"] not in str(caught.value)
    assert "private-unused-invite" not in str(caught.value)
    assert str(state) not in str(caught.value)
    assert credential["api"] not in str(caught.value)


@pytest.mark.skipif(sys.platform == "win32", reason="Subprocesso POSIX com API real.")
@pytest.mark.parametrize("mismatch", ["api", "kind"])
def test_real_cli_preserves_started_until_original_configuration_returns(
    tmp_path, monkeypatch, mismatch
):
    server = Server(tmp_path)
    process = None
    authentications, exchanges = [], []
    try:
        service = ExecutorService(server.database)
        state = tmp_path / "executor"
        invite = service.invite({"kind": "personal", "name": "Preflight"})
        credential = exchange(server.url, invite["token"], state, expected_kind="personal")
        journal_path = state / "journal.sqlite3"
        journal = Journal(journal_path)
        journal.begin("interrupted-command", "files.write")
        journal.close()
        pending = service.invite({"kind": "personal", "name": "Não consumir"})
        invite_file = tmp_path / "invite.json"
        invite_file.write_text(json.dumps({"token": pending["token"]}))
        before = snapshot(state)
        invite_before = invite_file.read_bytes()
        original_auth, original_exchange = ExecutorService.authenticate, ExecutorService.exchange

        def auth(self, *args, **kwargs):
            authentications.append(True)
            return original_auth(self, *args, **kwargs)

        def tracked_exchange(self, *args, **kwargs):
            exchanges.append(True)
            return original_exchange(self, *args, **kwargs)

        monkeypatch.setattr(ExecutorService, "authenticate", auth)
        monkeypatch.setattr(ExecutorService, "exchange", tracked_exchange)

        def start(mode, api):
            return subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "bees_executor.cli",
                    mode,
                    "--api",
                    api,
                    "--state",
                    str(state),
                    "--invite-file",
                    str(invite_file),
                    *(
                        ["--computers", str(tmp_path / "computers"), "--mode", "single"]
                        if mode == "computer"
                        else []
                    ),
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                env=environment(),
            )

        process = start(
            "computer" if mismatch == "kind" else "personal",
            "http://127.0.0.1:1" if mismatch == "api" else server.url,
        )
        output, _ = process.communicate(timeout=10)
        diagnostic = output.decode("utf-8")
        assert process.returncode != 0
        assert f"executor_{mismatch}_mismatch" in diagnostic
        assert "Traceback" not in diagnostic
        assert credential["credential"] not in diagnostic
        assert pending["token"] not in diagnostic
        assert snapshot(state) == before
        assert invite_file.read_bytes() == invite_before
        assert authentications == exchanges == []

        process = start("personal", server.url + "/")
        wait_for(lambda: service.list("personal")[0]["online"], process)
        assert len(service.list("personal")) == 1
        assert service.list("personal")[0]["id"] == credential["executor_id"]
        assert authentications == [True]
        assert exchanges == []
        assert (state / "credential.json").read_bytes() == before["credential.json"]
        assert invite_file.read_bytes() == invite_before
        stop(process)
        process = None
        with sqlite3.connect(journal_path.resolve().as_uri() + "?mode=ro", uri=True) as saved:
            assert saved.execute("SELECT status FROM commands").fetchall() == [("unknown",)]
        journal = Journal(journal_path)
        try:
            assert journal.begin("interrupted-command", "files.write") == {
                "status": "unknown",
                "code": "executor_replay_unknown",
            }
        finally:
            journal.close()
    finally:
        stop(process)
        server.stop()
