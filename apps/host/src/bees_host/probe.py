"""Sondas nativas fixas. Nada vindo do servidor modifica comandos ou argumentos."""

import ctypes
import json
import os
import shutil
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from threading import RLock

_DLL_LOCK = RLock()


def _kernel32():
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetSystemDirectoryW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32]
    kernel.GetSystemDirectoryW.restype = ctypes.c_uint32
    kernel.GetDllDirectoryW.argtypes = [ctypes.c_uint32, ctypes.c_wchar_p]
    kernel.GetDllDirectoryW.restype = ctypes.c_uint32
    kernel.SetDllDirectoryW.argtypes = [ctypes.c_wchar_p]
    kernel.SetDllDirectoryW.restype = ctypes.c_int
    return kernel


def _system_directory() -> Path | None:
    """Origem nativa do Windows; PATH, cwd e SystemRoot do chamador não participam."""
    try:
        buffer = ctypes.create_unicode_buffer(32768)
        size = _kernel32().GetSystemDirectoryW(buffer, len(buffer))
        path = Path(buffer.value)
        return path if 0 < size < len(buffer) and path.is_absolute() else None
    except OSError:
        return None


@contextmanager
def _external_library_search():
    """Filho externo usa DLLs do sistema; bootloader mantém seu caminho após spawn.

    PyInstaller configura SetDllDirectoryW no processo e o filho o herda.
    O runtime não usa threads; a trava também serializa sondas concorrentes.
    """
    if os.name != "nt" or not getattr(sys, "frozen", False):
        yield
        return
    with _DLL_LOCK:
        kernel = _kernel32()
        buffer = ctypes.create_unicode_buffer(32768)
        size = kernel.GetDllDirectoryW(len(buffer), buffer)
        if size >= len(buffer):
            raise OSError("Caminho de bibliotecas indisponível.")
        previous = buffer.value if size else None
        if not kernel.SetDllDirectoryW(None):
            raise OSError("Não foi possível preparar a sonda externa.")
        try:
            yield
        finally:
            if not kernel.SetDllDirectoryW(previous):
                raise OSError("Não foi possível restaurar bibliotecas do runtime.")


def _windows_environment(system_directory: Path) -> dict[str, str]:
    """Não encaminhar segredos, módulos do usuário ou hooks de empacotamento."""
    windows_directory = system_directory.parent
    powershell_directory = system_directory / "WindowsPowerShell" / "v1.0"
    return {
        "SystemRoot": str(windows_directory),
        "WINDIR": str(windows_directory),
        "PATH": ";".join(map(str, [system_directory, windows_directory, powershell_directory])),
        "PSModulePath": str(powershell_directory / "Modules"),
    }


def _read_command(
    argv: list[str],
    timeout: float = 5.0,
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
) -> str | None:
    try:
        with tempfile.TemporaryFile() as output:
            with _external_library_search():
                process = subprocess.Popen(
                    argv,
                    stdout=output,
                    stderr=subprocess.DEVNULL,
                    shell=False,
                    cwd=cwd,
                    env=env,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)
                    if os.name == "nt"
                    else 0,
                )
            try:
                process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
                return None
            if process.returncode != 0:
                return None
            output.seek(0)
            value = output.read(8193)
            if len(value) > 8192:
                return None
            return value.decode("utf-8", errors="strict")
    except OSError, UnicodeError:
        return None


def diagnose() -> tuple[str, dict[str, bool]]:
    if os.name == "nt":
        probe = {"platform": True, "module": False, "service": False}
        system_directory = _system_directory()
        executable = (
            system_directory / "WindowsPowerShell" / "v1.0" / "powershell.exe"
            if system_directory
            else None
        )
        if executable is not None and executable.is_file():
            value = _read_command(
                [
                    str(executable),
                    "-NoLogo",
                    "-NoProfile",
                    "-NonInteractive",
                    "-Command",
                    "$ErrorActionPreference='Stop';"
                    "$module=[bool](Get-Module -ListAvailable Hyper-V);"
                    "$service=Get-Service vmms -ErrorAction SilentlyContinue;"
                    "@{module=$module;service=[bool]($service -and $service.Status -eq 'Running')}"
                    "|ConvertTo-Json -Compress",
                ],
                cwd=system_directory,
                env=_windows_environment(system_directory),
            )
            try:
                data = json.loads(value or "null")
                if (
                    isinstance(data, dict)
                    and set(data) == {"module", "service"}
                    and all(type(v) is bool for v in data.values())
                ):
                    probe.update(data)
            except ValueError:
                pass
        return "hyperv", probe
    probe = {"platform": os.name == "posix", "kvm": False, "service": False}
    if probe["platform"]:
        probe["kvm"] = os.access("/dev/kvm", os.R_OK | os.W_OK)
        executable = shutil.which("virsh")
        if executable:
            probe["service"] = (
                _read_command([executable, "--connect", "qemu:///system", "uri"])
                == "qemu:///system\n"
            )
    return "libvirt", probe
