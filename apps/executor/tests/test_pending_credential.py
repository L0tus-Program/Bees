"""Temporário de pareamento é evidência pendente, inclusive após reinício."""

import json
from pathlib import Path

import pytest
from test_exchange_response import VALID

from bees_executor import cli, client
from bees_executor.state import state_lock


@pytest.mark.parametrize("existing", [False, True])
@pytest.mark.parametrize("pending", ["complete", "partial", "empty", "directory", "symlink"])
@pytest.mark.parametrize("operation", ["load", "exchange"])
def test_pending_credential_blocks_without_touching_evidence(
    tmp_path, monkeypatch, existing, pending, operation
):
    saved = tmp_path / "credential.json"
    temporary = tmp_path / "credential.tmp"
    if existing:
        saved.write_text(json.dumps(VALID | {"api": "http://localhost"}))
    if pending == "directory":
        temporary.mkdir()
    elif pending == "symlink":
        try:
            temporary.symlink_to(tmp_path / "missing-private-target")
        except OSError:
            pytest.skip("Symlink indisponível nesta conta.")
    else:
        temporary.write_bytes(
            {"complete": json.dumps(VALID).encode(), "partial": b'{"secret":', "empty": b""}[
                pending
            ]
        )
    before = saved.read_bytes() if existing else None
    temporary_before = temporary.lstat()

    def no_network(*args, **kwargs):
        pytest.fail("Estado pendente não autoriza consumir outro convite.")

    monkeypatch.setattr("urllib.request.OpenerDirector.open", no_network)
    with pytest.raises(client.CredentialPendingError, match="^executor_credential_pending$"):
        if operation == "load":
            client.load_credential(tmp_path)
        else:
            client.exchange("http://localhost", "private-invite", tmp_path)
    assert (saved.read_bytes() if existing else None) == before
    assert temporary.lstat() == temporary_before


@pytest.mark.parametrize("operation", ["load", "exchange"])
def test_pending_inspection_failure_is_closed(tmp_path, monkeypatch, operation):
    original = Path.lstat

    def unreadable(path, *args, **kwargs):
        if path.name == "credential.tmp":
            raise PermissionError("private-path-and-secret")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "lstat", unreadable)
    with pytest.raises(
        client.CredentialPendingError, match="^executor_credential_pending$"
    ) as caught:
        if operation == "load":
            client.load_credential(tmp_path)
        else:
            client.exchange("http://localhost", "private-invite", tmp_path)
    assert caught.value.__suppress_context__


@pytest.mark.parametrize("mode", ["personal", "computer"])
@pytest.mark.parametrize("existing", [False, True])
def test_cli_refuses_pending_before_resources_or_network(tmp_path, monkeypatch, mode, existing):
    state = tmp_path / "executor"
    state.mkdir()
    pending = state / "credential.tmp"
    pending.write_bytes(b"private-pending-identity")
    saved = state / "credential.json"
    if existing:
        saved.write_text(json.dumps(VALID | {"api": "http://localhost"}))
    journal = state / "journal.sqlite3"
    journal.write_bytes(b"private-journal-evidence")
    invite = tmp_path / "invite.json"
    invite.write_text('{"token":"private-invite"}')
    snapshot = {p.name: p.read_bytes() for p in state.iterdir()}

    def forbidden(*args, **kwargs):
        pytest.fail("Não preparar recursos ou rede antes da reconciliação.")

    monkeypatch.setattr(cli, "persistent_journal", forbidden)
    monkeypatch.setattr(cli, "ToolHost", forbidden)
    monkeypatch.setattr(cli, "exchange", forbidden)
    monkeypatch.setattr(cli.ExecutorClient, "run_forever", forbidden)
    from bees_executor import computers

    monkeypatch.setattr(computers, "SingleComputers", forbidden)
    with pytest.raises(SystemExit) as caught:
        cli.main([mode, "--state", str(state), "--invite-file", str(invite)])
    diagnostic = str(caught.value)
    assert diagnostic.startswith("executor_credential_pending:")
    assert "Traceback" not in diagnostic
    assert "private-" not in diagnostic
    assert str(tmp_path) not in diagnostic
    assert invite.read_text() == '{"token":"private-invite"}'
    assert {name: (state / name).read_bytes() for name in snapshot} == snapshot
    assert sorted(p.name for p in state.iterdir()) == sorted([*snapshot, "executor.lock"])
    with state_lock(state):
        pass
