from types import SimpleNamespace

import pytest

from bees_guest import cli
from bees_guest.errors import GuestError


def test_check_is_readonly_no_journal_connection_or_network(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(cli, "sys", SimpleNamespace(platform="linux", stderr=cli.sys.stderr))
    monkeypatch.setattr(cli, "load_identity", lambda _path: object())
    monkeypatch.setattr(cli, "tls_context", lambda _path: object())
    monkeypatch.setattr(cli, "Journal", lambda *_args: pytest.fail("check created state"))
    monkeypatch.setattr(cli, "connect", lambda *_args: pytest.fail("check accessed transport"))
    assert cli.main(["--identity-dir", str(tmp_path), "--check"]) == 0
    assert capsys.readouterr().out.strip() == '{"configured": true, "vm_ready": false}'
    assert list(tmp_path.iterdir()) == []


def test_cli_never_initializes_missing_state(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(cli, "sys", SimpleNamespace(platform="linux", stderr=cli.sys.stderr))
    monkeypatch.setattr(cli, "load_identity", lambda _path: object())
    monkeypatch.setattr(cli, "tls_context", lambda _path: object())

    def missing(*_args):
        raise GuestError("journal_missing")

    monkeypatch.setattr(cli, "Journal", missing)
    monkeypatch.setattr(cli, "connect", lambda *_args: pytest.fail("missing state connected"))
    assert cli.main(["--identity-dir", str(tmp_path), "--once"]) == 1
    assert capsys.readouterr().err.strip() == "Bees guest: journal_missing"
    assert list(tmp_path.iterdir()) == []


def test_cli_platform_and_external_errors_are_closed(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(cli, "sys", SimpleNamespace(platform="win32", stderr=cli.sys.stderr))
    monkeypatch.setattr(cli, "load_identity", lambda _path: pytest.fail("non-linux loaded config"))
    assert cli.main(["--identity-dir", str(tmp_path), "--check"]) == 1
    assert capsys.readouterr().err.strip() == "Bees guest: linux_required"
    monkeypatch.setattr(cli, "sys", SimpleNamespace(platform="linux", stderr=cli.sys.stderr))

    def inaccessible(_path):
        raise OSError("private path and credential")

    monkeypatch.setattr(cli, "load_identity", inaccessible)
    assert cli.main(["--identity-dir", str(tmp_path), "--check"]) == 1
    assert capsys.readouterr().err.strip() == "Bees guest: configuration_unavailable"
