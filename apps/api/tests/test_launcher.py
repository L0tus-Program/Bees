"""Launcher Windows: autorização privada e erros sem vazar a saída do bootstrap."""

import os
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[3] / "scripts/start.ps1"
TOKEN = "T" * 43
pytestmark = pytest.mark.skipif(os.name != "nt", reason="Launcher PowerShell Windows.")


@pytest.mark.parametrize("mode", ["setup", "configured", "invalid", "failed"])
def test_launcher_captures_private_authorization_without_printing_or_logging(tmp_path, mode):
    stub = tmp_path / "docker.cmd"
    receipt = (
        '{"configured":true}'
        if mode == "configured"
        else '{"configured":false,"bootstrap_token":"' + TOKEN + '"}'
    )
    if mode == "invalid":
        receipt = '{"bootstrap_token":"' + TOKEN + '"BROKEN'
    stub.write_text(
        "@echo off\n"
        'if "%1"=="info" (echo linux & exit /b 0)\n'
        'echo %* | findstr /c:"bootstrap" >nul\n'
        "if %errorlevel%==0 goto bootstrap\n"
        "echo container preparing 1>&2\necho container ready\nexit /b 0\n"
        ":bootstrap\n"
        + (f"echo {TOKEN} 1>&2\nexit /b 1\n" if mode == "failed" else f"echo {receipt}\n"),
        encoding="ascii",
    )
    result = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-File",
            str(SCRIPT),
            "-NoBrowser",
            "-NoBuild",
            "-ProjectName",
            "bees-launcher-test",
            "-LogDirectory",
            str(tmp_path / "logs"),
        ],
        env=os.environ | {"PATH": str(tmp_path) + os.pathsep + os.environ["PATH"]},
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == (0 if mode in ("setup", "configured") else 1)
    assert TOKEN.encode() not in result.stdout + result.stderr
    assert TOKEN not in (tmp_path / "logs/startup.log").read_text(encoding="utf-16")


@pytest.mark.parametrize("changed", [False, True])
@pytest.mark.parametrize("no_build", [False, True])
def test_launcher_quiesces_existing_workers_only_when_image_changes(tmp_path, changed, no_build):
    trace = tmp_path / "calls.txt"
    stub = tmp_path / "docker.cmd"
    image = "sha256:new" if changed else "sha256:old"
    stub.write_text(
        "@echo off\n"
        f'echo %* >> "{trace}"\n'
        'if "%1"=="info" (echo linux & exit /b 0)\n'
        'if "%1"=="inspect" (echo sha256:old & exit /b 0)\n'
        f'if "%1"=="image" (echo {image} & exit /b 0)\n'
        'echo %* | findstr /c:" ps --all --quiet bees" >nul\n'
        "if %errorlevel%==0 (echo existing-container & exit /b 0)\n"
        'echo %* | findstr /c:"bootstrap" >nul\n'
        'if %errorlevel%==0 (echo {"configured":true} & exit /b 0)\n'
        "exit /b 0\n",
        encoding="ascii",
    )
    result = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-File",
            str(SCRIPT),
            "-NoBrowser",
            "-ProjectName",
            "bees-launcher-test",
            "-LogDirectory",
            str(tmp_path / "logs"),
        ]
        + (["-NoBuild"] if no_build else []),
        env=os.environ | {"PATH": str(tmp_path) + os.pathsep + os.environ["PATH"]},
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 0
    calls = trace.read_text(encoding="ascii").splitlines()
    stop = next((index for index, call in enumerate(calls) if " stop worker bees" in call), None)
    up = next(index for index, call in enumerate(calls) if " up --detach" in call)
    assert (stop is not None) is changed
    if changed:
        assert stop < up
