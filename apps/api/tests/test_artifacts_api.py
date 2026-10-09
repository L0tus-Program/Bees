"""Listagem e download autenticados; blobs reais em diretório próprio de teste."""

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from bees_api.app import create_app
from bees_api.config import Settings
from bees_core.artifacts import KEY_PREFIX
from bees_core.models import Agent, Artifact, Task

ORIGIN = "http://127.0.0.1:8000"
REPORT = "# Relatório\n\nConteúdo próprio de teste.\n".encode()


@pytest.fixture
def state(tmp_path):
    settings = Settings(data_dir=tmp_path / "state", web_dist=tmp_path / "missing")
    with TestClient(create_app(settings), base_url=ORIGIN) as client:
        token = client.app.state.identity.issue_bootstrap()
        assert (
            client.post(
                "/api/v1/auth/setup",
                json={"bootstrap_token": token, "name": "Teste", "password": "senha teste 123456"},
                headers={"Origin": ORIGIN},
            ).status_code
            == 200
        )
        first, other = Agent(name="Primeira"), Agent(name="Outra")
        task = Task(agent_id=first.id, title="Relatório", objective="Gerar relatório")
        with client.app.state.store.transaction() as unit:
            unit.agents.create(first)
            unit.agents.create(other)
            unit.tasks.create(task)
        artifact = client.app.state.artifacts.create(
            first.id, task.id, name="relatório final.md", media_type="text/markdown", content=REPORT
        )
        yield client, first, other, task, artifact, settings


def test_session_is_required_and_listing_exposes_only_safe_fields(state):
    client, first, _, task, artifact, _ = state
    path = f"/api/v1/agents/{first.id}/tasks/{task.id}/artifacts"
    anonymous = TestClient(client.app, base_url=ORIGIN)
    assert anonymous.get(path).status_code == 401
    assert (
        anonymous.get(f"/api/v1/agents/{first.id}/artifacts/{artifact.id}/download").status_code
        == 401
    )
    listed = client.get(path).json()
    assert listed["has_more"] is False and listed["next_offset"] is None
    [view] = listed["artifacts"]
    assert view["id"] == str(artifact.id) and view["status"] == "ready"
    assert view["sha256"] == artifact.sha256 and view["size_bytes"] == len(REPORT)
    assert "storage_key" not in view and "metadata" not in view
    assert KEY_PREFIX not in str(listed)


def test_download_is_verified_attachment_with_safe_headers(state):
    client, first, _, _, artifact, _ = state
    response = client.get(f"/api/v1/agents/{first.id}/artifacts/{artifact.id}/download")
    assert response.status_code == 200 and response.content == REPORT
    assert response.headers["content-type"] == "text/markdown; charset=utf-8"
    assert response.headers["content-disposition"] == (
        "attachment; filename=\"relat_rio_final.md\"; filename*=UTF-8''relat%C3%B3rio%20final.md"
    )
    assert "sandbox" in response.headers.get_list("content-security-policy")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["cache-control"] == "no-store"


def test_ids_from_other_agent_return_not_found_without_bytes(state):
    client, first, other, task, artifact, _ = state
    response = client.get(f"/api/v1/agents/{other.id}/artifacts/{artifact.id}/download")
    assert response.status_code == 404 and REPORT not in response.content
    assert client.get(f"/api/v1/agents/{other.id}/tasks/{task.id}/artifacts").status_code == 404
    assert client.get(f"/api/v1/agents/{first.id}/artifacts/{uuid4()}/download").status_code == 404


def test_tampered_blob_is_refused_without_path_or_content(state):
    client, first, _, _, artifact, settings = state
    blob = settings.data_dir / "artifacts" / "blobs" / str(artifact.id)
    blob.write_bytes(b"!" + REPORT[1:])
    response = client.get(f"/api/v1/agents/{first.id}/artifacts/{artifact.id}/download")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "artifact_integrity_failed"
    assert str(artifact.id) not in response.text.replace(str(first.id), "")
    assert "blobs" not in response.text and "Relat" not in response.text


def test_draft_is_listed_but_never_downloaded(state):
    client, first, _, task, _, _ = state
    draft_id = uuid4()
    with client.app.state.store.transaction() as unit:
        unit.artifacts.create(
            Artifact(
                id=draft_id,
                task_id=task.id,
                name="pendente.md",
                media_type="text/markdown",
                storage_key=KEY_PREFIX + str(draft_id),
                sha256="a" * 64,
                size_bytes=3,
                series_id=draft_id,
            )
        )
    response = client.get(f"/api/v1/agents/{first.id}/artifacts/{draft_id}/download")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "artifact_not_ready"
    page = client.get(f"/api/v1/agents/{first.id}/tasks/{task.id}/artifacts?limit=1").json()
    assert page["has_more"] is True and page["next_offset"] == 1
    rest = client.get(
        f"/api/v1/agents/{first.id}/tasks/{task.id}/artifacts?offset=1&limit=1"
    ).json()
    assert [item["status"] for item in page["artifacts"] + rest["artifacts"]] == [
        "ready",
        "draft",
    ]


def test_download_is_read_only_and_mutations_are_not_routed(state):
    client, first, _, task, artifact, _ = state
    path = f"/api/v1/agents/{first.id}/artifacts/{artifact.id}/download"
    csrf = client.get("/api/v1/auth/status").json()["csrf_token"]
    response = client.post(path, json={}, headers={"Origin": ORIGIN, "X-Bees-CSRF": csrf})
    assert response.status_code == 405
    with client.app.state.store.transaction(write=False) as unit:
        assert unit.artifacts.get(artifact.id) == artifact
