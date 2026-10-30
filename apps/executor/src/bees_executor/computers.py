"""Computadores das abelhas dentro do runtime.

Modo ``multiuser`` (container, root): cada computador tem conta Linux própria,
home persistente, desktop Xvfb + XFCE e um processo agente que executa as
ferramentas com o UID da abelha. O supervisor apenas provisiona e encaminha.

Modo ``single`` (desenvolvimento/testes): um diretório por computador e
execução no próprio processo, sem isolamento de conta.
"""

import asyncio
import json
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path

from bees_executor.journal import Journal
from bees_executor.tools import ToolHost

log = logging.getLogger("bees_executor")


def _short(computer_id: str) -> str:
    return computer_id.replace("-", "")[:10]


class SingleComputers:
    mode = "single"

    def __init__(self, base: Path):
        self.base = base
        self.hosts: dict[str, ToolHost] = {}

    def home(self, computer_id: str) -> Path:
        return self.base / computer_id

    async def provision(self, computer_id: str) -> None:
        home = self.home(computer_id)
        (home / "workspace").mkdir(parents=True, exist_ok=True)

    def _host(self, computer_id: str) -> ToolHost:
        if computer_id not in self.hosts:
            home = self.home(computer_id)
            self.hosts[computer_id] = ToolHost(
                {"workspace": home / "workspace"},
                home,
                Journal(home / ".bees" / "journal.sqlite3"),
                named=False,
                screenshots=home / "workspace" / "screenshots",
            )
        return self.hosts[computer_id]

    async def execute(self, computer_id: str, command: dict) -> dict:
        if not self.home(computer_id).exists():
            return {"status": "failed", "code": "computer_not_provisioned"}
        host = self._host(computer_id)
        return await asyncio.to_thread(host.execute, command)

    async def remove(self, computer_id: str) -> None:
        host = self.hosts.pop(computer_id, None)
        if host is not None:
            host.close()
        shutil.rmtree(self.home(computer_id), ignore_errors=True)

    def display(self, computer_id: str) -> str | None:
        return None

    def user(self, computer_id: str):
        return None

    async def close(self):
        for host in self.hosts.values():
            host.close()


def account_limits(environ=os.environ) -> list[str]:
    """Prefixo `prlimit` aplicado já com o UID da conta: processos e arquivos por abelha.

    CPU, memória e PIDs do conjunto ficam no container (compose). Aqui cada conta ganha
    teto próprio de processos (RLIMIT_NPROC conta todos os processos do UID), tamanho
    máximo de arquivo e core desligado. Valores inválidos voltam ao padrão.
    """
    executable = shutil.which("prlimit")
    if executable is None:
        log.warning("prlimit ausente; computadores sem limite por conta.")
        return []

    def number(name, default, low, high):
        try:
            value = int(environ.get(name, default))
        except ValueError:
            return default
        return value if low <= value <= high else default

    processes = number("BEES_COMPUTER_MAX_PROCESSES", 512, 64, 65536)
    file_mb = number("BEES_COMPUTER_MAX_FILE_MB", 4096, 16, 1048576)
    return [
        executable,
        f"--nproc={processes}",
        f"--fsize={file_mb * 1024 * 1024}",
        "--core=0",
        "--",
    ]


class MultiUserComputers:
    """Exige root dentro do container; nunca usar no host do usuário."""

    mode = "multiuser"

    def __init__(self, base: Path, state: Path):
        self.base = base
        self.state = state
        self.registry_path = state / "computers.json"
        self.registry = (
            json.loads(self.registry_path.read_text()) if self.registry_path.exists() else {}
        )
        self.processes: dict[str, list[subprocess.Popen]] = {}
        self.locks: dict[str, asyncio.Lock] = {}

    def _save(self):
        self.state.mkdir(parents=True, exist_ok=True)
        temporary = self.registry_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.registry))
        os.replace(temporary, self.registry_path)

    def home(self, computer_id: str) -> Path:
        return self.base / computer_id

    def user(self, computer_id: str):
        entry = self.registry.get(computer_id)
        if not entry:
            return None
        import pwd

        info = pwd.getpwnam(entry["user"])
        return info.pw_uid, info.pw_gid, entry["user"]

    def display(self, computer_id: str) -> str | None:
        entry = self.registry.get(computer_id)
        return f":{entry['display']}" if entry else None

    def _socket(self, computer_id: str) -> Path:
        return self.home(computer_id) / ".bees" / "agent.sock"

    def _ensure_user(self, computer_id: str) -> None:
        """Contas não persistem ao recriar o container: recriar com o mesmo UID."""
        import pwd

        entry = self.registry[computer_id]
        try:
            pwd.getpwnam(entry["user"])
            return
        except KeyError:
            pass
        command = [
            "useradd",
            "--home-dir",
            str(self.home(computer_id)),
            "--no-create-home",
            "--shell",
            "/bin/bash",
            "--user-group",
            entry["user"],
        ]
        if entry.get("uid"):
            command[1:1] = ["--uid", str(entry["uid"])]
        subprocess.run(command, check=True, capture_output=True)
        if not entry.get("uid"):
            entry["uid"] = pwd.getpwnam(entry["user"]).pw_uid
            self._save()

    async def provision(self, computer_id: str) -> None:
        if computer_id not in self.registry:
            used = {entry["display"] for entry in self.registry.values()}
            display = next(number for number in range(10, 200) if number not in used)
            self.registry[computer_id] = {"user": "bee" + _short(computer_id), "display": display}
            self._save()
        self.home(computer_id).parent.mkdir(parents=True, exist_ok=True)
        self._ensure_user(computer_id)
        await self._start(computer_id)

    def _alive(self, computer_id: str) -> bool:
        return all(
            process.poll() is None for process in self.processes.get(computer_id, [])
        ) and bool(self.processes.get(computer_id))

    async def _start(self, computer_id: str) -> None:
        if self._alive(computer_id):
            return
        for process in self.processes.pop(computer_id, []):
            process.kill()
        uid, gid, name = self.user(computer_id)
        home = self.home(computer_id)
        for directory in (home, home / "workspace", home / ".bees"):
            directory.mkdir(parents=True, exist_ok=True)
            os.chown(directory, uid, gid)
        os.chmod(home, 0o700)
        display = self.display(computer_id)
        env = {
            "HOME": str(home),
            "USER": name,
            "LOGNAME": name,
            "PATH": "/usr/local/bin:/usr/bin:/bin",
            "LANG": os.environ.get("LANG", "C.UTF-8"),
            "DISPLAY": display,
            "XDG_RUNTIME_DIR": str(home / ".bees" / "xdg"),
        }
        Path(env["XDG_RUNTIME_DIR"]).mkdir(exist_ok=True)
        os.chown(env["XDG_RUNTIME_DIR"], uid, gid)
        os.chmod(env["XDG_RUNTIME_DIR"], 0o700)
        if "PYTHONPATH" in os.environ:
            env["PYTHONPATH"] = os.environ["PYTHONPATH"]
        if "TZ" in os.environ:
            env["TZ"] = os.environ["TZ"]
        if "BEES_BROWSER_EXECUTABLE" in os.environ:
            env["BEES_BROWSER_EXECUTABLE"] = os.environ["BEES_BROWSER_EXECUTABLE"]
        if "PLAYWRIGHT_BROWSERS_PATH" in os.environ:
            env["PLAYWRIGHT_BROWSERS_PATH"] = os.environ["PLAYWRIGHT_BROWSERS_PATH"]
        options = {
            "user": uid,
            "group": gid,
            "extra_groups": [],
            "env": env,
            "cwd": str(home),
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
            "start_new_session": True,
        }
        processes = []
        limits = account_limits()
        if shutil.which("Xvfb"):
            processes.append(
                subprocess.Popen(
                    limits + ["Xvfb", display, "-screen", "0", "1280x800x24", "-nolisten", "tcp"],
                    **options,
                )
            )
            await asyncio.sleep(0.8)
            session = shutil.which("startxfce4")
            if session:
                launcher = [shutil.which("dbus-launch") or "", "--exit-with-session", session]
                processes.append(
                    subprocess.Popen(limits + [part for part in launcher if part], **options)
                )
        socket_path = self._socket(computer_id)
        socket_path.unlink(missing_ok=True)
        processes.append(
            subprocess.Popen(
                limits
                + [
                    sys.executable,
                    "-m",
                    "bees_executor.agent_process",
                    "--socket",
                    str(socket_path),
                    "--home",
                    str(home),
                ],
                **options,
            )
        )
        self.processes[computer_id] = processes
        for _ in range(100):
            if socket_path.exists():
                break
            await asyncio.sleep(0.1)

    async def execute(self, computer_id: str, command: dict) -> dict:
        if computer_id not in self.registry:
            return {"status": "failed", "code": "computer_not_provisioned"}
        lock = self.locks.setdefault(computer_id, asyncio.Lock())
        async with lock:
            await self._start(computer_id)
            try:
                reader, writer = await asyncio.open_unix_connection(str(self._socket(computer_id)))
            except OSError:
                return {"status": "failed", "code": "computer_agent_unavailable"}
            try:
                writer.write(json.dumps(command).encode() + b"\n")
                await writer.drain()
                line = await reader.readline()
                if not line:
                    # Conexão perdida após envio: o efeito pode ter ocorrido.
                    return {"status": "unknown", "code": "computer_agent_lost"}
                return json.loads(line)
            finally:
                writer.close()

    async def remove(self, computer_id: str) -> None:
        for process in self.processes.pop(computer_id, []):
            try:
                os.killpg(process.pid, 9)
            except OSError:
                process.kill()
        entry = self.registry.pop(computer_id, None)
        self._save()
        if entry:
            subprocess.run(["pkill", "-KILL", "-u", entry["user"]], check=False)
            subprocess.run(["userdel", entry["user"]], check=False, capture_output=True)
        shutil.rmtree(self.home(computer_id), ignore_errors=True)

    async def close(self):
        for processes in self.processes.values():
            for process in processes:
                process.terminate()
