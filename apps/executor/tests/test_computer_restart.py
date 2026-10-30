"""Reiniciar o computador: supervisor encerra a sessão e sobe de novo, arquivos ficam."""

import asyncio
import subprocess
import sys
from types import SimpleNamespace

import pytest

from bees_executor import computers
from bees_executor.cli import ComputerRuntime
from bees_executor.computers import MultiUserComputers, SingleComputers

# Modo multiusuário só existe no container Linux (contas, killpg, Xvfb).
LINUX_ONLY = pytest.mark.skipif(sys.platform != "linux", reason="Computador multiusuário é Linux.")

RESTART = {"id": "r1", "tool": "computer.restart", "arguments": {}}


def test_single_restart_recreates_tool_host_and_keeps_files(tmp_path):
    single = SingleComputers(tmp_path)

    async def scenario():
        await single.provision("c1")
        written = await single.execute(
            "c1",
            {
                "id": "w1",
                "tool": "computer.files.write",
                "arguments": {"path": "notas.txt", "content": "fica"},
            },
        )
        assert written["status"] == "confirmed"
        before = single.hosts["c1"]
        receipt = await single.execute("c1", RESTART)
        return before, receipt

    before, receipt = asyncio.run(scenario())
    assert receipt == {"status": "confirmed", "result": {"restarted": True}}
    assert single.hosts["c1"] is not before
    assert (tmp_path / "c1" / "workspace" / "notas.txt").read_text() == "fica"
    # O restart não passa pelo journal; o novo ToolHost continua respondendo replay.
    replay = asyncio.run(single.execute("c1", {"id": "w1", "tool": "computer.files.write"}))
    assert replay["status"] == "confirmed"
    asyncio.run(single.close())


def test_single_restart_requires_provisioned_computer(tmp_path):
    receipt = asyncio.run(SingleComputers(tmp_path).execute("nada", RESTART))
    assert receipt == {"status": "failed", "code": "computer_not_provisioned"}


class FakeProcess:
    def __init__(self, pid):
        self.pid = pid
        self.waited = False

    def poll(self):
        return None

    def kill(self):
        pass

    def wait(self, timeout=None):
        self.waited = timeout is not None


@pytest.fixture
def multi(tmp_path, monkeypatch):
    x11 = tmp_path / "x11"
    (x11 / ".X11-unix").mkdir(parents=True)
    supervisor = MultiUserComputers(tmp_path / "computers", tmp_path / "state", x11=x11)
    supervisor.registry["c1"] = {"user": "beec1", "display": 12}
    processes = [FakeProcess(101), FakeProcess(102)]
    supervisor.processes["c1"] = list(processes)
    killed, ran, started = [], [], []
    monkeypatch.setattr(
        computers.os, "killpg", lambda pid, sig: killed.append((pid, sig)), raising=False
    )

    def run(command, **options):
        ran.append(command)
        return SimpleNamespace(returncode=1)  # pgrep: nenhum processo restante

    monkeypatch.setattr(computers.subprocess, "run", run)

    async def start(computer_id):
        assert not supervisor.processes.get(computer_id)  # nada antigo sobrevive
        started.append(computer_id)
        supervisor.processes[computer_id] = [FakeProcess(201)]

    monkeypatch.setattr(supervisor, "_start", start)
    return SimpleNamespace(
        supervisor=supervisor,
        x11=x11,
        processes=processes,
        killed=killed,
        ran=ran,
        started=started,
    )


@LINUX_ONLY
def test_multiuser_restart_kills_session_cleans_display_and_starts_again(multi):
    (multi.x11 / ".X12-lock").write_text("101")
    (multi.x11 / ".X11-unix" / "X12").write_text("")
    (multi.x11 / ".X13-lock").write_text("outra tela")
    receipt = asyncio.run(multi.supervisor.execute("c1", RESTART))
    assert receipt == {"status": "confirmed", "result": {"restarted": True}}
    assert [pid for pid, _ in multi.killed] == [101, 102]
    assert all(process.waited for process in multi.processes)
    assert ["pkill", "-KILL", "-u", "beec1"] in multi.ran
    assert ["pgrep", "-u", "beec1"] in multi.ran
    assert not (multi.x11 / ".X12-lock").exists()
    assert not (multi.x11 / ".X11-unix" / "X12").exists()
    assert (multi.x11 / ".X13-lock").exists()
    assert multi.started == ["c1"]
    assert [process.pid for process in multi.supervisor.processes["c1"]] == [201]


@LINUX_ONLY
def test_multiuser_restart_waits_for_the_computer_lock(multi):
    async def scenario():
        lock = multi.supervisor.locks.setdefault("c1", asyncio.Lock())
        await lock.acquire()
        restart = asyncio.create_task(multi.supervisor.execute("c1", RESTART))
        await asyncio.sleep(0.05)
        assert not restart.done() and multi.started == []
        lock.release()
        return await restart

    assert asyncio.run(scenario())["status"] == "confirmed"
    assert multi.started == ["c1"]


@LINUX_ONLY
def test_multiuser_restart_failure_is_reported(multi, monkeypatch):
    async def broken(computer_id):
        raise subprocess.CalledProcessError(1, "Xvfb")

    monkeypatch.setattr(multi.supervisor, "_start", broken)
    receipt = asyncio.run(multi.supervisor.execute("c1", RESTART))
    assert receipt == {"status": "failed", "code": "computer_restart_failed"}
    unknown = asyncio.run(multi.supervisor.execute("c2", RESTART))
    assert unknown == {"status": "failed", "code": "computer_not_provisioned"}


class FakeClient:
    def __init__(self):
        self.sent = []

    async def send(self, message):
        self.sent.append(message)
        return True


def test_runtime_restart_control_answers_the_request(tmp_path):
    single = SingleComputers(tmp_path)
    runtime = ComputerRuntime(single)
    client = FakeClient()

    async def scenario():
        await single.provision("c1")
        await runtime.control(
            client, {"type": "computer_restart", "request": "q1", "computer_id": "c1"}
        )
        await runtime.control(
            client, {"type": "computer_restart", "request": "q2", "computer_id": "ausente"}
        )
        await runtime.control(client, {"type": "computer_restart", "request": 3})

    asyncio.run(scenario())
    assert client.sent == [
        {"type": "computer_restarted", "request": "q1", "status": "confirmed", "code": None},
        {
            "type": "computer_restarted",
            "request": "q2",
            "status": "failed",
            "code": "computer_not_provisioned",
        },
    ]
    asyncio.run(single.close())
