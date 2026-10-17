"""Logical portability is a human operation: authenticated, CSRF and private read."""

import io
import json
import zipfile
from uuid import uuid4

from fastapi.testclient import TestClient
from test_global_stop_api import ORIGIN, headers
from test_global_stop_api import state as state

PATH = "/api/v1/portability/exports"
CONSENT = {"consent": "export_private_data"}


def test_export_requires_human_session_csrf_and_explicit_closed_consent(state):
    client, _, _ = state
    anonymous = TestClient(client.app, base_url=ORIGIN)
    assert anonymous.post(PATH, json=CONSENT, headers={"Origin": ORIGIN}).status_code == 401
    assert anonymous.get(PATH + f"/{uuid4()}/download").status_code == 401
    assert client.post(PATH, json=CONSENT, headers={"Origin": ORIGIN}).status_code in (401, 403)
    for body in ({}, {"consent": "unknown"}, CONSENT | {"path": "../../escape"}):
        assert client.post(PATH, json=body, headers=headers(client)).status_code == 422
    assert client.get(PATH).status_code in (404, 405)


def test_export_download_excludes_live_identity_and_read_has_no_generation_effect(
    state, monkeypatch
):
    client, _, _ = state
    result = client.post(PATH, json=CONSENT, headers=headers(client))
    assert result.status_code == 200, result.text
    value = result.json()
    assert value["status"] == "ready"
    path = PATH + f"/{value['id']}/download"

    def unexpected(*args, **kwargs):
        raise AssertionError("Reading must not create another export")

    monkeypatch.setattr("bees_core.portability.ExportService.create", unexpected)
    response = client.get(path)
    assert response.status_code == 200
    assert "no-store" in response.headers["cache-control"]
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        names = archive.namelist()
        assert "data/agents.jsonl" in names
        assert not any(
            "identity" in name or "provisioning_" in name or "host_link" in name for name in names
        )
        assert b"senha teste 123456" not in response.content
    assert client.get(PATH + f"/{uuid4()}/download").status_code == 404
    assert client.get(PATH + "/bad/download").status_code == 422


def test_missing_and_adulterated_downloads_return_safe_error(state):
    client, _, _ = state
    value = client.post(PATH, json=CONSENT, headers=headers(client)).json()
    path = client.app.state.artifacts.root.parent / "exports" / "ready" / f"{value['id']}.zip"
    path.write_bytes(b"private corrupt content")
    response = client.get(PATH + f"/{value['id']}/download")
    assert response.status_code == 409
    assert "private corrupt content" not in response.text
    assert str(path) not in response.text


def test_export_rejects_credentials_in_json_keys_without_exposing_them(state):
    client, agent, _ = state
    token = "bx_" + "A" * 43
    with client.app.state.database.transaction() as connection:
        connection.execute(
            "UPDATE agents SET metadata_json=? WHERE id=?",
            (json.dumps({token: "synthetic-title"}), str(agent.id)),
        )
    response = client.post(PATH, json=CONSENT, headers=headers(client))
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "portability_sensitive_content"
    assert token not in response.text
    ready = client.app.state.artifacts.root.parent / "exports" / "ready"
    assert not list(ready.iterdir())
