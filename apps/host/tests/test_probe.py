import os
import sys
from types import SimpleNamespace

import pytest
from bees_host import probe


@pytest.mark.parametrize(
    "output",
    [
        None,
        '{"module":1,"service":true}',
        '{"module":"true","service":true}',
        '{"module":true,"service":true,"command":"evil"}',
        "not JSON",
    ],
)
def test_hyperv_untrusted_output_never_implies_available(monkeypatch, tmp_path, output):
    monkeypatch.setattr(probe, "os", SimpleNamespace(name="nt"))
    directory = tmp_path / "system32"
    executable = directory / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"not executed")
    monkeypatch.setattr(probe, "_system_directory", lambda: directory)
    monkeypatch.setattr(probe.shutil, "which", lambda value: pytest.fail("Consultou PATH"))
    calls = []

    def command(argv, **kwargs):
        calls.append(argv)
        assert kwargs["cwd"] == directory
        assert kwargs["env"]["PSModulePath"] == str(executable.parent / "Modules")
        return output

    monkeypatch.setattr(probe, "_read_command", command)
    driver, value = probe.diagnose()
    assert driver == "hyperv" and value == {"platform": True, "module": False, "service": False}
    assert len(calls) == 1
    assert "Get-Module" in calls[0][-1] and "Get-Service" in calls[0][-1]
    assert not any(word in calls[0][-1] for word in ("Set-", "New-", "Install-", "Enable-"))
    assert calls[0][0] == str(executable)


def test_windows_path_cwd_env_and_missing_trusted_executable_fail_closed(monkeypatch, tmp_path):
    fake = tmp_path / "powershell.exe"
    fake.write_bytes(b"never execute")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setenv("SystemRoot", str(tmp_path))
    monkeypatch.setenv("PSModulePath", str(tmp_path))
    monkeypatch.setenv("BEES_TEST_SECRET", "do-not-forward")
    monkeypatch.setattr(probe, "os", SimpleNamespace(name="nt"))
    directory = tmp_path / "trusted-system"
    monkeypatch.setattr(probe, "_system_directory", lambda: directory)
    monkeypatch.setattr(probe, "_read_command", lambda *a, **k: pytest.fail("Executou PATH/CWD"))
    assert probe.diagnose() == ("hyperv", {"platform": True, "module": False, "service": False})
    assert "BEES_TEST_SECRET" not in probe._windows_environment(directory)
    assert probe._windows_environment(directory)["PSModulePath"] != str(tmp_path)
    monkeypatch.setattr(probe, "_system_directory", lambda: None)
    assert probe.diagnose()[1]["service"] is False


class DllKernel:
    def __init__(self, directory="bundle-internal", *, set_failure=False, oversized=False):
        self.directory = directory
        self.calls = []
        self.set_failure = set_failure
        self.oversized = oversized

    def GetDllDirectoryW(self, size, buffer):
        buffer.value = self.directory
        return size if self.oversized else len(self.directory)

    def SetDllDirectoryW(self, directory):
        self.calls.append(directory)
        if self.set_failure:
            return 0
        self.directory = directory or ""
        return 1


@pytest.mark.parametrize("raises", [False, True])
def test_frozen_external_spawn_restores_dll_directory_even_on_exception(monkeypatch, raises):
    kernel = DllKernel()
    monkeypatch.setattr(probe, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(probe.sys, "frozen", True, raising=False)
    monkeypatch.setattr(probe, "_kernel32", lambda: kernel)
    try:
        with probe._external_library_search():
            assert kernel.directory == ""
            if raises:
                raise OSError("spawn failed")
    except OSError:
        assert raises
    assert kernel.calls == [None, "bundle-internal"]
    assert kernel.directory == "bundle-internal"


@pytest.mark.parametrize("changes", [{"set_failure": True}, {"oversized": True}])
def test_frozen_dll_sanitization_failure_never_spawns(monkeypatch, changes):
    kernel = DllKernel(**changes)
    monkeypatch.setattr(probe, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(probe.sys, "frozen", True, raising=False)
    monkeypatch.setattr(probe, "_kernel32", lambda: kernel)
    with pytest.raises(OSError):
        with probe._external_library_search():
            pytest.fail("Sanitização falhou mas processo seria iniciado")


def test_unfrozen_process_does_not_modify_dll_search(monkeypatch):
    monkeypatch.delattr(probe.sys, "frozen", raising=False)
    monkeypatch.setattr(probe, "_kernel32", lambda: pytest.fail("Alterou DLLs sem congelamento"))
    with probe._external_library_search():
        pass


def test_frozen_spawn_failure_restores_before_returning_unavailable(monkeypatch):
    kernel = DllKernel()
    monkeypatch.setattr(probe, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(probe.sys, "frozen", True, raising=False)
    monkeypatch.setattr(probe, "_kernel32", lambda: kernel)

    def failed(argv, **kwargs):
        assert kernel.directory == "" and kwargs["shell"] is False
        raise OSError("Processo externo indisponível")

    monkeypatch.setattr(probe.subprocess, "Popen", failed)
    assert probe._read_command(["not spawned"]) is None
    assert kernel.calls == [None, "bundle-internal"]


def test_linux_probe_never_installs_libvirt_or_infers_service_from_kvm(monkeypatch):
    monkeypatch.setattr(
        probe, "os", SimpleNamespace(name="posix", access=lambda *args: True, R_OK=4, W_OK=2)
    )
    monkeypatch.setattr(probe.shutil, "which", lambda value: None)
    assert probe.diagnose() == ("libvirt", {"platform": True, "kvm": True, "service": False})


def test_command_timeout_and_output_limit_are_real():
    assert (
        probe._read_command([sys.executable, "-c", "import time; time.sleep(3)"], timeout=0.05)
        is None
    )
    assert probe._read_command([sys.executable, "-c", "print('x' * 8193)"]) is None
    assert probe._read_command([sys.executable, "-c", "import sys; sys.exit(1)"]) is None
    assert probe._read_command([sys.executable, "-c", "print('OK')"]).splitlines() == ["OK"]


@pytest.mark.skipif(os.name != "nt", reason="Host Hyper-V real somente leitura Windows")
def test_native_windows_diagnostic_is_boolean_and_not_a_vm_claim():
    driver, value = probe.diagnose()
    assert driver == "hyperv" and set(value) == {"platform", "module", "service"}
    assert all(type(v) is bool for v in value.values())
    assert value["platform"] is True


@pytest.mark.skipif(os.name != "nt", reason="DLL search nativo somente Windows")
def test_native_frozen_library_search_restores_after_real_powershell(monkeypatch, tmp_path):
    kernel = probe._kernel32()
    buffer = probe.ctypes.create_unicode_buffer(32768)
    size = kernel.GetDllDirectoryW(len(buffer), buffer)
    original = buffer.value if size else None
    bundle_directory = str(tmp_path)
    assert kernel.SetDllDirectoryW(bundle_directory)
    try:
        monkeypatch.setattr(probe.sys, "frozen", True, raising=False)
        monkeypatch.setenv("SystemRoot", str(tmp_path))
        system = probe._system_directory()
        assert system is not None and system != tmp_path
        driver, value = probe.diagnose()
        assert driver == "hyperv" and all(type(v) is bool for v in value.values())
        restored = probe.ctypes.create_unicode_buffer(32768)
        assert kernel.GetDllDirectoryW(len(restored), restored) == len(bundle_directory)
        assert restored.value == bundle_directory
    finally:
        assert kernel.SetDllDirectoryW(original)
