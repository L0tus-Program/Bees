import json
import subprocess
import sys
from pathlib import Path

import pytest

from bees_core.security.identity import AuthError, IdentityService
from bees_core.storage.database import Database


def run_cli(data: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            "-c",
            "from bees_api.bootstrap_cli import main; main()",
            "bootstrap",
            "--data-dir",
            str(data),
            *arguments,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=15,
        check=False,
    )


def test_bootstrap_cli_requires_initialized_state(tmp_path: Path) -> None:
    data = tmp_path / "missing"
    response = run_cli(data)
    assert response.returncode == 2
    assert "Inicie bees-api" in response.stderr
    assert response.stdout == ""
    assert not data.exists()


def test_bootstrap_cli_renews_once_and_refuses_configured_identity(tmp_path: Path) -> None:
    database = Database(tmp_path / "bees.sqlite3")
    database.initialize()
    first = run_cli(tmp_path)
    second = run_cli(tmp_path)
    assert first.returncode == second.returncode == 0
    previous = first.stdout.splitlines()[1]
    token = second.stdout.splitlines()[1]
    assert len(previous) == len(token) == 43
    assert previous != token
    identity = IdentityService(database)
    with pytest.raises(AuthError):
        identity.setup(previous, "Teste", "senha de teste com 20 caracteres")
    identity.setup(bootstrap_token=token, name="Teste", password="senha de teste com 20 caracteres")
    refused = run_cli(tmp_path)
    assert refused.returncode == 2
    assert refused.stdout == ""
    assert previous not in refused.stderr and token not in refused.stderr
    with database.transaction(write=False) as connection:
        rows = connection.execute(
            "SELECT token_hash, consumed_at FROM identity_bootstrap"
        ).fetchall()
        assert len(rows) == 1 and rows[0][1] is not None
        assert token not in repr(rows) and previous not in repr(rows)


def test_launcher_json_never_requires_parsing_terminal_text(tmp_path: Path) -> None:
    database = Database(tmp_path / "bees.sqlite3")
    database.initialize()
    first = run_cli(tmp_path, "--format", "json")
    second = run_cli(tmp_path, "--format", "json")
    assert first.returncode == second.returncode == 0
    assert first.stderr == second.stderr == ""
    previous = json.loads(first.stdout)
    current = json.loads(second.stdout)
    assert set(current) == {"configured", "bootstrap_token"}
    assert current["configured"] is False
    assert current["bootstrap_token"] != previous["bootstrap_token"]
    assert len(current["bootstrap_token"]) == 43
    identity = IdentityService(database)
    with pytest.raises(AuthError):
        identity.setup(previous["bootstrap_token"], "Teste", "senha descartável de teste")
    identity.setup(current["bootstrap_token"], "Teste", "senha descartável de teste")
    configured = run_cli(tmp_path, "--format", "json")
    assert configured.returncode == 0
    assert configured.stderr == ""
    assert json.loads(configured.stdout) == {"configured": True}
    assert current["bootstrap_token"] not in configured.stdout


def test_launcher_invalid_state_has_no_json_or_token(tmp_path: Path) -> None:
    failed = run_cli(tmp_path, "--format", "json")
    assert failed.returncode == 2
    assert failed.stdout == ""
    assert "Inicie bees-api" in failed.stderr
