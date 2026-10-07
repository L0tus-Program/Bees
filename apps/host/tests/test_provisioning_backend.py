import base64
import json
import os
import subprocess
import sys
from importlib.resources import files
from pathlib import Path
from uuid import uuid4

import pytest
from bees_host import probe
from bees_host.provisioning.backend import Command, HyperVBackend
from bees_host.provisioning.contracts import ProvisionError
from test_guest_bridge_tls import private_bridge_directory


def test_real_child_without_go_exits_without_local_effect():
    with private_bridge_directory() as base:
        target = base / "effect.txt"
        request = uuid4()
        script = """
import json,sys
from pathlib import Path
import os,ctypes
ticks=1
if os.name=='nt':
 k=ctypes.WinDLL('kernel32'); k.GetProcessTimes.argtypes=[ctypes.c_void_p]*5
 times=[ctypes.c_uint64() for _ in range(4)]
 assert k.GetProcessTimes(ctypes.c_void_p(-1),*(ctypes.byref(t) for t in times))
 ticks=times[0].value
print(json.dumps({'ready':True,'pid':os.getpid(),'start_ticks':ticks}),flush=True)
line=sys.stdin.readline().strip()
if line==sys.argv[2]:
 Path(sys.argv[1]).write_text('effect')
"""
        process = subprocess.Popen(
            [sys._base_executable, "-c", script, str(target), "GO:" + str(request)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            close_fds=True,
        )
        command = Command(process, request)
        try:
            assert command.ready()[0] == process.pid
            process.stdin.close()
            process.wait(timeout=5)
            assert not target.exists()
        finally:
            command.close()


def test_real_child_go_is_explicit_and_only_local_fixture_effect():
    with private_bridge_directory() as base:
        target = base / "effect.txt"
        request = uuid4()
        script = """
import json,sys,os
from pathlib import Path
import ctypes
ticks=1
if os.name=='nt':
 k=ctypes.WinDLL('kernel32'); k.GetProcessTimes.argtypes=[ctypes.c_void_p]*5
 times=[ctypes.c_uint64() for _ in range(4)]
 assert k.GetProcessTimes(ctypes.c_void_p(-1),*(ctypes.byref(t) for t in times))
 ticks=times[0].value
print(json.dumps({'ready':True,'pid':os.getpid(),'start_ticks':ticks}),flush=True)
line=sys.stdin.readline().strip()
if line==sys.argv[2]:
 Path(sys.argv[1]).write_text('fixture')
"""
        process = subprocess.Popen(
            [sys._base_executable, "-c", script, str(target), "GO:" + str(request)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            close_fds=True,
        )
        command = Command(process, request)
        try:
            command.ready()
            command.go()
            process.wait(timeout=5)
            assert target.read_text() == "fixture"
        finally:
            command.close()


def test_packaged_script_exists_and_powershell_parse_is_readonly():
    script = files("bees_host.provisioning").joinpath("hyperv.ps1").read_bytes()
    assert script
    if os.name != "nt":
        pytest.skip("Parser PowerShell5.1 é exclusivo do Windows.")
    system = probe._system_directory()
    executable = system / "WindowsPowerShell/v1.0/powershell.exe"
    parser = (
        "$t=[Console]::In.ReadToEnd();$tokens=$null;$errors=$null;"
        "[Management.Automation.Language.Parser]::ParseInput($t,[ref]$tokens,[ref]$errors)"
        "|Out-Null;if($errors.Count){exit 1}else{exit 0}"
    )
    with probe._external_library_search():
        result = subprocess.run(
            [str(executable), "-NoProfile", "-NonInteractive", "-Command", parser],
            input=script,
            cwd=system,
            env=probe._windows_environment(system),
            timeout=10,
            capture_output=True,
            check=False,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    assert result.returncode == 0 and not result.stdout and not result.stderr


def test_unsupported_platform_has_no_process_or_plaintext_fallback(monkeypatch):
    from types import SimpleNamespace

    import bees_host.provisioning.backend as module

    monkeypatch.setattr(module, "os", SimpleNamespace(name="posix"))
    monkeypatch.setattr(
        subprocess, "Popen", lambda *args, **kwargs: pytest.fail("unexpected process")
    )
    with pytest.raises(ProvisionError, match="provision_transport_unsupported"):
        HyperVBackend(Path.home()).preflight()


@pytest.mark.parametrize("failure", [None, "hardlink", "foreign_acl", "foreign_read_acl"])
def test_readonly_native_hardware_guard_without_hyperv(failure):
    """Somente arquivos descartáveis: não carrega módulo nem altera hardware/ACL global."""
    if os.name != "nt":
        pytest.skip("Guardas de arquivo/ACL Windows nativas.")
    script = files("bees_host.provisioning").joinpath("hyperv.ps1").read_text()
    guards = script.split("# BEGIN_READONLY_GUARDS:", 1)[1].split("\n", 1)[1]
    guards = guards.split("# END_READONLY_GUARDS", 1)[0]
    with private_bridge_directory() as base:
        from bees_host.provisioning.journal import create

        target = base / "hardware.bin"
        create(target, b"own-test-hardware")
        if failure == "hardlink":
            os.link(target, base / "alias.bin")
        program = (
            "$ErrorActionPreference='Stop';$ProgressPreference='SilentlyContinue'\n"
            + guards
            + """
$p=[Console]::In.ReadLine()|ConvertFrom-Json
Initialize-NativeGuard
if (-not (Test-OwnedPath $p.root $p.root)) {exit 2}
if (-not (Test-OwnedPath (Join-Path $p.root 'nested') $p.root)) {exit 3}
if (Test-OwnedPath ($p.root+'-foreign') $p.root) {exit 4}
if ($p.failure -in @('foreign_acl','foreign_read_acl')) {
    $acl=[Security.AccessControl.FileSecurity]::new()
    $owner=[Security.Principal.WindowsIdentity]::GetCurrent().User
    $acl.SetOwner($owner)
    $acl.SetAccessRuleProtection($true,$false)
    $acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new($owner,[Security.AccessControl.FileSystemRights]::FullControl,[Security.AccessControl.AccessControlType]::Allow))
    $sid=[Security.Principal.SecurityIdentifier]::new('S-1-1-0')
    $rights = if ($p.failure -eq 'foreign_read_acl') {
        [Security.AccessControl.FileSystemRights]::Read
    } else {
        [Security.AccessControl.FileSystemRights]::Write
    }
    $rule=[Security.AccessControl.FileSystemAccessRule]::new($sid,$rights,[Security.AccessControl.AccessControlType]::Allow)
    $acl.AddAccessRule($rule)
    [IO.File]::SetAccessControl($p.file,$acl)
}
try {Assert-HardwareNode $p.file $p.root $false ''; $allowed=$true}
catch {$allowed=$false}
if ($allowed -ne ($null -eq $p.failure)) {exit 5}
exit 0
"""
        )
        system = probe._system_directory()
        with probe._external_library_search():
            result = subprocess.run(
                [
                    str(system / "WindowsPowerShell/v1.0/powershell.exe"),
                    "-NoProfile",
                    "-NonInteractive",
                    "-EncodedCommand",
                    base64.b64encode(program.encode("utf-16-le")).decode(),
                ],
                input=json.dumps(
                    {"root": str(base), "file": str(target), "failure": failure}
                ).encode(),
                cwd=system,
                env=probe._windows_environment(system),
                timeout=15,
                capture_output=True,
                check=False,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        assert result.returncode == 0, result.stderr.decode(errors="replace")
        assert not result.stdout and not result.stderr
