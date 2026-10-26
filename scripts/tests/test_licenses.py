"""Distribution metadata and shipped notices must agree with the chosen license."""

import json
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PACKAGES = ("apps/api", "apps/worker", "apps/host", "apps/guest", "apps/executor", "packages/core")


def test_python_distributions_include_the_chosen_license_and_notice():
    canonical = {name: (ROOT / name).read_bytes() for name in ("LICENSE", "NOTICE")}
    assert b"Version 2.0, January 2004" in canonical["LICENSE"]
    for directory in (".", *PACKAGES):
        project = tomllib.loads((ROOT / directory / "pyproject.toml").read_text())["project"]
        assert project["license"] == "Apache-2.0"
        assert project["license-files"] == ["LICENSE", "NOTICE"]
        for name, expected in canonical.items():
            assert (ROOT / directory / name).read_bytes() == expected


def test_web_notices_cover_locked_production_and_peer_packages():
    web = ROOT / "apps/web"
    package = json.loads((web / "package.json").read_text())
    lock = json.loads((web / "package-lock.json").read_text())
    assert package["license"] == lock["packages"][""]["license"] == "Apache-2.0"
    notices = (web / "public/third-party-notices.txt").read_text()
    for name, details in lock["packages"].items():
        if name and not details.get("dev"):
            assert name.removeprefix("node_modules/") + " " + details["version"] in notices
    assert "Mozilla Public License Version 2.0" in notices
