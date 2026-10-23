"""Launcher Linux/macOS com Docker simulado: código só no navegador, nunca em log/saída."""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
TOKEN = "A" * 21 + "_b-" + "C" * 19
pytestmark = pytest.mark.skipif(
    sys.platform == "win32" or shutil.which("bash") is None, reason="launcher POSIX"
)


def fake(tmp_path, receipt, *, ostype="linux"):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    calls = tmp_path / "calls.txt"
    docker = bin_dir / "docker"
    docker.write_text(
        "#!/usr/bin/env bash\n"
        f'echo "$*" >> "{calls}"\n'
        f'if [ "$1" = info ]; then echo {ostype}; exit 0; fi\n'
        'case "$*" in\n'
        f"  *bees-auth*) printf '%s' '{receipt}' ;;\n"
        "  *' ps '*) ;;\n"
        "  *) echo progresso ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    opened = tmp_path / "opened.txt"
    opener = bin_dir / "abrir"
    opener.write_text(f'#!/usr/bin/env bash\necho "$1" > "{opened}"\n', encoding="utf-8")
    for item in (docker, opener):
        item.chmod(0o755)
    env = os.environ | {
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "BEES_OPEN_COMMAND": str(opener),
    }
    return env, calls, opened


def run(env, tmp_path, *args):
    return subprocess.run(
        ["bash", str(ROOT / "scripts" / "start.sh"), "--log-dir", str(tmp_path / "logs"), *args],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_first_access_opens_private_fragment(tmp_path):
    env, calls, opened = fake(tmp_path, f'{{"configured":false,"bootstrap_token":"{TOKEN}"}}')
    result = run(env, tmp_path, "--port", "8090")
    assert result.returncode == 0, result.stderr
    assert opened.read_text().strip() == f"http://localhost:8090/#setup={TOKEN}"
    assert TOKEN not in result.stdout + result.stderr
    assert TOKEN not in (tmp_path / "logs" / "startup.log").read_text()
    log = calls.read_text()
    assert "compose --project-name bees" in log and "up --detach --wait" in log


def test_configured_installation_opens_login(tmp_path):
    env, _, opened = fake(tmp_path, '{"configured":true}')
    result = run(env, tmp_path, "--no-build")
    assert result.returncode == 0, result.stderr
    assert opened.read_text().strip() == "http://localhost:8080/"


def test_refuses_bad_receipt_and_missing_linux_engine(tmp_path):
    env, _, opened = fake(tmp_path, '{"configured":false,"bootstrap_token":"curto"}')
    result = run(env, tmp_path)
    assert result.returncode == 1 and not opened.exists()
    other = tmp_path / "other"
    other.mkdir()
    env, _, _ = fake(other, '{"configured":true}', ostype="windows")
    assert run(env, other).returncode == 1
    assert run(env, other, "--port", "0").returncode == 1
