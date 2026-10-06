"""Emissão somente no operador com estado atual e identidade configurada."""

import json
import os
import subprocess
import sys

from bees_core.security.identity import IdentityService
from bees_core.storage.database import Database


def run_cli(path):
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "bees_api.host_cli",
            "invite",
            "--format",
            "json",
            "--data-dir",
            str(path),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=15,
    )


def test_missing_and_unconfigured_refuse_without_secret_or_mutation(tmp_path):
    absent = tmp_path / "absent"
    missing = run_cli(absent)
    assert missing.returncode == 2 and missing.stdout == ""
    assert not absent.exists()
    database = Database(tmp_path / "bees.sqlite3")
    database.initialize()
    refused = run_cli(tmp_path)
    assert refused.returncode == 2 and refused.stdout == ""
    assert "Traceback" not in refused.stderr


def test_configured_invite_is_private_and_does_not_replace_identity(tmp_path):
    database = Database(tmp_path / "bees.sqlite3")
    database.initialize()
    identity = IdentityService(database)
    identity.setup(identity.issue_bootstrap(), "Teste", "senha teste 123456")
    response = run_cli(tmp_path)
    assert response.returncode == 0, response.stderr
    value = json.loads(response.stdout)
    assert set(value) == {"installation_id", "invite_id", "invite_token", "expires_at"}
    assert value["invite_token"].startswith("bi_") and len(value["invite_token"]) == 46
    assert response.stderr == ""
    assert identity.configured() is True


def test_cli_uses_container_data_directory_from_environment(tmp_path):
    database = Database(tmp_path / "bees.sqlite3")
    database.initialize()
    identity = IdentityService(database)
    identity.setup(identity.issue_bootstrap(), "Teste", "senha teste 123456")
    response = subprocess.run(
        [
            sys.executable,
            "-m",
            "bees_api.host_cli",
            "invite",
            "--format",
            "json",
        ],
        env=os.environ | {"BEES_DATA_DIR": str(tmp_path)},
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=15,
    )
    assert response.returncode == 0, response.stderr
    assert len(json.loads(response.stdout)["invite_token"]) == 46
    assert response.stderr == ""
