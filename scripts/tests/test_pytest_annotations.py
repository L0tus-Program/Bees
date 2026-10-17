"""O canal de diagnóstico conserva falha real sem exportar conteúdo/parametrização."""

import importlib.util
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

SPEC = importlib.util.spec_from_file_location(
    "bees_pytest_annotations", Path(__file__).parents[1] / "pytest_annotations.py"
)
annotations_plugin = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(annotations_plugin)


@pytest.mark.parametrize("active", [True, False])
def test_real_failure_preserves_exit_status_and_annotations_are_metadata_only(tmp_path, active):
    test = tmp_path / "test_annotation_fixture.py"
    test.write_text(
        'import pytest\n@pytest.mark.parametrize("value", [1], ids=["PRIVATE_PARAMETER"])\n'
        'def test_failure(value):\n    assert False, "PRIVATE_FAILURE_CONTENT"\n',
        encoding="utf-8",
    )
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "pytest_annotations", str(test)],
        cwd=tmp_path,
        env={
            **os.environ,
            "GITHUB_ACTIONS": "true" if active else "false",
            "PYTHONPATH": str(Path(__file__).parents[1]),
            "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
        },
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 1
    annotations = [line for line in result.stdout.splitlines() if "::error title=" in line]
    assert len(annotations) == int(active)
    if active:
        assert "test_annotation_fixture.py::test_failure" in annotations[0]
        assert ",line=4::" in annotations[0]
        assert annotations[0].startswith("::error ")
        assert "PRIVATE_PARAMETER" not in annotations[0]
        assert "PRIVATE_FAILURE_CONTENT" not in annotations[0]


@pytest.mark.parametrize(
    "filename",
    [
        r"C:\Users\PRIVATE_PERSON\AppData\Local\Temp\test_fixture.py",
        "/tmp/PRIVATE_PERSON/test_fixture.py",
        "../../private/test_fixture.py",
    ],
)
def test_external_paths_keep_only_public_test_filename(filename):
    value = annotations_plugin._identifier(filename + "::test_failure[PRIVATE_PARAMETER]")
    assert value == "test_fixture.py::test_failure"


def test_case_ordinal_never_prints_parameter_or_upstream_line(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(annotations_plugin, "_CASES", {})
    nodeid = "packages/core/tests/test_fixture.py::test_failure[PRIVATE_PARAMETER]"
    report = SimpleNamespace(
        nodeid=nodeid,
        failed=True,
        location=("unused", 20, "test_failure"),
        longrepr=SimpleNamespace(reprcrash=SimpleNamespace(path="private/library.py", lineno=715)),
    )
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    annotations_plugin.pytest_collection_modifyitems([SimpleNamespace(nodeid=nodeid)])
    annotations_plugin.pytest_runtest_logreport(report)
    output = capsys.readouterr().out
    assert ",line=21::" in output and "(case 1)" in output
    assert "PRIVATE_PARAMETER" not in output and "private/library.py" not in output
