"""Causas disponíveis sem repetir gerações/efeitos ou publicar conteúdo privado."""

import asyncio
from uuid import UUID

import httpx
import pytest
from test_task_actions_api import PRIVATE, paused_task
from test_tasks import body, headers
from test_tasks import state as state  # noqa: F401

from bees_core.execution import TaskWorker
from bees_core.models import Action
from bees_core.providers.contracts import ProviderConfig


@pytest.mark.parametrize(
    "payload,issue",
    [
        ({"choices": []}, "choices"),
        (
            {
                "choices": [
                    {"message": {"role": "assistant", "content": None}, "finish_reason": "stop"}
                ]
            },
            "content",
        ),
        (
            {
                "choices": [
                    {
                        "message": {"role": "assistant", "content": "PRIVATE"},
                        "finish_reason": "invented",
                    }
                ]
            },
            "finish_reason",
        ),
    ],
)
def test_bad_model_response_preserves_reason_and_unknown_without_retry(state, payload, issue):
    client, agent, *_ = state
    path = f"/api/v1/agents/{agent.id}/tasks"
    task = client.post(path, json=body(), headers=headers(client)).json()
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(200, json=payload)

    worker = TaskWorker(client.app.state.database, transport=httpx.MockTransport(respond))
    assert asyncio.run(worker.run_once()) and not asyncio.run(worker.run_once())
    response = client.get(path + "/" + task["id"])
    detail = response.json()["task"]
    assert detail["status"] == "paused" and detail["unknown_model_calls"] == 1
    assert detail["latest_run"]["error_code"] == "outcome_unknown"
    assert detail["latest_run"]["failure"]["code"] == "invalid_response"
    assert detail["latest_run"]["failure"]["response_issue"] == issue
    assert "PRIVATE" not in response.text and len(requests) == 1
    assert (
        client.get(path).json()["tasks"][0]["latest_run"]["failure"]
        == detail["latest_run"]["failure"]
    )


def test_success_http_error_envelope_retains_safe_message_and_unknown(state):
    client, agent, *_ = state
    path = f"/api/v1/agents/{agent.id}/tasks"
    task = client.post(path, json=body(), headers=headers(client)).json()
    worker = TaskWorker(
        client.app.state.database,
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                json={
                    "error": {
                        "message": "Model does not support tools. Bearer SECRET https://private.invalid/token"
                    }
                },
            )
        ),
    )
    assert asyncio.run(worker.run_once()) and not asyncio.run(worker.run_once())
    detail = client.get(path + "/" + task["id"]).json()["task"]
    failure = detail["latest_run"]["failure"]
    assert detail["unknown_model_calls"] == 1
    assert failure["response_issue"] == "error_envelope" and failure["http_status"] is None
    assert "Model does not support tools." in failure["provider_message"]
    assert "SECRET" not in str(failure) and "private.invalid" not in str(failure)


def test_local_model_preflight_keeps_reason_without_creating_unknown_generation(state):
    client, agent, *_ = state
    with client.app.state.store.transaction() as unit:
        current = unit.agents.get(agent.id)
        config = ProviderConfig(
            kind="ollama", endpoint="http://127.0.0.1:11434", model="test-model", capabilities={}
        )
        unit.agents.update(
            current.model_copy(update={"provider_config": config.model_dump(mode="json")}),
            current.revision,
        )
    path = f"/api/v1/agents/{agent.id}/tasks"
    task = client.post(path, json=body(), headers=headers(client)).json()
    seen = []

    def respond(request):
        seen.append(request.url.path)
        return httpx.Response(
            200, content=b"not json PRIVATE", headers={"content-type": "application/json"}
        )

    worker = TaskWorker(client.app.state.database, transport=httpx.MockTransport(respond))
    assert asyncio.run(worker.run_once()) and not asyncio.run(worker.run_once())
    detail = client.get(path + "/" + task["id"]).json()["task"]
    assert detail["latest_run"]["failure"]["response_issue"] == "invalid_json"
    assert detail["unknown_model_calls"] == 0 and detail["calls_started"] == 0
    assert seen == ["/api/tags"] and "PRIVATE" not in str(detail)


def test_tool_cause_reaches_list_and_details_without_private_results(state):
    client, agent, *_ = state
    path, task = paused_task(client, agent)
    with client.app.state.store.transaction() as unit:
        unit.actions.create(
            Action(
                run_id=UUID(task["active_run_id"]),
                tool_name="text.normalize",
                status="outcome_unknown",
                parameters={"input_hash": PRIVATE},
                metadata={"snapshot": PRIVATE, "error_code": "executor_timeout"},
            )
        )
        run = unit.runs.get(UUID(task["active_run_id"]))
        unit.runs.update(run.model_copy(update={"error": "tool_outcome_unknown"}), run.revision)
    response = client.get(path)
    detail = response.json()
    assert PRIVATE not in response.text
    assert detail["actions"][0]["error_code"] == "executor_timeout"
    assert detail["task"]["latest_run"]["failure"] == {
        "source": "tool",
        "tool_name": "text.normalize",
        "code": "executor_timeout",
        "http_status": None,
        "parameter": None,
        "provider_message": None,
    }
    listed = client.get(f"/api/v1/agents/{agent.id}/tasks").json()["tasks"][0]
    assert listed["latest_run"]["failure"] == detail["task"]["latest_run"]["failure"]
    assert listed["unknown_tool_actions"] == 1


def test_arbitrary_journal_code_is_not_exposed_as_diagnostic(state):
    client, agent, *_ = state
    path, task = paused_task(client, agent)
    with client.app.state.store.transaction() as unit:
        unit.actions.create(
            Action(
                run_id=UUID(task["active_run_id"]),
                tool_name="text.normalize",
                status="outcome_unknown",
                parameters={"input_hash": PRIVATE},
                metadata={"snapshot": PRIVATE, "error_code": "private_token_value"},
            )
        )
        run = unit.runs.get(UUID(task["active_run_id"]))
        unit.runs.update(run.model_copy(update={"error": "tool_outcome_unknown"}), run.revision)
    response = client.get(path)
    assert "private_token_value" not in response.text and PRIVATE not in response.text
    assert response.json()["task"]["latest_run"]["failure"] is None


def test_malformed_restored_reason_is_ignored_without_breaking_task_polling(state):
    client, agent, *_ = state
    path, task = paused_task(client, agent)
    with client.app.state.store.transaction() as unit:
        run = unit.runs.get(UUID(task["active_run_id"]))
        unit.runs.update(
            run.model_copy(
                update={
                    "error": "outcome_unknown",
                    "checkpoint": run.checkpoint
                    | {
                        "model_failure": {"code": "invalid_response", "response_issue": ["PRIVATE"]}
                    },
                }
            ),
            run.revision,
        )
    response = client.get(path)
    assert response.status_code == 200 and "PRIVATE" not in response.text
    assert "response_issue" not in response.json()["task"]["latest_run"]["failure"]


def test_unknown_model_cause_is_not_replaced_by_an_older_tool_failure(state):
    client, agent, *_ = state
    path = f"/api/v1/agents/{agent.id}/tasks"
    task = client.post(path, json=body(), headers=headers(client)).json()
    with client.app.state.store.transaction() as unit:
        unit.actions.create(
            Action(
                run_id=UUID(task["active_run_id"]),
                tool_name="text.normalize",
                status="failed_no_effect",
                metadata={"error_code": "executor_timeout"},
            )
        )

    def respond(_):
        raise RuntimeError("PRIVATE exception")

    worker = TaskWorker(client.app.state.database, transport=httpx.MockTransport(respond))
    assert asyncio.run(worker.run_once()) and not asyncio.run(worker.run_once())
    response = client.get(path + "/" + task["id"])
    assert response.json()["task"]["unknown_model_calls"] == 1
    assert response.json()["task"]["latest_run"]["failure"] is None
    assert "PRIVATE" not in response.text
