"""Revogação não torna a perda do journal uma primeira instalação."""

import asyncio
import json
import subprocess
import sys
from uuid import uuid4

import pytest
from test_state_lock import environment, stop

from bees_executor import cli
from bees_executor.journal import Journal, JournalStateError
from bees_executor.tools import ToolHost


@pytest.mark.parametrize("paired", [False, True])
def test_personal_marker_survives_identity_removal(tmp_path, monkeypatch, paired):
    state = tmp_path / "executor"
    if paired:
        state.mkdir()
        (state / "credential.json").write_text(
            json.dumps(
                {
                    "executor_id": str(uuid4()),
                    "credential": "be_" + "A" * 43,
                    "kind": "personal",
                    "api": "http://127.0.0.1:8080",
                }
            )
        )
        Journal(state / "journal.sqlite3").close()

    async def idle(args, handler, *unused):
        handler.host.close()
        handler.host.journal.close()

    monkeypatch.setattr(cli, "_run", idle)
    arguments = ["personal", "--state", str(state)]
    cli.main(arguments)
    marker = state / "journal.required"
    before = marker.read_bytes() if marker.exists() else None
    (state / "credential.json").unlink(missing_ok=True)
    path = state / "journal.sqlite3"
    original = path.read_bytes()
    path.unlink()
    with pytest.raises(SystemExit, match="executor_journal_invalid"):
        cli.main(arguments)
    assert not path.exists()
    assert marker.is_file()
    assert marker.read_bytes() == before
    path.write_bytes(original)
    cli.main(arguments)
    assert marker.read_bytes() == before


@pytest.mark.parametrize("damage", ["missing", "invalid"])
def test_paired_legacy_does_not_create_marker_on_invalid_journal(tmp_path, damage):
    from bees_executor.journal import persistent_journal

    path = tmp_path / "journal.sqlite3"
    if damage == "invalid":
        path.write_bytes(b"private-evidence")
    with pytest.raises(JournalStateError):
        persistent_journal(path, allow_create=False)
    assert not path.with_suffix(".required").exists()
    assert (path.read_bytes() if path.exists() else None) == (
        b"private-evidence" if damage == "invalid" else None
    )


def test_real_cli_revocation_then_journal_loss_blocks_before_pairing(tmp_path):
    from websockets.asyncio.server import serve

    async def scenario():
        state = tmp_path / "executor"
        state.mkdir()
        root = tmp_path / "shared"
        root.mkdir()
        path = state / "journal.sqlite3"
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
        host.close()
        journal.close()
        original = path.read_bytes()
        requests, results = [], []
        completed = asyncio.Event()
        revoked = True

        async def track(connection, request):
            requests.append(request.path)
            if revoked:
                return connection.respond(401, "revoked")

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
            credential_file = state / "credential.json"
            credential = {
                "executor_id": str(uuid4()),
                "credential": "be_" + "A" * 43,
                "kind": "personal",
                "api": api,
            }
            credential_file.write_text(json.dumps(credential))
            invite = tmp_path / "invite.json"
            invite.write_text(json.dumps({"token": "private-pending-invite"}))
            invite_before = invite.read_bytes()

            def start(*, pending=False):
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
                        *(["--invite-file", str(invite)] if pending else []),
                    ],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    env=environment(),
                )

            process = start()
            try:
                for _ in range(200):
                    if not credential_file.exists():
                        break
                    assert process.poll() is None
                    await asyncio.sleep(0.025)
                assert not credential_file.exists()  # CLI recebeu recusa real e removeu identidade.
            finally:
                stop(process)
            assert requests == ["/api/v1/executors/connect"]
            marker = state / "journal.required"
            marker_before = marker.read_bytes()
            path.unlink()
            requests.clear()
            process = start(pending=True)
            try:
                output, _ = await asyncio.to_thread(process.communicate, timeout=10)
                assert process.returncode == 1
                assert b"executor_journal_invalid" in output
                for private in [
                    str(state),
                    api,
                    credential["credential"],
                    "private-pending-invite",
                ]:
                    assert private.encode() not in output
                assert b"Traceback" not in output
                assert requests == []
                assert not path.exists() and not credential_file.exists()
                assert marker.read_bytes() == marker_before
                assert invite.read_bytes() == invite_before
                assert (root / "effect.txt").read_text() == "unique"
            finally:
                stop(process)

            # Operador restaura histórico e fornece outra identidade de teste.
            # O gateway é fixture de protocolo; não representa aceite de pareamento na API.
            path.write_bytes(original)
            credential_file.write_text(json.dumps(credential | {"credential": "be_" + "B" * 43}))
            revoked = False
            process = start(pending=True)
            try:
                await asyncio.wait_for(completed.wait(), timeout=15)
                assert results[0] == {"type": "result", "id": "append-once", **receipt}
                assert results[1]["status"] == "unknown"
                assert results[1]["code"] == "executor_replay_unknown"
                assert (root / "effect.txt").read_text() == "unique"
                assert requests == ["/api/v1/executors/connect"]
                assert marker.read_bytes() == marker_before
                assert invite.read_bytes() == invite_before
            finally:
                stop(process)

    asyncio.run(scenario())
