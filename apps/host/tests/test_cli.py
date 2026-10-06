"""Ação fechada de inspeção privada não inicia nem corrige a instalação."""

import os

import pytest
from bees_host import cli
from bees_host.security import check_private


def private_directory(tmp_path):
    directory = tmp_path / "pasta privativa á"
    directory.mkdir(mode=0o700)
    check_private(directory, directory=True, protect=True)
    return directory


def forbid_runtime(monkeypatch):
    for name in ("native_cipher", "StateStore", "Runtime"):
        monkeypatch.setattr(cli, name, lambda *a, **k: pytest.fail("Inspeção iniciou runtime"))


def test_check_directory_requires_no_state_crypto_store_or_network(tmp_path, monkeypatch, capsys):
    directory = private_directory(tmp_path)
    forbid_runtime(monkeypatch)
    assert cli.main(["--check-private", str(directory), "--directory"]) == 0
    assert list(directory.iterdir()) == []
    output = capsys.readouterr()
    assert output.out == output.err == ""


def test_check_file_is_readonly_and_preserves_contents(tmp_path, monkeypatch, capsys):
    directory = private_directory(tmp_path)
    path = directory / "convite privado.json"
    path.write_bytes(b"conteudo privado que nao deve ser lido ou mostrado")
    check_private(path, protect=True)
    forbid_runtime(monkeypatch)
    before = path.stat()
    assert cli.main(["--check-private", str(path)]) == 0
    assert path.stat().st_mtime_ns == before.st_mtime_ns
    assert path.read_bytes() == b"conteudo privado que nao deve ser lido ou mostrado"
    output = capsys.readouterr()
    assert output.out == output.err == ""


@pytest.mark.parametrize("case", ["missing", "insecure", "directory_as_file", "file_as_directory"])
def test_check_invalid_path_returns_fixed_error_without_revealing_path(
    tmp_path, monkeypatch, capsys, case
):
    directory = private_directory(tmp_path)
    path = directory / "segredo no nome da pasta.txt"
    directory_flag = False
    if case != "missing":
        path.write_text("segredo do conteudo")
        if case != "insecure":
            check_private(path, protect=True)
        elif os.name != "nt":
            path.chmod(0o644)
    if case == "directory_as_file":
        path = directory
    elif case == "file_as_directory":
        directory_flag = True
    forbid_runtime(monkeypatch)
    args = ["--check-private", str(path)] + (["--directory"] if directory_flag else [])
    assert cli.main(args) == 1
    output = capsys.readouterr()
    assert output.out == "" and output.err == "Bees host: private_path_required\n"
    assert path.exists() is (case != "missing")


@pytest.mark.parametrize(
    "args",
    [
        ["--directory"],
        [],
        ["--check-private", "private", "--state-dir", "runtime"],
        ["--check-private", "private", "--once"],
        ["--check-private", "private", "--bootstrap-file", "bootstrap"],
        ["--check-private", "private", "--new-pair"],
    ],
)
def test_check_cannot_be_combined_with_runtime_and_directory_is_closed(monkeypatch, args):
    forbid_runtime(monkeypatch)
    with pytest.raises(SystemExit) as error:
        cli.main(args)
    assert error.value.code == 2


def test_link_in_ancestor_is_not_resolved_away(tmp_path, monkeypatch, capsys):
    directory = private_directory(tmp_path)
    link = tmp_path / "link"
    try:
        link.symlink_to(directory, target_is_directory=True)
    except OSError:
        pytest.skip("Conta sem permissão para criar symlink.")
    forbid_runtime(monkeypatch)
    assert cli.main(["--check-private", str(link), "--directory"]) == 1
    assert capsys.readouterr().err == "Bees host: private_path_required\n"
