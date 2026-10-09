import base64
import json
import os
import subprocess
import sys
import threading
import time
from importlib.resources import files
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from bees_host import probe
from bees_host.provisioning.backend import Command, HyperVBackend
from bees_host.provisioning.contracts import ProvisionError
from test_guest_bridge_tls import private_bridge_directory
from test_provisioning_assets import fingerprint, set_hardware_acl

_INVENTORY = {
    "vm_id": None,
    "found": False,
    "owned": False,
    "powered_off": True,
    "generation2": False,
    "cpu_count": None,
    "memory_bytes": None,
    "disk_bytes": None,
    "fixed_vhdx": False,
    "network_adapter_count": 0,
    "iso_attached": False,
}


def _fixture_command(body: str) -> Command:
    """Filho real descartável, somente stdout/espera; nenhum efeito de hardware."""
    script = "import sys,time,os,json\ninventory=" + repr(json.dumps(_INVENTORY)) + "\n" + body
    process = subprocess.Popen(
        [sys._base_executable, "-c", script],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        close_fds=True,
    )
    return Command(process, uuid4())


def test_real_finish_succeeds_without_optional_guard_and_leaves_no_reader():
    before = set(threading.enumerate())
    command = _fixture_command("print(inventory,flush=True)\n")
    try:
        assert command.finish().powered_off is True
    finally:
        command.close()
    assert command.process.poll() == 0
    assert command.process.stdout.closed and command.process.stdin.closed
    assert set(threading.enumerate()) == before


def test_maximum_4096_byte_result_line_is_accepted():
    command = _fixture_command(
        "sys.stdout.buffer.write((inventory+' '*(4095-len(inventory))+'\\n').encode());"
        "sys.stdout.buffer.flush()\n"
    )
    try:
        assert command.finish().found is False
    finally:
        command.close()


def test_guard_failure_before_reading_terminates_own_child_without_reader():
    command = _fixture_command("time.sleep(60)\n")
    before = set(threading.enumerate())

    def guard():
        raise ProvisionError("fixture_revoked")

    try:
        with pytest.raises(ProvisionError, match="fixture_revoked"):
            command.finish(guard=guard)
    finally:
        command.close()
    assert command.process.poll() is not None
    assert set(threading.enumerate()) == before


def test_guard_runs_on_owner_during_partial_read_and_after_exit():
    owner = threading.get_ident()
    stamps = []
    command = _fixture_command(
        "sys.stdout.write(inventory[:5]);sys.stdout.flush()\n"
        "time.sleep(1.2)\n"
        "print(inventory[5:],flush=True)\n"
    )

    def guard():
        assert threading.get_ident() == owner
        stamps.append(time.monotonic())

    try:
        assert command.finish(guard=guard).powered_off is True
    finally:
        command.close()
    assert len(stamps) >= 3  # início, renovação enquanto não há linha, confirmação
    assert stamps[1] - stamps[0] >= 0.9
    assert stamps[1] - stamps[0] < 1.6


@pytest.mark.parametrize(
    "body",
    [
        "time.sleep(60)\n",  # nenhuma linha
        "sys.stdout.write(inventory[:10]);sys.stdout.flush();time.sleep(60)\n",
        "print(inventory,flush=True);time.sleep(60)\n",  # linha recebida, processo ativo
    ],
)
def test_guard_revocation_interrupts_read_or_process_wait_and_close_is_quiescent(body):
    before = set(threading.enumerate())
    command = _fixture_command(body)
    count = 0
    start = time.monotonic()

    def guard():
        nonlocal count
        count += 1
        if count == 2:
            raise ProvisionError("fixture_revoked")

    try:
        with pytest.raises(ProvisionError, match="fixture_revoked"):
            command.finish(guard=guard)
    finally:
        command.close()
    assert count == 2 and time.monotonic() - start < 3
    assert command.process.poll() is not None
    assert command.process.stdout.closed
    assert set(threading.enumerate()) == before


@pytest.mark.parametrize(
    "body",
    [
        "time.sleep(60)\n",
        "sys.stdout.write(inventory[:10]);sys.stdout.flush();time.sleep(60)\n",
        "print(inventory,flush=True);time.sleep(60)\n",
        "sys.stdout.close();os.close(1);time.sleep(60)\n",
    ],
)
def test_total_deadline_includes_partial_line_exit_and_eof(body, monkeypatch):
    import bees_host.provisioning.backend as module

    monkeypatch.setattr(module, "TIMEOUT", 0.25)
    command = _fixture_command(body)
    start = time.monotonic()
    try:
        with pytest.raises(ProvisionError) as failure:
            command.finish()
        assert str(failure.value) in {"provision_command_timeout", "provision_command_invalid"}
    finally:
        command.close()
    assert time.monotonic() - start < 1.5
    assert command.process.poll() is not None


@pytest.mark.parametrize(
    "body,code",
    [
        ("print(inventory+'extra',flush=True)\n", "provision_command_unknown"),
        ("print(inventory,flush=True);print('extra',flush=True)\n", "provision_command_unknown"),
        (
            "print(inventory,flush=True);time.sleep(.15);print('extra',flush=True)\n",
            "provision_command_unknown",
        ),
        ("sys.stdout.write(inventory);sys.stdout.flush()\n", "provision_command_invalid"),
        ("print('x'*4096,flush=True)\n", "provision_command_invalid"),
        ("print(inventory,flush=True);sys.exit(1)\n", "provision_command_unknown"),
    ],
)
def test_bad_or_trailing_stdout_never_publishes_inventory(body, code):
    command = _fixture_command(body)
    try:
        with pytest.raises(ProvisionError, match=code):
            command.finish()
    finally:
        command.close()


def test_guard_duration_counts_in_total_deadline(monkeypatch):
    import bees_host.provisioning.backend as module

    monkeypatch.setattr(module, "TIMEOUT", 0.1)
    command = _fixture_command("print(inventory,flush=True)\n")

    def guard():
        time.sleep(0.15)

    try:
        with pytest.raises(ProvisionError, match="provision_command_timeout"):
            command.finish(guard=guard)
    finally:
        command.close()


def test_finish_waits_for_eof_under_same_deadline_after_child_has_exited(monkeypatch):
    """Outro handle mantém pipe aberto; não há filho antigo morto pelo controle."""
    import bees_host.provisioning.backend as module

    monkeypatch.setattr(module, "TIMEOUT", 0.4)
    read_fd, write_fd = os.pipe()
    process = subprocess.Popen(
        [sys._base_executable, "-c", "print(" + repr(json.dumps(_INVENTORY)) + ",flush=True)"],
        stdin=subprocess.PIPE,
        stdout=write_fd,
        stderr=subprocess.DEVNULL,
        close_fds=True,
    )
    process.stdout = os.fdopen(read_fd, "rb")
    command = Command(process, uuid4())
    # Escritor pertencente a este teste mantém pipe aberto após o filho sair.
    process.wait(timeout=5)
    start = time.monotonic()
    try:
        with pytest.raises(ProvisionError, match="provision_command_timeout"):
            command.finish()
    finally:
        command.close()
        os.close(write_fd)
    assert time.monotonic() - start < 1.5


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
    import bees_host.provisioning.backend as module

    monkeypatch.setattr(module, "os", SimpleNamespace(name="posix"))
    monkeypatch.setattr(
        subprocess, "Popen", lambda *args, **kwargs: pytest.fail("unexpected process")
    )
    with pytest.raises(ProvisionError, match="provision_transport_unsupported"):
        HyperVBackend(Path.home()).preflight()


@pytest.mark.skipif(os.name != "nt", reason="Protocolo PowerShell nativo Windows")
def test_native_powershell_prepared_go_reads_separate_stdin_statements():
    """Contraste com o protocolo de produção; nenhum módulo/efeito de Hyper-V."""
    request_id = uuid4()
    program = (
        """
$ErrorActionPreference='Stop'
[Console]::InputEncoding = [Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
$line=[Console]::In.ReadLine()
$p=$line | ConvertFrom-Json
if (-not $p.request_id) {exit 2}
$ready=@{ready=$true;pid=$PID;start_ticks=([Diagnostics.Process]::GetCurrentProcess().StartTime.ToUniversalTime().ToFileTimeUtc())}
[Console]::Out.WriteLine(($ready | ConvertTo-Json -Compress))
[Console]::Out.Flush()
$go=[Console]::In.ReadLine()
if ($go -cne ('GO:'+$p.request_id)) {exit 3}
"""
        + "[Console]::Out.WriteLine('"
        + json.dumps(_INVENTORY)
        + "');exit 0"
    )
    system = probe._system_directory()
    with probe._external_library_search():
        process = subprocess.Popen(
            [
                str(system / "WindowsPowerShell/v1.0/powershell.exe"),
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-EncodedCommand",
                base64.b64encode(program.encode("utf-16-le")).decode("ascii"),
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            cwd=system,
            env=probe._windows_environment(system),
            close_fds=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    command = Command(process, request_id)
    try:
        # stdin fica aberto até GO: não depender de EOF para ler a primeira linha.
        process.stdin.write(json.dumps({"request_id": str(request_id)}).encode() + b"\n")
        process.stdin.flush()
        pid, ticks = command.ready()
        assert pid == process.pid and ticks > 0
        assert not process.stdin.closed and process.poll() is None
        command.go()
        assert command.finish().powered_off is True
    finally:
        command.close()
    assert process.poll() == 0 and process.stdin.closed and process.stdout.closed


@pytest.mark.parametrize("admin", [False, True])
def test_preflight_accepts_native_vmms_acl_and_only_checks_existing_elevation(monkeypatch, admin):
    """Área própria com ACL real; resposta administrativa falsa, sem UAC/processo."""
    if os.name != "nt":
        pytest.skip("ACL VMMS nativa é Windows.")
    import bees_host.provisioning.backend as module

    calls = []

    def library(name):
        calls.append(name)
        assert name == "shell32"
        return SimpleNamespace(IsUserAnAdmin=lambda: admin)

    monkeypatch.setattr(module, "ctypes", SimpleNamespace(WinDLL=library))
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **kw: pytest.fail("Processo inesperado"))
    with private_bridge_directory() as base:
        hardware = base / "hardware"
        hardware.mkdir()
        set_hardware_acl(hardware)
        before = fingerprint(base)
        if admin:
            HyperVBackend(hardware).preflight()
        else:
            with pytest.raises(ProvisionError, match="provision_elevation_required"):
                HyperVBackend(hardware).preflight()
        assert fingerprint(base) == before and calls == ["shell32"]


@pytest.mark.parametrize("failure", [None, "hardlink", "foreign_acl", "foreign_read_acl"])
def test_readonly_native_hardware_guard_without_hyperv(failure):
    """Somente arquivos descartáveis: não carrega módulo nem altera hardware/ACL global."""
    if os.name != "nt":
        pytest.skip("Guardas de arquivo/ACL Windows nativas.")
    script = files("bees_host.provisioning").joinpath("hyperv.ps1").read_text(encoding="utf-8")
    guards = script.split("# BEGIN_READONLY_GUARDS:", 1)[1].split("\n", 1)[1]
    guards = guards.split("# END_READONLY_GUARDS", 1)[0]
    with private_bridge_directory() as base:
        from bees_host.provisioning.journal import create

        target = base / "hardware.bin"
        create(target, b"own-test-hardware")
        if failure == "hardlink":
            os.link(target, base / "alias.bin")
        program = (
            """
$ErrorActionPreference='Stop';$ProgressPreference='SilentlyContinue'
[Console]::InputEncoding = [Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
$clock=[Diagnostics.Stopwatch]::StartNew()
function Write-TestPhase([string]$Phase) {
    [Console]::Error.WriteLine(('BEES_TEST_PHASE:'+$Phase+':'+$clock.ElapsedMilliseconds))
    [Console]::Error.Flush()
}
Write-TestPhase 'entered'
"""
            + guards
            + """
$line=[Console]::In.ReadLine()
Write-TestPhase 'input_read'
$p=$line | ConvertFrom-Json
Write-TestPhase 'input'
Initialize-NativeGuard
Write-TestPhase 'native_initialized'
if (-not (Test-OwnedPath $p.root $p.root)) {exit 2}
if (-not (Test-OwnedPath (Join-Path $p.root 'nested') $p.root)) {exit 3}
if (Test-OwnedPath ($p.root+'-foreign') $p.root) {exit 4}
Write-TestPhase 'path_checked'
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
Write-TestPhase 'acl_prepared'
try {Assert-HardwareNode $p.file $p.root $false ''; $allowed=$true}
catch {$allowed=$false}
if ($allowed -ne ($null -eq $p.failure)) {exit 5}
Write-TestPhase 'validated'
exit 0
"""
        )
        system = probe._system_directory()
        started = time.monotonic()
        with probe._external_library_search():
            try:
                result = subprocess.run(
                    [
                        str(system / "WindowsPowerShell/v1.0/powershell.exe"),
                        "-NoProfile",
                        "-NonInteractive",
                        "-EncodedCommand",
                        base64.b64encode(program.encode("utf-16-le")).decode(),
                    ],
                    input=(
                        json.dumps({"root": str(base), "file": str(target), "failure": failure})
                        + "\n"
                    ).encode(),
                    cwd=system,
                    env=probe._windows_environment(system),
                    timeout=15,
                    capture_output=True,
                    check=False,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
            except subprocess.TimeoutExpired as error:
                phases = (error.stderr or b"").decode(errors="replace")[-2048:]
                pytest.fail(
                    f"Guarda nativa excedeu 15s; caso={failure!r}; "
                    f"tempo={time.monotonic() - started:.3f}s; fases={phases!r}",
                    pytrace=False,
                )
        diagnostic = result.stderr.decode(errors="replace")
        assert result.returncode == 0, diagnostic
        assert not result.stdout
        phases = [line.split(":") for line in diagnostic.splitlines()]
        assert [phase[:2] for phase in phases] == [
            ["BEES_TEST_PHASE", name]
            for name in (
                "entered",
                "input_read",
                "input",
                "native_initialized",
                "path_checked",
                "acl_prepared",
                "validated",
            )
        ], diagnostic
        timings = [int(phase[2]) for phase in phases]
        assert timings == sorted(timings)
        print(f"Guarda nativa {failure!r}: {time.monotonic() - started:.3f}s; {diagnostic}")
