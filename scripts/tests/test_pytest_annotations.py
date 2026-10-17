"""O canal de diagnóstico conserva falha real sem exportar conteúdo/parametrização."""

import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize("active", [True, False])
def test_real_failure_preserves_exit_status_and_annotations_are_metadata_only(tmp_path, active):
    test = tmp_path / "test_annotation_fixture.py"
    test.write_text(
        'import pytest\n@pytest.mark.parametrize("value", [1], ids=["PRIVATE_PARAMETER"])\n'
        'def test_failure(value):\n    assert False, "PRIVATE_FAILURE_CONTENT"\n',
        encoding="utf-8",
    )
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "scripts.pytest_annotations", str(test)],
        cwd=Path(__file__).parents[2],
        env={**os.environ, "GITHUB_ACTIONS": "true" if active else "false"},
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
