"""Journal existente inválido não pode se tornar um histórico vazio."""

import sqlite3

import pytest

from bees_executor.journal import Journal


@pytest.mark.parametrize("damage", ["empty", "garbage", "no_table", "schema", "receipt"])
def test_invalid_existing_journal_is_preserved(tmp_path, damage):
    path = tmp_path / "journal.sqlite3"
    if damage == "empty":
        path.touch()
    elif damage == "garbage":
        path.write_bytes(b"private-corrupt-journal")
    elif damage in ("no_table", "schema"):
        with sqlite3.connect(path) as connection:
            connection.execute(
                "CREATE TABLE unrelated(id TEXT)"
                if damage == "no_table"
                else "CREATE TABLE commands(id TEXT)"
            )
    else:
        journal = Journal(path)
        journal.begin("done", "files.write")
        journal.finish("done", {"status": "confirmed"})
        journal.begin("active", "files.write")
        journal.close()
        with sqlite3.connect(path) as connection:
            connection.execute("UPDATE commands SET result='null' WHERE id='done'")
    before = path.read_bytes()
    with pytest.raises(RuntimeError, match="^executor_journal_invalid$"):
        Journal(path)
    assert path.read_bytes() == before


@pytest.mark.parametrize(
    "raw", ["{", "[]", "null", "42", "true", '"secret"', "[" * 1100 + "0" + "]" * 1100]
)
def test_bad_receipt_does_not_reclassify_started(tmp_path, raw):
    path = tmp_path / "journal.sqlite3"
    journal = Journal(path)
    journal.begin("done", "files.write")
    journal.finish("done", {"status": "confirmed"})
    journal.begin("active", "files.write")
    journal.close()
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE commands SET result=? WHERE id='done'", (raw,))
    before = path.read_bytes()
    with pytest.raises(RuntimeError, match="^executor_journal_invalid$"):
        Journal(path)
    assert path.read_bytes() == before


def test_valid_restart_retains_receipts_and_unknown(tmp_path):
    path = tmp_path / "journal.sqlite3"
    receipt = {"status": "confirmed", "result": {"content": "ação única"}}
    journal = Journal(path)
    journal.begin("done", "files.write")
    journal.finish("done", receipt)
    journal.begin("active", "files.write")
    journal.close()
    journal = Journal(path)
    assert journal.begin("done", "files.write") == receipt
    assert journal.begin("active", "files.write") == {
        "status": "unknown",
        "code": "executor_replay_unknown",
    }
    assert journal.begin("new", "files.read") is None
    journal.close()


@pytest.mark.parametrize("damage", ["no_table", "receipt", "missing"])
def test_real_cli_refuses_then_replays_after_explicit_restore(tmp_path, damage):
    import asyncio
    import json
    import subprocess
    import sys
    from uuid import uuid4

    from test_state_lock import environment, stop
    from websockets.asyncio.server import serve

    from bees_executor.tools import ToolHost

    async def scenario():
        state = tmp_path / "executor"
        state.mkdir()
        path = state / "journal.sqlite3"
        root = tmp_path / "shared"
        root.mkdir()
        journal = Journal(path)
        host = ToolHost({"Docs": root}, tmp_path, journal, named=True)
        command = {
            "type": "command",
            "id": "append-once",
            "tool": "personal.files.write",
            "arguments": {"path": "Docs/effect.txt", "content": "unique", "append": True},
            "scope": {"roots": ["Docs"], "write": True},
        }
        receipt = host.execute(command)
        assert receipt["status"] == "confirmed"
        journal.begin("interrupted", "personal.files.write")
        journal.close()
        original = path.read_bytes()
        if damage == "missing":
            path.unlink()
        elif damage == "no_table":
            path.unlink()
            with sqlite3.connect(path) as connection:
                connection.execute("CREATE TABLE unrelated(id TEXT)")
        else:
            with sqlite3.connect(path) as connection:
                connection.execute("UPDATE commands SET result='null' WHERE status='done'")
        damaged = path.read_bytes() if path.exists() else None
        requests, results = [], []
        completed = asyncio.Event()

        async def track(connection, request):
            requests.append(request.path)

        async def gateway(connection):
            hello = json.loads(await connection.recv())
            assert hello["type"] == "hello"
            for message in [command, command | {"id": "interrupted"}]:
                await connection.send(json.dumps(message))
                results.append(json.loads(await connection.recv()))
            completed.set()
            await connection.wait_closed()

        async with serve(gateway, "127.0.0.1", 0, process_request=track) as server:
            api = f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}"
            credential = {
                "executor_id": str(uuid4()),
                "credential": "be_" + "A" * 43,
                "kind": "personal",
                "api": api,
            }
            credential_file = state / "credential.json"
            credential_file.write_text(json.dumps(credential))
            credential_before = credential_file.read_bytes()
            invite = tmp_path / "invite.json"
            invite.write_text(json.dumps({"token": "private-pending-invite"}))
            invite_before = invite.read_bytes()

            def start():
                return subprocess.Popen(
                    [
                        sys.executable,
                        "-m",
                        "bees_executor.cli",
                        "personal",
                        "--api",
                        api,
                        "--state",
                        str(state),
                        "--root",
                        f"Docs={root}",
                        "--invite-file",
                        str(invite),
                    ],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    env=environment(),
                )

            process = start()
            try:
                output, _ = await asyncio.to_thread(process.communicate, timeout=10)
                diagnostic = output.decode()
                assert process.returncode != 0
                assert "executor_journal_invalid" in diagnostic
                assert "Traceback" not in diagnostic
                assert str(state) not in diagnostic
                assert credential["credential"] not in diagnostic
                assert "private-pending-invite" not in diagnostic
                assert api not in diagnostic
                assert requests == []
                assert not (state / "files").exists()
                assert (path.read_bytes() if path.exists() else None) == damaged
                assert credential_file.read_bytes() == credential_before
                assert invite.read_bytes() == invite_before
                assert (root / "effect.txt").read_text() == "unique"
            finally:
                stop(process)

            # O operador restaura a evidência original; nenhum reparo do executor.
            path.write_bytes(original)
            process = start()
            try:
                await asyncio.wait_for(completed.wait(), timeout=15)
                assert results[0] == {"type": "result", "id": "append-once", **receipt}
                assert results[1]["status"] == "unknown"
                assert results[1]["code"] == "executor_replay_unknown"
                assert (root / "effect.txt").read_text() == "unique"
                assert requests == ["/api/v1/executors/connect"]
                assert credential_file.read_bytes() == credential_before
                assert invite.read_bytes() == invite_before
            finally:
                stop(process)

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "assignment",
    [
        "id=NULL",
        "tool=''",
        "started_at='private'",
        "finished_at='private'",
        "result='{}'",
        "status='invalid'",
    ],
)
def test_invalid_record_is_preserved(tmp_path, assignment):
    path = tmp_path / "journal.sqlite3"
    journal = Journal(path)
    journal.begin("active", "files.write")
    journal.close()
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA ignore_check_constraints=ON")
        connection.execute(f"UPDATE commands SET {assignment}")
    before = path.read_bytes()
    with pytest.raises(RuntimeError, match="^executor_journal_invalid$"):
        Journal(path)
    assert path.read_bytes() == before


def test_directory_is_not_a_new_journal(tmp_path):
    path = tmp_path / "journal.sqlite3"
    path.mkdir()
    with pytest.raises(RuntimeError, match="^executor_journal_invalid$"):
        Journal(path)
    assert path.is_dir()


def test_computer_refuses_invalid_journal_before_effect(tmp_path):
    import asyncio

    from bees_executor.computers import SingleComputers

    computers = SingleComputers(tmp_path)
    home = computers.home("test")
    (home / "workspace").mkdir(parents=True)
    path = home / ".bees" / "journal.sqlite3"
    path.parent.mkdir()
    path.write_bytes(b"private-invalid-journal")
    before = path.read_bytes()
    with pytest.raises(RuntimeError, match="^executor_journal_invalid$"):
        asyncio.run(
            computers.execute(
                "test",
                {
                    "id": "write-once",
                    "tool": "computer.files.write",
                    "arguments": {"path": "effect.txt", "content": "must-not-exist"},
                },
            )
        )
    assert not (home / "workspace" / "effect.txt").exists()
    assert path.read_bytes() == before


@pytest.mark.parametrize("parent_exists", [False, True])
def test_required_missing_journal_does_not_create_state(tmp_path, parent_exists):
    parent = tmp_path / "state"
    if parent_exists:
        parent.mkdir()
    with pytest.raises(RuntimeError, match="^executor_journal_invalid$"):
        Journal(parent / "journal.sqlite3", allow_create=False)
    assert parent.exists() == parent_exists
    assert not (parent / "journal.sqlite3").exists()


def test_required_existing_journal_retains_replay(tmp_path):
    path = tmp_path / "journal.sqlite3"
    journal = Journal(path)
    receipt = {"status": "confirmed", "result": {"content": "único"}}
    journal.begin("done", "files.write")
    journal.finish("done", receipt)
    journal.begin("active", "files.write")
    journal.close()
    journal = Journal(path, allow_create=False)
    try:
        assert journal.begin("done", "files.write") == receipt
        assert journal.begin("active", "files.write")["status"] == "unknown"
    finally:
        journal.close()
