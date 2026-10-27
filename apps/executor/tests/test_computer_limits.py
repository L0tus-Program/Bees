"""Limites por conta do computador container: prefixo prlimit real e valores validados."""

import shutil
import subprocess
import sys

import pytest

from bees_executor.computers import account_limits

pytestmark = pytest.mark.skipif(
    sys.platform != "linux" or shutil.which("prlimit") is None, reason="prlimit do util-linux."
)


def test_defaults_apply_to_the_spawned_process_and_its_children():
    prefix = account_limits({})
    assert prefix[0] == shutil.which("prlimit") and prefix[-1] == "--"
    result = subprocess.run(
        prefix + ["/bin/bash", "-c", "ulimit -u; ulimit -f; ulimit -c; bash -c 'ulimit -u'"],
        capture_output=True,
        text=True,
        check=True,
    )
    # bash reporta -f em blocos de 1024 bytes.
    assert result.stdout.split() == ["512", str(4096 * 1024), "0", "512"]


def test_configured_values_and_invalid_input_fall_back():
    custom = account_limits(
        {"BEES_COMPUTER_MAX_PROCESSES": "128", "BEES_COMPUTER_MAX_FILE_MB": "64"}
    )
    assert "--nproc=128" in custom and f"--fsize={64 * 1024 * 1024}" in custom
    for invalid in ({"BEES_COMPUTER_MAX_PROCESSES": "x"}, {"BEES_COMPUTER_MAX_PROCESSES": "1"}):
        assert "--nproc=512" in account_limits(invalid)


def test_big_file_write_is_refused_by_the_kernel(tmp_path):
    prefix = account_limits({"BEES_COMPUTER_MAX_FILE_MB": "16"})
    target = tmp_path / "grande.bin"
    result = subprocess.run(
        prefix + ["/bin/bash", "-c", f"head -c 20000000 /dev/zero > {target}"],
        capture_output=True,
        check=False,
    )
    assert result.returncode != 0
    assert target.stat().st_size <= 16 * 1024 * 1024
