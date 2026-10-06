"""Sondas nativas fixas. Nada vindo do servidor modifica comandos ou argumentos."""

import json
import os
import shutil
import subprocess
import tempfile


def _read_command(argv: list[str], timeout: float = 5.0) -> str | None:
    try:
        with tempfile.TemporaryFile() as output:
            process = subprocess.Popen(argv, stdout=output, stderr=subprocess.DEVNULL, shell=False)
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
        executable = shutil.which("powershell.exe")
        if executable:
            value = _read_command(
                [
                    executable,
                    "-NoLogo",
                    "-NoProfile",
                    "-NonInteractive",
                    "-Command",
                    "$ErrorActionPreference='Stop';"
                    "$module=[bool](Get-Module -ListAvailable Hyper-V);"
                    "$service=Get-Service vmms -ErrorAction SilentlyContinue;"
                    "@{module=$module;service=[bool]($service -and $service.Status -eq 'Running')}"
                    "|ConvertTo-Json -Compress",
                ]
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
