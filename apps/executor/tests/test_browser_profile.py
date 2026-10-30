"""Perfil do navegador da abelha sobrevive a reinícios do container."""

import os
import subprocess
import sys

import pytest

from bees_executor.browser import release_profile

pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="/proc e travas do Chromium.")


def test_stale_lock_from_another_host_is_released(tmp_path):
    profile = tmp_path / ".bees-browser"
    profile.mkdir()
    # Container anterior: trava aponta para outro hostname; o Chromium recusaria (código 21).
    os.symlink("hostantigo-120", profile / "SingletonLock")
    os.symlink("123", profile / "SingletonCookie")
    os.symlink("/tmp/inexistente/SingletonSocket", profile / "SingletonSocket")
    (profile / "Default").mkdir()
    release_profile(profile)
    assert sorted(item.name for item in profile.iterdir()) == ["Default"]


def test_orphan_browser_on_the_profile_is_stopped(tmp_path):
    profile = tmp_path / ".bees-browser"
    profile.mkdir()
    orphan = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)", f"--user-data-dir={profile}"]
    )
    other = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)", f"--user-data-dir={tmp_path}/x"]
    )
    try:
        release_profile(profile)
        assert orphan.wait(timeout=5) == -9
        assert other.poll() is None
    finally:
        for process in (orphan, other):
            process.kill()
            process.wait()
