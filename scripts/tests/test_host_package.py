"""Falhas de integridade de uma distribuição, sem construir ou publicar binários."""

import importlib.util
import json
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "host_package_build", Path(__file__).resolve().parents[1] / "build-host.py"
)
build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build)


@pytest.fixture
def package(tmp_path):
    folder = tmp_path / "pacote"
    folder.mkdir()
    (folder / "package-info.json").write_text(
        json.dumps({"runtime_dependencies": {"bees-host": "0.1.0"}}), encoding="utf-8"
    )
    (folder / "program.bin").write_bytes(b"trusted build")
    hashes = {
        file.name: {"sha256": build.digest(file), "size": file.stat().st_size}
        for file in folder.iterdir()
    }
    (folder / "SHA256SUMS.json").write_text(json.dumps(hashes), encoding="utf-8")
    build.verify(folder)
    return folder


@pytest.mark.parametrize("change", ["content", "size", "missing", "extra"])
def test_incomplete_or_changed_distribution_is_rejected(package, change):
    binary = package / "program.bin"
    if change == "content":
        binary.write_bytes(b"changed build")
    elif change == "size":
        binary.write_bytes(b"x")
    elif change == "missing":
        binary.unlink()
    else:
        (package / "unexpected.dll").write_bytes(b"x")
    with pytest.raises(ValueError):
        build.verify(package)


def test_control_plane_cannot_be_declared_in_distribution(package):
    info = package / "package-info.json"
    info.write_text(json.dumps({"runtime_dependencies": {"bees-core": "0.1.0"}}))
    hashes = json.loads((package / "SHA256SUMS.json").read_text())
    hashes[info.name] = {"sha256": build.digest(info), "size": info.stat().st_size}
    (package / "SHA256SUMS.json").write_text(json.dumps(hashes))
    with pytest.raises(ValueError, match="plano de controle"):
        build.verify(package)


def test_build_cannot_target_another_directory(tmp_path):
    with pytest.raises(ValueError, match="fora do checkout"):
        build.checked_output(tmp_path / "build")
    assert not (tmp_path / "build").exists()


def test_manifest_cannot_redirect_to_a_parent_file(package):
    manifest = package / "SHA256SUMS.json"
    hashes = json.loads(manifest.read_text())
    hashes["../program.bin"] = hashes.pop("program.bin")
    manifest.write_text(json.dumps(hashes))
    with pytest.raises(ValueError):
        build.verify(package)


def test_build_refuses_to_replace_a_package_with_runtime_state(package):
    state = package / "data" / "credentials.bin"
    state.parent.mkdir()
    state.write_bytes(b"private encrypted state")
    with pytest.raises(ValueError, match="preserve data"):
        build.reject_runtime_state(package)
    assert state.read_bytes() == b"private encrypted state"
