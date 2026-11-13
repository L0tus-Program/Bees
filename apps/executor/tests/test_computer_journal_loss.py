"""Perda do journal não permite repetir efeitos nos computadores."""

import asyncio
import sys

import pytest

from bees_executor.computers import SingleComputers

COMMAND = {
    "id": "append-once",
    "tool": "computer.files.write",
    "arguments": {"path": "effect.txt", "content": "unique", "append": True},
}


def test_single_loss_blocks_then_explicit_restore_preserves_replay(tmp_path):
    async def scenario():
        computers = SingleComputers(tmp_path)
        await computers.provision("c1")
        receipt = await computers.execute("c1", COMMAND)
        host = computers.hosts["c1"]
        host.journal.begin("interrupted", "computer.files.write")
        host.close()
        host.journal.close()
        journal = tmp_path / "c1" / ".bees" / "journal.sqlite3"
        original = journal.read_bytes()
        journal.unlink()
        computers = SingleComputers(tmp_path)
        await computers.provision("c1")
        with pytest.raises(RuntimeError, match="^executor_journal_invalid$"):
            await computers.execute("c1", COMMAND)
        assert not journal.exists()
        effect = tmp_path / "c1" / "workspace" / "effect.txt"
        assert effect.read_text() == "unique"
        journal.write_bytes(original)
        assert await computers.execute("c1", COMMAND) == receipt
        assert await computers.execute("c1", COMMAND | {"id": "interrupted"}) == {
            "status": "unknown",
            "code": "executor_replay_unknown",
        }
        assert effect.read_text() == "unique"
        await computers.close()
        computers.hosts["c1"].journal.close()

    asyncio.run(scenario())


def test_legacy_journal_is_adopted_only_when_valid(tmp_path):
    from bees_executor.journal import Journal, computer_journal

    path = tmp_path / ".bees" / "journal.sqlite3"
    journal = Journal(path)
    journal.begin("interrupted", "computer.files.write")
    journal.close()
    assert not path.with_suffix(".required").exists()
    journal = computer_journal(tmp_path)
    assert journal.begin("interrupted", "computer.files.write")["status"] == "unknown"
    journal.close()
    assert path.with_suffix(".required").is_file()


def test_invalid_legacy_does_not_create_marker(tmp_path):
    from bees_executor.journal import computer_journal

    path = tmp_path / ".bees" / "journal.sqlite3"
    path.parent.mkdir()
    path.write_bytes(b"private-invalid-journal")
    with pytest.raises(RuntimeError, match="^executor_journal_invalid$"):
        computer_journal(tmp_path)
    assert path.read_bytes() == b"private-invalid-journal"
    assert not path.with_suffix(".required").exists()


def test_interrupted_initial_creation_stays_blocked(tmp_path, monkeypatch):
    import os

    from bees_executor import journal

    def fail(_):
        raise OSError("private disk failure")

    with monkeypatch.context() as patch:
        patch.setattr(os, "fsync", fail)
        with pytest.raises(RuntimeError, match="^executor_journal_invalid$"):
            journal.computer_journal(tmp_path)
    path = tmp_path / ".bees" / "journal.sqlite3"
    assert path.with_suffix(".required").is_file()
    assert not path.exists()
    with pytest.raises(RuntimeError, match="^executor_journal_invalid$"):
        journal.computer_journal(tmp_path)
    assert not path.exists()


def test_concurrent_first_open_does_not_create_two_journals(tmp_path, monkeypatch):
    import threading
    from concurrent.futures import ThreadPoolExecutor

    from bees_executor import journal

    # Ambos observaram ausência antes da criação exclusiva do marcador.
    validate = journal._validate
    barrier = threading.Barrier(2)

    def synchronized(path):
        result = validate(path)
        barrier.wait(timeout=5)
        return result

    with monkeypatch.context() as patch:
        patch.setattr(journal, "_validate", synchronized)
        # Só sincronizar a leitura inicial, não a validação do construtor.
        local = threading.local()

        def once(path):
            if getattr(local, "visited", False):
                return validate(path)
            local.visited = True
            return synchronized(path)

        patch.setattr(journal, "_validate", once)

        def open_one():
            try:
                opened = journal.computer_journal(tmp_path)
            except RuntimeError as error:
                assert str(error) == "executor_journal_invalid"
                return False
            opened.close()
            return True

        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(lambda _: open_one(), range(2)))
    assert any(outcomes)
    opened = journal.computer_journal(tmp_path)
    opened.close()


def test_single_restart_does_not_replace_missing_journal(tmp_path):
    async def scenario():
        computers = SingleComputers(tmp_path)
        await computers.provision("c1")
        await computers.execute("c1", COMMAND)
        host = computers.hosts["c1"]
        path = host.journal.path
        # Fechar antes da perda também funciona em Windows, que bloqueia unlink aberto.
        host.close()
        host.journal.close()
        computers.hosts.clear()
        path.unlink()
        with pytest.raises(RuntimeError, match="^executor_journal_invalid$"):
            await computers.restart("c1")
        assert not path.exists()
        assert (tmp_path / "c1" / "workspace" / "effect.txt").read_text() == "unique"

    asyncio.run(scenario())


@pytest.mark.skipif(sys.platform != "linux", reason="Agente Unix do container Linux")
def test_real_agent_loss_blocks_before_socket_and_restored_replay_is_unique(tmp_path):
    import json
    import os
    import subprocess
    import sys
    from pathlib import Path

    async def scenario():
        home = tmp_path / "home"
        (home / "workspace").mkdir(parents=True)
        socket = tmp_path / "s"
        env = os.environ | {"PYTHONPATH": str(Path("apps/executor/src").resolve())}

        def start():
            socket.unlink(missing_ok=True)
            return subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "bees_executor.agent_process",
                    "--home",
                    str(home),
                    "--socket",
                    str(socket),
                ],
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )

        async def stop(process):
            if process.poll() is None:
                process.terminate()
            try:
                await asyncio.to_thread(process.communicate, timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                await asyncio.to_thread(process.communicate, timeout=10)

        async def ready(process):
            for _ in range(100):
                if socket.exists():
                    return
                assert process.poll() is None
                await asyncio.sleep(0.05)
            pytest.fail("Agente não abriu socket")

        async def send(command):
            reader, writer = await asyncio.open_unix_connection(str(socket))
            try:
                writer.write(json.dumps(command).encode() + b"\n")
                await writer.drain()
                return json.loads(await asyncio.wait_for(reader.readline(), 5))
            finally:
                writer.close()
                await writer.wait_closed()

        process = start()
        try:
            await ready(process)
            receipt = await send(COMMAND)
            assert receipt["status"] == "confirmed"
        finally:
            await stop(process)
        path = home / ".bees" / "journal.sqlite3"
        original = path.read_bytes()
        marker = path.with_suffix(".required").read_bytes()
        path.unlink()
        process = start()
        try:
            _, error = await asyncio.to_thread(process.communicate, timeout=10)
            assert process.returncode == 1
            assert b"executor_journal_invalid" in error
            assert str(home).encode() not in error
            assert b"Traceback" not in error
            assert not socket.exists() and not path.exists()
            assert path.with_suffix(".required").read_bytes() == marker
            assert (home / "workspace" / "effect.txt").read_text() == "unique"
        finally:
            await stop(process)
        path.write_bytes(original)
        process = start()
        try:
            await ready(process)
            assert await send(COMMAND) == receipt
            assert (home / "workspace" / "effect.txt").read_text() == "unique"
        finally:
            await stop(process)

    asyncio.run(scenario())


@pytest.mark.parametrize("exited", [True, False])
@pytest.mark.skipif(sys.platform != "linux", reason="Supervisor Linux")
def test_supervisor_does_not_report_ready_when_agent_fails(tmp_path, monkeypatch, exited):
    import os
    from types import SimpleNamespace

    from bees_executor import computers
    from bees_executor.cli import ComputerRuntime

    supervisor = computers.MultiUserComputers(tmp_path / "homes", tmp_path / "state")
    supervisor.registry["c1"] = {"user": "test", "display": 12}
    monkeypatch.setattr(supervisor, "_ensure_user", lambda _: None)
    monkeypatch.setattr(supervisor, "user", lambda _: (os.getuid(), os.getgid(), "test"))
    monkeypatch.setattr(computers.shutil, "which", lambda _: None)
    monkeypatch.setattr(computers, "account_limits", lambda: [])
    process = SimpleNamespace(poll=lambda: 1 if exited else None)
    monkeypatch.setattr(computers.subprocess, "Popen", lambda *a, **k: process)

    async def no_wait(_):
        pass

    monkeypatch.setattr(computers.asyncio, "sleep", no_wait)
    sent = []

    async def send(message):
        sent.append(message)

    runtime = ComputerRuntime(supervisor)
    asyncio.run(
        runtime.control(
            SimpleNamespace(send=send),
            {
                "type": "computers",
                "computers": [{"id": "c1", "status": "provisioning"}],
            },
        )
    )
    assert sent == [
        {"type": "computer_status", "id": "c1", "status": "failed", "reason": "provisioning_failed"}
    ]
