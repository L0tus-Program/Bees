"""Verificador da wheel do executor: conteúdo restrito e reprodutibilidade."""

import importlib.util
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("check_wheel", ROOT / "scripts" / "check-wheel.py")
check_wheel = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check_wheel)

META = "bees_executor-0.1.0.dist-info/"


def build(path: Path, extra: dict | None = None) -> Path:
    files = {
        "bees_executor/__init__.py": "",
        "bees_executor/cli.py": "def main(): pass\n",
        META + "METADATA": (
            "Metadata-Version: 2.4\nName: bees-executor\nVersion: 0.1.0\n"
            "License-Expression: Apache-2.0\nLicense-File: LICENSE\nLicense-File: NOTICE\n"
        ),
        META + "licenses/LICENSE": (ROOT / "LICENSE").read_text(),
        META + "licenses/NOTICE": (ROOT / "NOTICE").read_text(),
        META + "entry_points.txt": "[console_scripts]\nbees-executor = bees_executor.cli:main\n",
    } | (extra or {})
    with zipfile.ZipFile(path, "w") as archive:
        for name, content in sorted(files.items()):
            info = zipfile.ZipInfo(name, date_time=(2020, 2, 2, 0, 0, 0))
            archive.writestr(info, content)
    return path


def test_accepts_reproducible_clean_wheel(tmp_path):
    first = build(tmp_path / "a.whl")
    second = build(tmp_path / "b.whl")
    out = tmp_path / "SHA256SUMS"
    assert check_wheel.main([str(first), str(second), "--out", str(out)]) == 0
    assert out.read_text().split()[0] == check_wheel.digest(first)


def test_rejects_secrets_foreign_files_and_drift(tmp_path):
    leaked = build(tmp_path / "a.whl", {"bees_executor/credential.json": "{}"})
    assert any("proibido" in item for item in check_wheel.problems(leaked))
    foreign = build(tmp_path / "b.whl", {"tests/test_x.py": ""})
    assert any("fora do pacote" in item for item in check_wheel.problems(foreign))
    first = build(tmp_path / "c.whl")
    second = build(tmp_path / "d.whl", {"bees_executor/extra.py": "x = 1\n"})
    assert check_wheel.main([str(first), str(second)]) == 1


def test_rejects_missing_spdx_or_changed_notices(tmp_path):
    no_spdx = build(tmp_path / "no-spdx.whl", {META + "METADATA": "Name: bees-executor\n"})
    assert any("SPDX" in error for error in check_wheel.problems(no_spdx))
    changed = build(tmp_path / "changed.whl", {META + "licenses/LICENSE": "another license"})
    assert any("LICENSE" in error for error in check_wheel.problems(changed))
    missing = tmp_path / "missing.whl"
    with zipfile.ZipFile(changed) as source, zipfile.ZipFile(missing, "w") as target:
        for name in source.namelist():
            if not name.endswith("licenses/NOTICE"):
                target.writestr(name, source.read(name))
    assert any("NOTICE" in error for error in check_wheel.problems(missing))
