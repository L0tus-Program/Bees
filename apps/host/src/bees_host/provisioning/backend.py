"""Comandos constantes e processo em duas fases: GO só após journal durável do PID."""

import base64
import ctypes
import json
import os
import queue
import subprocess
import threading
from pathlib import Path
from uuid import UUID

from pydantic import ValidationError

from bees_host import probe
from bees_host.provisioning.contracts import Inventory, Operation, Plan, ProvisionError, canonical
from bees_host.provisioning.journal import private

TIMEOUT = 30


def _line(stream, timeout: int = 5) -> bytes:
    result = queue.Queue(maxsize=1)

    def read():
        try:
            result.put(stream.readline(4097))
        except OSError:
            result.put(b"")

    thread = threading.Thread(target=read, daemon=True)
    thread.start()
    try:
        value = result.get(timeout=timeout)
    except queue.Empty:
        raise ProvisionError("provision_command_timeout") from None
    if not value.endswith(b"\n") or len(value) > 4096:
        raise ProvisionError("provision_command_invalid")
    return value


def process_running(pid: int, start_ticks: int) -> bool:
    """Não mata processo antigo. Falta de prova de quiescência falha fechada."""
    if os.name != "nt":
        raise ProvisionError("provision_transport_unsupported")
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    ptr = ctypes.c_void_p
    kernel.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
    kernel.OpenProcess.restype = ptr
    kernel.GetProcessTimes.argtypes = [ptr, ptr, ptr, ptr, ptr]
    kernel.WaitForSingleObject.argtypes = [ptr, ctypes.c_uint32]
    kernel.CloseHandle.argtypes = [ptr]
    handle = kernel.OpenProcess(0x00101000, 0, pid)
    if not handle:
        if ctypes.get_last_error() == 87:
            return False
        raise ProvisionError("provision_quiescence_unproved")
    try:
        times = [ctypes.c_uint64() for _ in range(4)]
        if not kernel.GetProcessTimes(handle, *(ctypes.byref(value) for value in times)):
            raise ProvisionError("provision_quiescence_unproved")
        if times[0].value != start_ticks:
            return False
        status = kernel.WaitForSingleObject(handle, 0)
        if status not in {0, 258}:
            raise ProvisionError("provision_quiescence_unproved")
        return status == 258
    finally:
        kernel.CloseHandle(handle)


class Command:
    def __init__(self, process, request_id: UUID):
        self.process, self.request_id = process, request_id
        self.pid, self.start_ticks = None, None

    def ready(self) -> tuple[int, int]:
        try:
            value = json.loads(_line(self.process.stdout))
            if (
                type(value) is not dict
                or set(value) != {"ready", "pid", "start_ticks"}
                or value["ready"] is not True
                or type(value["pid"]) is not int
                or value["pid"] != self.process.pid
                or type(value["start_ticks"]) is not int
                or not 0 < value["start_ticks"] <= 2**63 - 1
            ):
                raise ProvisionError("provision_command_invalid")
            self.pid, self.start_ticks = value["pid"], value["start_ticks"]
            if os.name == "nt" and not process_running(self.pid, self.start_ticks):
                raise ProvisionError("provision_command_invalid")
            return self.pid, self.start_ticks
        except ValueError, OSError:
            raise ProvisionError("provision_command_invalid") from None

    def go(self):
        try:
            self.process.stdin.write(("GO:" + str(self.request_id) + "\n").encode())
            self.process.stdin.flush()
            self.process.stdin.close()
        except OSError:
            raise ProvisionError("provision_command_unknown") from None

    def finish(self) -> Inventory:
        try:
            raw = _line(self.process.stdout, TIMEOUT)
            self.process.wait(timeout=2)
            if self.process.returncode != 0 or self.process.stdout.read(1):
                raise ProvisionError("provision_command_unknown")
            return Inventory.model_validate_json(raw)
        except OSError, ValueError, subprocess.TimeoutExpired, ValidationError:
            raise ProvisionError("provision_command_unknown") from None

    def close(self):
        # Somente filho criado por este objeto. Nunca PID antigo vindo de journal.
        try:
            if self.process.poll() is None:
                self.process.kill()
            self.process.wait(timeout=5)
            for stream in (self.process.stdin, self.process.stdout):
                if stream is not None and not stream.closed:
                    stream.close()
        except OSError, subprocess.TimeoutExpired:
            raise ProvisionError("provision_quiescence_unproved") from None


class HyperVBackend:
    def __init__(self, storage_root: Path):
        self.storage_root = Path(storage_root)

    def preflight(self):
        if os.name != "nt":
            raise ProvisionError("provision_transport_unsupported")
        private(self.storage_root, directory=True)
        if not ctypes.WinDLL("shell32").IsUserAnAdmin():
            raise ProvisionError("provision_elevation_required")

    def start(
        self, plan: Plan, operation: Operation, request_id: UUID, iso: Path, vm_id: UUID | None
    ) -> Command:
        self.preflight()
        system = probe._system_directory()
        executable = system / "WindowsPowerShell/v1.0/powershell.exe" if system else None
        if executable is None or not executable.is_file():
            raise ProvisionError("provision_powershell_unavailable")
        script = Path(__file__).with_name("hyperv.ps1").read_text(encoding="utf-8")
        payload = {
            "operation": operation,
            "request_id": str(request_id),
            "vm_name": plan.vm_name,
            "installation_id": str(plan.installation_id),
            "environment_id": str(plan.environment_id),
            "plan_id": str(plan.plan_id),
            "plan_hash": plan.digest(),
            "cpu_count": plan.cpu_count,
            "memory_bytes": plan.memory_bytes,
            "disk_bytes": plan.disk_bytes,
            "image_iso_sha256": plan.image_iso_sha256,
            "storage_root": str(self.storage_root),
            "template_iso": str(iso),
            "vm_id": str(vm_id) if vm_id else None,
        }
        argv = [
            str(executable),
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-EncodedCommand",
            base64.b64encode(script.encode("utf-16-le")).decode("ascii"),
        ]
        process = None
        try:
            with probe._external_library_search():
                process = subprocess.Popen(
                    argv,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    shell=False,
                    cwd=system,
                    env=probe._windows_environment(system),
                    close_fds=True,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
            process.stdin.write(canonical(payload) + b"\n")
            process.stdin.flush()
            return Command(process, request_id)
        except OSError:
            if process is not None:
                Command(process, request_id).close()
            raise ProvisionError("provision_command_unavailable") from None

    def inspect(self, plan: Plan, iso: Path, vm_id: UUID | None) -> Inventory:
        from uuid import uuid4

        command = self.start(plan, "inspect", uuid4(), iso, vm_id)
        try:
            command.ready()
            command.go()
            return command.finish()
        finally:
            command.close()

    def running(self, pid: int, start_ticks: int) -> bool:
        return process_running(pid, start_ticks)
