import json

from test_tasks import headers
from test_tasks import state as state


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
