"""Upload humano real, fronteiras antes do stream e versões sem sobrescrita."""

import asyncio
import base64
import hashlib
import json
import socket
import threading
import time
from contextlib import contextmanager
from uuid import uuid4

import httpx
import pytest
import uvicorn
from test_delegation_api import ORIGIN, headers
from test_delegation_api import state as state  # noqa: F401

from bees_core.artifacts import ArtifactStore
from bees_core.models import Conversation, Task


def path(agent, conversation, artifact_id=None):
    value = f"/api/v1/agents/{agent.id}/conversations/{conversation.id}/artifacts"
    return value + f"/{artifact_id}/content" if artifact_id else value


def upload_headers(client, content=b"arquivo", **changes):
    metadata = {
        "name": "arquivo.md",
        "media_type": "text/markdown",
        "size_bytes": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
        "previous_id": None,
        "expected_revision": None,
    } | changes
    return headers(client) | {
        "Content-Type": "application/octet-stream",
        "Content-Length": str(len(content)),
        "X-Bees-Artifact": base64.urlsafe_b64encode(
            json.dumps(metadata, ensure_ascii=False).encode()
        )
        .decode()
        .rstrip("="),
    }


def upload(state, content=b"arquivo", *, request_id=None, **metadata):
    client, _, agent, _, conversation, _, _ = state
    return client.put(
        path(agent, conversation, request_id or uuid4()),
        content=content,
        headers=upload_headers(client, content, **metadata),
    )


def drive(client, request_path, values, chunks):
    sent, reads = [], []
    iterator = iter(chunks)
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "PUT",
        "scheme": "http",
        "path": request_path,
        "raw_path": request_path.encode(),
        "query_string": b"",
        "server": ("127.0.0.1", 8000),
        "client": ("127.0.0.1", 44000),
        "headers": [(b"host", b"127.0.0.1:8000")] + values,
    }

    async def receive():
        reads.append(True)
        return next(iterator)

    async def send(message):
        sent.append(message)

    asyncio.run(client.app(scope, receive, send))
    status = next(item["status"] for item in sent if item["type"] == "http.response.start")
    body = b"".join(item.get("body", b"") for item in sent if item["type"] == "http.response.body")
    return status, json.loads(body), reads


def asgi_headers(client, **changes):
    values = upload_headers(client) | {
        "Cookie": "; ".join(f"{key}={value}" for key, value in client.cookies.items())
    }
    values |= changes
    return [(key.lower().encode(), value.encode()) for key, value in values.items()]


def test_upload_beyond_json_limit_preview_exact_and_readonly(state):
    client, _, agent, _, conversation, requests, _ = state
    content = ("á" * 40000 + "<script>alert('dados')</script>").encode()
    response = upload(state, content)
    assert response.status_code == 200, response.text
    artifact = response.json()
    assert artifact["source"] == "user_upload" and artifact["task_id"] is None
    assert artifact["agent_id"] == str(agent.id) and artifact["conversation_id"] == str(
        conversation.id
    )
    base = path(agent, conversation) + "/" + artifact["id"]
    with client.app.state.store.transaction(write=False) as unit:
        before = unit.artifacts._context.connection.execute(
            "SELECT MAX(seq) FROM domain_events"
        ).fetchone()[0]
    preview = client.get(base + "/preview?limit=65535").json()
    assert preview["truncated"] and preview["total_bytes"] == len(content)
    assert len(preview["text"].encode()) == 65534
    assert client.get(base).json() == artifact
    assert client.get(path(agent, conversation)).json()["artifacts"] == [artifact]
    downloaded = client.get(f"/api/v1/agents/{agent.id}/artifacts/{artifact['id']}/download")
    assert (
        downloaded.content == content and "attachment" in downloaded.headers["content-disposition"]
    )
    with client.app.state.store.transaction(write=False) as unit:
        assert (
            unit.artifacts._context.connection.execute(
                "SELECT MAX(seq) FROM domain_events"
            ).fetchone()[0]
            == before
        )
    assert requests == []
    assert (
        client.post(
            "/api/v1/auth/login",
            content=b" " * 65537,
            headers={"Origin": ORIGIN, "Content-Type": "application/json"},
        ).status_code
        == 413
    )


@pytest.mark.parametrize(
    "case,status",
    [
        ("anonymous", 401),
        ("csrf", 403),
        ("binding", 404),
        ("duplicate_length", 400),
        ("missing_length", 400),
        ("encoding", 422),
        ("oversize", 413),
        ("duplicate_metadata", 422),
        ("invalid_metadata", 422),
        ("origin", 403),
        ("type", 415),
    ],
)
def test_rejected_upload_never_consumes_body(state, case, status):
    client, _, agent, other, conversation, _, _ = state
    request_path = path(agent, conversation, uuid4())
    values = asgi_headers(client)
    if case == "anonymous":
        values = [(key, value) for key, value in values if key != b"cookie"]
    if case == "csrf":
        values = [(key, value) for key, value in values if key != b"x-bees-csrf"]
    if case == "binding":
        request_path = path(other, conversation, uuid4())
    if case == "duplicate_length":
        values.append((b"content-length", b"7"))
    if case == "missing_length":
        values = [(key, value) for key, value in values if key != b"content-length"]
    if case == "encoding":
        values.append((b"content-encoding", b"gzip"))
    if case == "oversize":
        values = [
            (key, b"26214401" if key == b"content-length" else value) for key, value in values
        ]
    if case == "duplicate_metadata":
        values.append((b"x-bees-artifact", b"e30"))
    if case == "invalid_metadata":
        values = [(key, b"!" if key == b"x-bees-artifact" else value) for key, value in values]
    if case == "origin":
        values = [
            (key, b"https://evil.invalid" if key == b"origin" else value) for key, value in values
        ]
    if case == "type":
        values = [
            (key, b"text/plain" if key == b"content-type" else value) for key, value in values
        ]
    actual, _, reads = drive(client, request_path, values, [])
    assert actual == status and reads == []


@pytest.mark.parametrize("chunks", [[b"arquivo", b"excesso"], [b"curto"], [b"xxxxxxx"]])
def test_actual_stream_length_and_digest_are_enforced_without_draft(state, chunks):
    client, _, agent, _, conversation, requests, _ = state
    messages = [
        {"type": "http.request", "body": chunk, "more_body": i < len(chunks) - 1}
        for i, chunk in enumerate(chunks)
    ]
    status, body, reads = drive(
        client, path(agent, conversation, uuid4()), asgi_headers(client), messages
    )
    assert status in (400, 422) and reads
    assert body["error"]["code"] in ("invalid_length", "artifact_content_mismatch")
    assert client.get(path(agent, conversation)).json()["artifacts"] == [] and requests == []


def test_revision_replay_conflict_and_lost_publication_reconcile(state, monkeypatch):
    client, _, agent, _, conversation, requests, _ = state
    first = upload(state).json()
    revision_id = uuid4()
    metadata = {"previous_id": first["id"], "expected_revision": first["revision"]}
    second = upload(state, b"revisado", request_id=revision_id, **metadata)
    assert second.status_code == 200, second.text
    revised = second.json()
    assert revised["source"] == "user_revision" and revised["version"] == 2
    assert upload(state, b"revisado", request_id=revision_id, **metadata).json() == revised
    assert upload(state, b"divergente", request_id=revision_id, **metadata).status_code == 409
    assert upload(state, b"concorrente", **metadata).status_code == 409
    assert client.get(path(agent, conversation) + "/" + first["id"]).json() == first
    original = ArtifactStore.upload

    def lose(self, *args, **kwargs):
        original(self, *args, **kwargs)
        raise RuntimeError("Perda controlada após publicação")

    monkeypatch.setattr(ArtifactStore, "upload", lose)
    lost_id = uuid4()
    assert upload(state, b"publicado", request_id=lost_id).status_code == 500
    monkeypatch.setattr(ArtifactStore, "upload", original)
    recovered = client.get(path(agent, conversation) + "/" + str(lost_id)).json()
    assert recovered["status"] == "ready"
    assert upload(state, b"publicado", request_id=lost_id).json() == recovered
    assert requests == []


def test_preview_and_receipt_require_exact_conversation(state):
    client, _, agent, _, conversation, _, _ = state
    artifact = upload(state, b"<script>dados</script>").json()
    another = Conversation(agent_id=agent.id)
    with client.app.state.store.transaction() as unit:
        unit.conversations.create(another)
    for suffix in ("", "/preview"):
        response = client.get(path(agent, another) + "/" + artifact["id"] + suffix)
        assert response.status_code == 404 and "<script>" not in response.text
    preview = client.get(path(agent, conversation) + "/" + artifact["id"] + "/preview").json()
    assert preview["text"] == "<script>dados</script>" and preview["truncated"] is False


def test_upload_slots_refuse_before_body_and_disconnect_has_no_draft(state):
    client, _, agent, _, conversation, _, _ = state
    slots = client.app.state.artifact_upload_slots
    assert slots.acquire(False) and slots.acquire(False)
    try:
        status, body, reads = drive(
            client, path(agent, conversation, uuid4()), asgi_headers(client), []
        )
        assert status == 429 and body["error"]["code"] == "artifact_upload_busy" and reads == []
    finally:
        slots.release()
        slots.release()
    status, body, _ = drive(
        client,
        path(agent, conversation, uuid4()),
        asgi_headers(client),
        [{"type": "http.disconnect"}],
    )
    assert status == 400 and body["error"]["code"] == "upload_disconnected"
    assert client.get(path(agent, conversation)).json()["artifacts"] == []
    assert upload(state).status_code == 200


def test_task_artifact_revision_derives_binding_without_human_scope_fields(state):
    client, _, agent, _, _, requests, _ = state
    conversation = Conversation(agent_id=agent.id, metadata={"execution_kind": "text_task"})
    task = Task(
        agent_id=agent.id, conversation_id=conversation.id, title="Texto", objective="Texto"
    )
    with client.app.state.store.transaction() as unit:
        unit.conversations.create(conversation)
        unit.tasks.create(task)
    original = client.app.state.artifacts.create(
        agent.id, task.id, name="resultado.md", media_type="text/markdown", content=b"resultado"
    )
    response = client.put(
        path(agent, conversation, uuid4()),
        content=b"nova revisao",
        headers=upload_headers(
            client,
            b"nova revisao",
            previous_id=str(original.id),
            expected_revision=original.revision,
        ),
    )
    assert response.status_code == 200, response.text
    revised = response.json()
    assert revised["task_id"] == str(task.id) and revised["conversation_id"] == str(conversation.id)
    assert revised["run_id"] is None and revised["source"] == "user_revision"
    assert requests == []


@contextmanager
def loopback(client):
    """Servidor HTTP próprio sobre a aplicação/banco da fixture, sem outro lifespan."""
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(client.app, log_level="error", lifespan="off"))
        thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]})
        thread.start()
        try:
            deadline = time.monotonic() + 10
            while not server.started and thread.is_alive() and time.monotonic() < deadline:
                time.sleep(0.01)
            assert server.started
            with httpx.Client(
                base_url=f"http://127.0.0.1:{port}",
                trust_env=False,
                timeout=10,
                headers={"Host": "127.0.0.1:8000"},
                cookies=dict(client.cookies),
            ) as remote:
                yield remote
        finally:
            server.should_exit = True
            thread.join(10)
            assert not thread.is_alive()


def test_real_http_stream_exact_maximum_then_uuid_replay_without_provider(state):
    client, _, agent, _, conversation, requests, _ = state
    content = b"a" * (25 * 1024 * 1024)
    request_id = uuid4()
    values = upload_headers(client, content, media_type="text/plain")
    request_path = path(agent, conversation, request_id)
    with loopback(client) as remote:
        chunks = (content[index : index + 32768] for index in range(0, len(content), 32768))
        response = remote.put(request_path, content=chunks, headers=values)
        assert response.status_code == 200, response.text
        artifact = response.json()
        assert (
            artifact["size_bytes"] == len(content)
            and artifact["sha256"] == hashlib.sha256(content).hexdigest()
        )
        replay = remote.put(request_path, content=content, headers=values)
        assert replay.json() == artifact
        downloaded = remote.get(f"/api/v1/agents/{agent.id}/artifacts/{request_id}/download")
        assert downloaded.content == content
    assert requests == []


def test_real_http_authentication_is_answered_without_sending_declared_body(state):
    client, _, agent, _, conversation, _, _ = state
    with loopback(client) as remote:
        port = int(remote.base_url.port)
        with socket.create_connection(("127.0.0.1", port), timeout=5) as connection:
            request_path = path(agent, conversation, uuid4())
            # Cabeçalho enviado sozinho: se o middleware tentar ler o corpo antes
            # de autenticar, não há resposta até o prazo do servidor e o teste falha.
            connection.sendall(
                (
                    f"PUT {request_path} HTTP/1.1\r\nHost: 127.0.0.1:8000\r\n"
                    "Origin: http://127.0.0.1:8000\r\nContent-Type: application/octet-stream\r\n"
                    "Content-Length: 26214400\r\nConnection: close\r\n\r\n"
                ).encode()
            )
            response = connection.recv(4096)
            assert response.startswith(b"HTTP/1.1 401"), response
