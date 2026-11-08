import json
from pathlib import Path

from test_tasks import headers
from test_tasks import state as state

from bees_core.executors import ExecutorService


def test_personal_pairing_preserves_pending_computer_invite(state, tmp_path):
    client, _, _, _, settings = state
    link_dir = tmp_path / "computer-link"
    client.app.state.settings = settings.model_copy(update={"computer_link_dir": link_dir})
    invite_file = link_dir / "invite.json"
    auth = headers(client)

    computer = client.post(
        "/api/v1/executors/invites",
        json={"kind": "computer_runtime", "name": "Computador"},
        headers=auth,
    )
    assert computer.status_code == 200
    assert computer.json()["delivered_locally"] is True
    pending = invite_file.read_text()
    assert json.loads(pending)["token"] == computer.json()["token"]

    personal = client.post(
        "/api/v1/executors/invites",
        json={"kind": "personal", "name": "Conector pessoal"},
        headers=auth,
    )
    assert personal.status_code == 200
    assert personal.json()["delivered_locally"] is False

    paired = client.post("/api/v1/executors/exchange", json={"token": personal.json()["token"]})
    assert paired.status_code == 200
    assert paired.json()["kind"] == "personal"
    assert invite_file.exists()
    assert invite_file.read_text() == pending

    paired = client.post(
        "/api/v1/executors/exchange", json={"token": json.loads(invite_file.read_text())["token"]}
    )
    assert paired.status_code == 200
    assert paired.json()["kind"] == "computer_runtime"
    assert not invite_file.exists()


def test_computer_pairing_returns_credential_when_invite_cleanup_fails(
    state, tmp_path, monkeypatch, caplog
):
    client, _, _, _, settings = state
    link_dir = tmp_path / "computer-link"
    client.app.state.settings = settings.model_copy(update={"computer_link_dir": link_dir})
    invite_file = link_dir / "invite.json"
    invite = client.post(
        "/api/v1/executors/invites",
        json={"kind": "computer_runtime", "name": "Computador"},
        headers=headers(client),
    )
    assert invite.status_code == 200
    token = invite.json()["token"]
    original_unlink = Path.unlink

    def fail_invite_unlink(path, *args, **kwargs):
        if path == invite_file:
            raise PermissionError("volume somente leitura")
        return original_unlink(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "unlink", fail_invite_unlink)
        paired = client.post("/api/v1/executors/exchange", json={"token": token})

    assert paired.status_code == 200
    receipt = paired.json()
    assert receipt["kind"] == "computer_runtime"
    assert invite_file.exists()
    assert "Falha ao limpar o convite local já consumido" in caplog.text
    assert token not in caplog.text
    assert receipt["credential"] not in caplog.text

    executor = ExecutorService(client.app.state.database).authenticate(receipt["credential"])
    assert executor["id"] == receipt["executor_id"]

    replay = client.post("/api/v1/executors/exchange", json={"token": token})
    assert replay.status_code == 401
    assert replay.json()["error"]["code"] == "executor_invite_invalid"


def test_older_computer_pairing_preserves_newer_invite(state, tmp_path):
    client, _, _, _, settings = state
    link_dir = tmp_path / "computer-link"
    client.app.state.settings = settings.model_copy(update={"computer_link_dir": link_dir})
    invite_file = link_dir / "invite.json"
    auth = headers(client)
    older = client.post(
        "/api/v1/executors/invites",
        json={"kind": "computer_runtime", "name": "Computador anterior"},
        headers=auth,
    )
    newer = client.post(
        "/api/v1/executors/invites",
        json={"kind": "computer_runtime", "name": "Computador mais recente"},
        headers=auth,
    )
    assert older.status_code == newer.status_code == 200
    pending = invite_file.read_text()
    assert json.loads(pending)["token"] == newer.json()["token"]

    older_exchange = client.post(
        "/api/v1/executors/exchange", json={"token": older.json()["token"]}
    )
    assert older_exchange.status_code == 200
    assert invite_file.read_text() == pending

    newer_exchange = client.post(
        "/api/v1/executors/exchange", json={"token": json.loads(pending)["token"]}
    )
    assert newer_exchange.status_code == 200
    assert not invite_file.exists()


def test_computer_pairing_preserves_unreadable_invite_file(state, tmp_path, caplog):
    client, _, _, _, settings = state
    link_dir = tmp_path / "computer-link"
    client.app.state.settings = settings.model_copy(update={"computer_link_dir": link_dir})
    invite_file = link_dir / "invite.json"
    invite = client.post(
        "/api/v1/executors/invites",
        json={"kind": "computer_runtime", "name": "Computador"},
        headers=headers(client),
    )
    assert invite.status_code == 200
    invite_file.write_bytes(b"\xff")

    paired = client.post("/api/v1/executors/exchange", json={"token": invite.json()["token"]})

    assert paired.status_code == 200
    assert paired.json()["kind"] == "computer_runtime"
    assert invite_file.read_bytes() == b"\xff"
    assert "Falha ao limpar o convite local já consumido" in caplog.text
