"""Conversão limitada à sessão humana e ao resultado exato da abelha."""

import hashlib
from uuid import uuid4

from fastapi.testclient import TestClient
from test_global_stop_api import ORIGIN, headers
from test_global_stop_api import state as state
from test_skills_api import definition

from bees_core.models import Conversation, Message, Run, Task
from bees_core.skills import CreateProcessSkill
from bees_core.storage.store import StateStore


def seed_process(database, agent_id):
    with StateStore(database).transaction() as unit:
        conversation = unit.conversations.create(Conversation(agent_id=agent_id))
        task = unit.tasks.create(
            Task(
                agent_id=agent_id,
                conversation_id=conversation.id,
                title="Processo",
                objective="Conferir",
                status="completed",
            )
        )
        message = unit.messages.create(
            Message(
                conversation_id=conversation.id,
                role="assistant",
                content="Fonte https://example.org/manual; pessoa de teste.",
            )
        )
        run = unit.runs.create(
            Run(
                task_id=task.id,
                status="completed",
                checkpoint={"result_message_id": str(message.id)},
            )
        )
    return CreateProcessSkill(
        client_request_id=uuid4(),
        definition=definition(),
        task_id=task.id,
        run_id=run.id,
        message_id=message.id,
        result_sha256=hashlib.sha256(message.content.encode("utf-8")).hexdigest(),
        review={
            "sources": ["https://example.org/manual"],
            "review_note": "Conferido",
            "pending": "Reavaliar recursos antes de executar",
            "confirmed": True,
        },
    )


def test_process_draft_auth_closed_contract_and_reload_origin(state):
    client, agent, _ = state
    request = seed_process(client.app.state.database, agent.id)
    body = request.model_dump(mode="json")
    path = f"/api/v1/agents/{agent.id}/skills"
    anonymous = TestClient(client.app, base_url=ORIGIN)
    assert anonymous.post(
        path + "/from-process", json=body, headers={"Origin": ORIGIN}
    ).status_code in (401, 403)
    assert client.post(path + "/from-process", json=body).status_code in (401, 403)
    assert (
        client.post(
            path + "/from-process",
            json=body,
            headers=headers(client) | {"Origin": "https://wrong.example"},
        ).status_code
        == 403
    )
    for altered in (
        body | {"enabled": True},
        body | {"agent_id": str(uuid4())},
        body | {"review": body["review"] | {"confirmed": False}},
        body | {"definition": body["definition"] | {"grants": ["browser.read"]}},
    ):
        assert (
            client.post(path + "/from-process", json=altered, headers=headers(client)).status_code
            == 422
        )
    response = client.post(path + "/from-process", json=body, headers=headers(client))
    assert response.status_code == 200
    saved = response.json()
    assert saved["enabled_version"] is None
    assert client.post(path + "/from-process", json=body, headers=headers(client)).json() == saved
    assert client.get(path + "/" + saved["id"]).json() == saved
    origin_path = path + f"/{saved['id']}/revisions/1/process"
    assert anonymous.get(origin_path).status_code == 401
    origin = client.get(origin_path).json()
    assert origin["message_id"] == str(request.message_id)
    assert origin["review"] == body["review"]
    assert origin["actor"] == "user"
    assert "pessoa de teste" not in response.text
    assert client.get(origin_path.replace(str(agent.id), str(uuid4()))).status_code == 404
    modified = body | {"definition": body["definition"] | {"name": "Alteração"}}
    assert (
        client.post(path + "/from-process", json=modified, headers=headers(client)).status_code
        == 409
    )
    bad_hash = body | {"client_request_id": str(uuid4()), "result_sha256": "0" * 64}
    assert (
        client.post(path + "/from-process", json=bad_hash, headers=headers(client)).status_code
        == 409
    )
    assert len(client.get(path).json()) == 1


def test_known_secret_rejected_without_echo_or_partial_record(state):
    client, agent, _ = state
    request = seed_process(client.app.state.database, agent.id)
    body = request.model_dump(mode="json")
    secret = "bx_" + "A" * 43
    body["definition"]["purpose"] = secret
    path = f"/api/v1/agents/{agent.id}/skills"
    response = client.post(path + "/from-process", json=body, headers=headers(client))
    assert response.status_code == 422
    assert secret not in response.text
    assert client.get(path).json() == []
