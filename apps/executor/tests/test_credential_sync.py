"""Publicação de identidade exige sincronização do conteúdo temporário."""

import io
import json
import os
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from test_exchange_response import VALID

from bees_executor import client


@pytest.mark.parametrize("existing", [False, True])
def test_credential_content_is_synced_before_publication(tmp_path, monkeypatch, existing):
    path = tmp_path / "credential.json"
    temporary = path.with_suffix(".tmp")
    if existing:
        path.write_bytes(b"previous-private-identity")
    events = []
    original_sync, original_replace = os.fsync, os.replace

    def sync(descriptor):
        # A leitura por outro descritor exige flush do buffer Python primeiro.
        assert os.fstat(descriptor).st_size == len(json.dumps(VALID).encode())
        # O HANDLE Windows é exclusivo enquanto grava: não abrir outro leitor.
        if os.name != "nt":
            assert json.loads(temporary.read_bytes()) == VALID
            assert os.fstat(descriptor).st_ino == temporary.stat().st_ino
        if existing:
            assert path.read_bytes() == b"previous-private-identity"
        else:
            assert not path.exists()
        original_sync(descriptor)
        events.append("synced")

    def replace(source, destination):
        assert events == ["synced"]
        original_replace(source, destination)
        events.append("published")

    monkeypatch.setattr(client.os, "fsync", sync)
    monkeypatch.setattr(client.os, "replace", replace)
    client._write_private(path, VALID)
    assert events == ["synced", "published"]
    assert json.loads(path.read_bytes()) == VALID
    assert not temporary.exists()


@pytest.mark.parametrize("phase", ["flush", "sync"])
@pytest.mark.parametrize("kind", ["personal", "computer_runtime"])
@pytest.mark.parametrize("existing", [False, True])
def test_sync_failure_preserves_pending_identity_and_blocks_restart(
    tmp_path, monkeypatch, kind, existing, phase
):
    path = tmp_path / "credential.json"
    temporary = path.with_suffix(".tmp")
    payload = VALID | {"kind": kind}
    if existing:
        path.write_bytes(b"previous-private-identity")
    attempts = []

    def response(*args, **kwargs):
        attempts.append(True)
        return io.BytesIO(json.dumps(payload).encode())

    def fail(descriptor):
        expected = payload | {"api": "http://localhost"}
        assert os.fstat(descriptor).st_size == len(json.dumps(expected).encode())
        raise OSError("private-path-and-secret")

    monkeypatch.setattr("urllib.request.OpenerDirector.open", response)
    if phase == "sync":
        monkeypatch.setattr(client.os, "fsync", fail)
    else:
        original_fdopen = os.fdopen

        def fail_flush():
            raise OSError("private-path-and-secret")

        @contextmanager
        def buffered_file(*args, **kwargs):
            with original_fdopen(*args, **kwargs) as handle:
                yield SimpleNamespace(write=handle.write, flush=fail_flush, fileno=handle.fileno)

        monkeypatch.setattr(client.os, "fdopen", buffered_file)
    with pytest.raises(
        client.CredentialPersistenceError, match="^executor_credential_save_failed$"
    ) as caught:
        client.exchange("http://localhost", "private-invite", tmp_path, expected_kind=kind)
    assert caught.value.__suppress_context__
    before = temporary.read_bytes()
    assert json.loads(before) == payload | {"api": "http://localhost"}
    if existing:
        assert path.read_bytes() == b"previous-private-identity"
    else:
        assert not path.exists()
    with pytest.raises(client.CredentialPendingError, match="^executor_credential_pending$"):
        client.load_credential(tmp_path)
    with pytest.raises(client.CredentialPendingError, match="^executor_credential_pending$"):
        client.exchange("http://localhost", "another-private-invite", tmp_path, expected_kind=kind)
    assert attempts == [True]
    assert temporary.read_bytes() == before
