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
def test_hyperv_untrusted_output_never_implies_available(monkeypatch, output):
    monkeypatch.setattr(probe, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(probe.shutil, "which", lambda value: "powershell.exe")
    calls = []

    def command(argv):
        calls.append(argv)
        return output

    monkeypatch.setattr(probe, "_read_command", command)
    driver, value = probe.diagnose()
    assert driver == "hyperv" and value == {"platform": True, "module": False, "service": False}
    assert len(calls) == 1
    assert "Get-Module" in calls[0][-1] and "Get-Service" in calls[0][-1]
    assert not any(word in calls[0][-1] for word in ("Set-", "New-", "Install-", "Enable-"))


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
