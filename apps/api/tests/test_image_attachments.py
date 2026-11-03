"""Imagem exata no provedor e worker; OCR local, isolamento e negativos sem geração."""

import asyncio
import base64
import io
import json
import random
import shutil
import subprocess
from uuid import UUID

import httpx
import pytest
from PIL import Image, ImageDraw, ImageFont
from test_artifact_chat import records, send, submission
from test_artifact_upload import upload
from test_delegation_api import headers
from test_delegation_api import state as state  # noqa: F401

from bees_core.agent_tools.cycle import AgentTaskWorker


def image_bytes(format="PNG", *, text=None, noise=0):
    image = Image.new("RGB", (600, 100), "white")
    if noise:
        image = Image.frombytes(
            "RGB", (noise, noise), random.Random(221).randbytes(noise * noise * 3)
        )
    if text:
        ImageDraw.Draw(image).text((20, 20), text, fill="black", font=ImageFont.load_default(36))
    output = io.BytesIO()
    image.save(output, format=format)
    return output.getvalue()


def configure(state, vision, tools=False):
    client, _, agent, *_ = state
    with client.app.state.store.transaction() as unit:
        current = unit.agents.get(agent.id)
        updated = unit.agents.update(
            current.model_copy(
                update={
                    "provider_config": current.provider_config
                    | {"capabilities": {"text": True, "tool_calls": tools, "vision": vision}}
                }
            ),
            current.revision,
        )
    return updated.revision


@pytest.mark.parametrize("format,mime", [("PNG", "image/png"), ("JPEG", "image/jpeg")])
def test_image_reaches_provider_as_image_and_not_on_next_turn(state, format, mime):
    revision = configure(state, True)
    raw = image_bytes(format)
    result = upload(state, raw, name="print.png", media_type=mime)
    assert result.status_code == 200, result.text
    artifact = result.json()
    body = submission(state, artifact, attachment_consent={"expected_agent_revision": revision})
    response = send(state, body)
    assert response.status_code == 200, response.text
    payload = state[5][0]
    attached = [message for message in payload["messages"] if isinstance(message["content"], list)]
    assert len(attached) == 1 and attached[0]["role"] == "user"
    [text, image] = attached[0]["content"]
    assert "bees_attachment_data" in text["text"] and artifact["sha256"] in text["text"]
    assert image == {
        "type": "image_url",
        "image_url": {"url": f"data:{mime};base64,{base64.b64encode(raw).decode()}"},
    }
    assert send(state, body).status_code == 200 and len(state[5]) == 1
    assert records(state)[0]["attachments"][0]["sha256"] == artifact["sha256"]
    assert (
        send(
            state, submission(state, artifact, attachments=[], attachment_consent=None)
        ).status_code
        == 200
    )
    assert not any(isinstance(message["content"], list) for message in state[5][1]["messages"])


def test_image_survives_durable_worker_submission_and_replay(state):
    revision = configure(state, True, tools=True)
    raw = image_bytes(noise=96)
    assert len(raw) > 24000  # Imagem não deve consumir a janela de texto do histórico.
    artifact = upload(state, raw, name="print.png", media_type="image/png").json()
    client, _, agent, _, conversation, requests, transport = state
    body = submission(state, artifact, attachment_consent={"expected_agent_revision": revision})
    body.pop("conversation_id")
    url = f"/api/v1/agents/{agent.id}/conversations/{conversation.id}/agent-messages"
    response = client.post(url, json=body, headers=headers(client))
    assert response.status_code == 200, response.text
    assert client.post(url, json=body, headers=headers(client)).json() == response.json()

    def respond(request):
        requests.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {"role": "assistant", "content": "Imagem recebida"},
                        "finish_reason": "stop",
                    }
                ]
            },
        )

    worker = AgentTaskWorker(client.app.state.database, transport=httpx.MockTransport(respond))
    assert asyncio.run(worker.run_once())
    assert not asyncio.run(worker.run_once())
    assert len(requests) == 1
    image = next(
        message for message in requests[0]["messages"] if isinstance(message["content"], list)
    )
    assert base64.b64decode(image["content"][1]["image_url"]["url"].split(",", 1)[1]) == raw
    with client.app.state.store.transaction(write=False) as unit:
        task = unit.tasks.get(UUID(response.json()["task_id"]))
        assert task.status == "completed"


@pytest.mark.skipif(shutil.which("tesseract") is None, reason="OCR local opcional não instalado")
def test_text_model_receives_real_local_ocr_with_source_and_limits(state):
    raw = image_bytes(text="BEES OCR TEST 12345")
    artifact = upload(state, raw, name="print.png", media_type="image/png").json()
    response = send(state, submission(state, artifact))
    assert response.status_code == 200, response.text
    attached = next(
        message for message in state[5][0]["messages"] if "bees_attachment_data" in str(message)
    )
    assert attached["role"] == "user" and isinstance(attached["content"], str)
    assert "BEES OCR TEST 12345" in attached["content"]
    assert "OCR local" in attached["content"] and artifact["sha256"] in attached["content"]


def test_missing_ocr_and_invalid_image_never_reach_provider(state, monkeypatch):
    monkeypatch.setattr("bees_core.images.shutil.which", lambda _: None)
    artifact = upload(state, image_bytes(), name="print.png", media_type="image/png").json()
    response = send(state, submission(state, artifact))
    assert (
        response.status_code == 422 and response.json()["error"]["code"] == "image_ocr_unavailable"
    )
    assert upload(state, b"\x89PNG\r\n\x1a\ninvalid", media_type="image/png").status_code == 422
    assert upload(state, image_bytes("JPEG"), media_type="image/png").status_code == 422
    assert state[5] == [] and records(state) == []


def test_image_cannot_be_used_by_other_agent_or_with_stale_consent(state):
    revision = configure(state, True)
    client, _, agent, other, conversation, requests, _ = state
    artifact = upload(state, image_bytes(), media_type="image/png").json()
    assert send(state, submission(state, artifact)).status_code == 409
    body = submission(state, artifact, attachment_consent={"expected_agent_revision": revision})
    body.pop("conversation_id")
    response = client.post(
        f"/api/v1/agents/{other.id}/conversations/{conversation.id}/agent-messages",
        json=body,
        headers=headers(client),
    )
    assert response.status_code == 409 and requests == []


@pytest.mark.parametrize(
    "mode,code", [("empty", "image_ocr_empty"), ("timeout", "image_ocr_failed")]
)
def test_ocr_failure_is_explicit_and_does_not_submit_work(state, monkeypatch, mode, code):
    monkeypatch.setattr("bees_core.images.shutil.which", lambda _: "/fixture/tesseract")

    def run(arguments, **options):
        if mode == "timeout":
            raise subprocess.TimeoutExpired(arguments, options["timeout"])
        from pathlib import Path

        Path(arguments[2] + ".txt").write_text("", encoding="utf-8")

    monkeypatch.setattr("bees_core.images.subprocess.run", run)
    artifact = upload(state, image_bytes(), media_type="image/png").json()
    response = send(state, submission(state, artifact))
    assert response.status_code == 422 and response.json()["error"]["code"] == code
    assert state[5] == [] and records(state) == []


def test_image_byte_limit_is_aggregate_and_rejects_before_model(state):
    revision = configure(state, True)
    raw = image_bytes(noise=256)
    files = [
        upload(state, raw, name=f"print{index}.png", media_type="image/png").json()
        for index in range(3)
    ]
    assert sum(file["size_bytes"] for file in files) > 512 * 1024
    body = submission(state, files[0], attachment_consent={"expected_agent_revision": revision})
    body["attachments"] = [submission(state, file)["attachments"][0] for file in files]
    response = send(state, body)
    assert response.status_code == 422 and response.json()["error"]["code"] == "request_too_large"
    assert state[5] == [] and records(state) == []
