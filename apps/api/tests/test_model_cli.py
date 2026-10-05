import asyncio
import io
import json
from pathlib import Path

import httpx
import pytest
from cryptography.fernet import Fernet
from pydantic import SecretStr

from bees_api import model_cli
from bees_core.providers.service import ProviderService
from bees_core.providers.vault import FileSecretVault
from bees_core.storage.database import Database
from bees_core.storage.store import StateStore


def config_file(tmp_path: Path, **overrides) -> Path:
    config = {
        "kind": "openai_compatible",
        "endpoint": "https://provider.example/v1",
        "model": "test-model",
        "secret_ref": "env:BEES_TEST_KEY",
        "capabilities": {"text": True, "tool_calls": False},
    }
    config.update(overrides)
    path = tmp_path / "model.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    return path


def run(args: list[str], capsys) -> tuple[int, dict]:
    status = asyncio.run(model_cli.run_cli(args))
    output = capsys.readouterr()
    assert not output.err
    return status, json.loads(output.out)


def test_create_switch_and_chat_preserve_conversation(tmp_path, monkeypatch, capsys):
    config = config_file(tmp_path)
    data = tmp_path / "private"
    status, created = run(
        ["create", "--name", "Pesquisadora", "--config", str(config), "--data-dir", str(data)],
        capsys,
    )
    assert status == 0
    assert created["revision"] == 1
    database = Database(data / "bees.sqlite3")
    with StateStore(database).transaction(write=False) as unit:
        original = unit.agents.get(created["agent_id"])
        assert original.name == "Pesquisadora"
        assert len(unit.conversations.list()) == 1

    config_file(tmp_path, model="other-model")
    status, changed = run(
        [
            "configure",
            "--agent",
            created["agent_id"],
            "--expected-revision",
            "1",
            "--config",
            str(config),
            "--data-dir",
            str(data),
        ],
        capsys,
    )
    assert status == 0
    assert changed == {"agent_id": created["agent_id"], "revision": 2}
    requests = []
    monkeypatch.setenv("BEES_TEST_KEY", "private-test-secret")

    def handler(request):
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "model": "other-model",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "Olá!"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7},
            },
        )

    monkeypatch.setattr(
        model_cli,
        "ProviderService",
        lambda database, resolver: ProviderService(
            database, resolver, transport=httpx.MockTransport(handler)
        ),
    )
    status, response = run(
        [
            "chat",
            "--agent",
            created["agent_id"],
            "--conversation",
            created["conversation_id"],
            "--text",
            "Oi",
            "--data-dir",
            str(data),
        ],
        capsys,
    )
    assert status == 0
    assert response["message"]["content"] == "Olá!"
    assert len(requests) == 1
    assert requests[0].headers["authorization"] == "Bearer private-test-secret"
    assert b"private-test-secret" not in requests[0].content
    assert "private-test-secret" not in json.dumps(response)
    with StateStore(database).transaction(write=False) as unit:
        messages = unit.messages.list(conversation_id=created["conversation_id"])
        assert [message.content for message in messages] == ["Oi", "Olá!"]
        assert unit.conversations.get(created["conversation_id"]).agent_id == original.id


@pytest.mark.parametrize(
    "overrides",
    [
        {"api_key": "do-not-disclose"},
        {"endpoint": "https://user:do-not-disclose@provider.example/v1"},
        {"secret_ref": "do-not-disclose"},
    ],
)
def test_config_error_never_echoes_secret(tmp_path, capsys, overrides):
    config = config_file(tmp_path, **overrides)
    data = tmp_path / "private"
    status, output = run(
        ["create", "--name", "Abelha", "--config", str(config), "--data-dir", str(data)],
        capsys,
    )
    assert status == 1
    assert output["code"] == "invalid_configuration"
    assert "do-not-disclose" not in json.dumps(output)
    database = Database(data / "bees.sqlite3")
    with StateStore(database).transaction(write=False) as unit:
        assert unit.agents.list() == []


def test_missing_config_is_safe_error(tmp_path, capsys):
    status, output = run(["diagnose", "--config", str(tmp_path / "absent.json")], capsys)
    assert status == 1
    assert output["code"] == "invalid_configuration"


def test_diagnose_exit_status_and_safe_auth_error(tmp_path, capsys, monkeypatch):
    config = config_file(tmp_path)
    monkeypatch.setenv("BEES_TEST_KEY", "private-test-secret")
    factory = model_cli.create_adapter
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, json={"data": [{"id": "test-model"}]})
    )
    monkeypatch.setattr(
        model_cli,
        "create_adapter",
        lambda kind, resolver: factory(kind, resolver, transport=transport),
    )
    status, result = run(["diagnose", "--config", str(config)], capsys)
    assert status == 0
    assert result["status"] == "ok"
    transport = httpx.MockTransport(
        lambda request: httpx.Response(401, text="private-test-secret must not appear")
    )
    status, result = run(["diagnose", "--config", str(config)], capsys)
    assert status == 1
    assert result["code"] == "authentication_failed"
    assert "private-test-secret" not in json.dumps(result)


def test_empty_stdin_is_safe_error(tmp_path, capsys, monkeypatch):
    config = config_file(tmp_path)
    status, created = run(
        ["create", "--name", "Abelha", "--config", str(config), "--data-dir", str(tmp_path)],
        capsys,
    )
    assert status == 0
    monkeypatch.setattr("sys.stdin", io.StringIO(" \n"))
    status, output = run(
        [
            "chat",
            "--agent",
            created["agent_id"],
            "--conversation",
            created["conversation_id"],
            "--data-dir",
            str(tmp_path),
        ],
        capsys,
    )
    assert status == 1
    assert output["code"] == "invalid_configuration"


def provision_vault(data: Path, monkeypatch) -> tuple[FileSecretVault, str]:
    key = Fernet.generate_key().decode("ascii")
    monkeypatch.setenv("BEES_VAULT_KEY", key)
    vault = FileSecretVault(data / "vault", key=SecretStr(key))
    return vault, vault.put(SecretStr("vault-private-test-secret"))


def test_switch_to_vault_and_chat_after_reopening(tmp_path, monkeypatch, capsys):
    data = tmp_path / "private"
    vault, reference = provision_vault(data, monkeypatch)
    config = config_file(tmp_path)
    status, created = run(
        ["create", "--name", "Abelha", "--config", str(config), "--data-dir", str(data)],
        capsys,
    )
    assert status == 0
    config_file(tmp_path, secret_ref=reference)
    status, changed = run(
        [
            "configure",
            "--agent",
            created["agent_id"],
            "--expected-revision",
            "1",
            "--config",
            str(config),
            "--data-dir",
            str(data),
        ],
        capsys,
    )
    assert status == 0
    assert changed == {"agent_id": created["agent_id"], "revision": 2}
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {"role": "assistant", "content": "Resposta"},
                        "finish_reason": "stop",
                    }
                ]
            },
        )

    monkeypatch.setattr(
        model_cli,
        "ProviderService",
        lambda database, resolver: ProviderService(
            database, resolver, transport=httpx.MockTransport(handler)
        ),
    )
    status, response = run(
        [
            "chat",
            "--agent",
            created["agent_id"],
            "--conversation",
            created["conversation_id"],
            "--text",
            "Pergunta",
            "--data-dir",
            str(data),
        ],
        capsys,
    )
    assert status == 0
    assert response["message"]["content"] == "Resposta"
    assert len(requests) == 1
    assert requests[0].headers["authorization"] == "Bearer vault-private-test-secret"
    assert b"vault-private-test-secret" not in requests[0].content
    assert "vault-private-test-secret" not in json.dumps(response)
    database = Database(data / "bees.sqlite3")
    with StateStore(database).transaction(write=False) as unit:
        agent = unit.agents.get(created["agent_id"])
        assert agent.provider_config["secret_ref"] == reference
        assert "vault-private-test-secret" not in agent.model_dump_json()
        assert [
            item.content for item in unit.messages.list(conversation_id=created["conversation_id"])
        ] == ["Pergunta", "Resposta"]
    assert vault.resolve(reference).get_secret_value() == "vault-private-test-secret"
    assert b"vault-private-test-secret" not in (data / "bees.sqlite3").read_bytes()


@pytest.mark.parametrize("data_dir_source", ["argument", "environment"])
def test_diagnose_resolves_vault_without_creating_database(
    tmp_path, monkeypatch, capsys, data_dir_source
):
    data = tmp_path / "private"
    _, reference = provision_vault(data, monkeypatch)
    config = config_file(tmp_path, secret_ref=reference)
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"data": [{"id": "test-model"}]})

    factory = model_cli.create_adapter
    monkeypatch.setattr(
        model_cli,
        "create_adapter",
        lambda kind, resolver: factory(kind, resolver, transport=httpx.MockTransport(handler)),
    )
    args = ["diagnose", "--config", str(config)]
    if data_dir_source == "argument":
        args.extend(["--data-dir", str(data)])
    else:
        monkeypatch.setenv("BEES_DATA_DIR", str(data))
    status, result = run(args, capsys)
    assert status == 0
    assert result["status"] == "ok"
    assert len(requests) == 1
    assert requests[0].headers["authorization"] == "Bearer vault-private-test-secret"
    assert "vault-private-test-secret" not in json.dumps(result)
    assert not (data / "bees.sqlite3").exists()


def test_missing_vault_secret_refuses_chat_without_fallback(tmp_path, monkeypatch, capsys):
    from uuid import uuid4

    data = tmp_path / "private"
    monkeypatch.setenv("BEES_TEST_KEY", "fallback-private-test-secret")
    config = config_file(tmp_path, secret_ref=f"vault:{uuid4()}")
    status, created = run(
        ["create", "--name", "Abelha", "--config", str(config), "--data-dir", str(data)],
        capsys,
    )
    assert status == 0
    requests = []

    def handler(request):
        requests.append(request)
        pytest.fail("Credencial ausente não pode gerar requisição de rede.")

    monkeypatch.setattr(
        model_cli,
        "ProviderService",
        lambda database, resolver: ProviderService(
            database, resolver, transport=httpx.MockTransport(handler)
        ),
    )
    status, result = run(
        [
            "chat",
            "--agent",
            created["agent_id"],
            "--conversation",
            created["conversation_id"],
            "--text",
            "Pergunta",
            "--data-dir",
            str(data),
        ],
        capsys,
    )
    assert status == 1
    assert result["code"] == "secret_unavailable"
    assert "fallback-private-test-secret" not in json.dumps(result)
    assert not requests
    with StateStore(Database(data / "bees.sqlite3")).transaction(write=False) as unit:
        assert unit.messages.list(conversation_id=created["conversation_id"]) == []
    assert not (data / "vault").exists()
