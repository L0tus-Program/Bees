"""Aceite Docker explícito com projeto/volumes próprios e servidor de protocolo local.

Executar com a .venv do repositório. Não configura a instalação do usuário,
não baixa modelos e não imprime bootstrap, senha, cookie ou chave.
"""

import argparse
import json
import os
import secrets
import subprocess
import time
from pathlib import Path
from uuid import uuid4

import httpx

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8081)
    parser.add_argument("--keep", action="store_true", help="Manter apenas o projeto de teste.")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("Porta inválida.")
    project = "bees-check-" + secrets.token_hex(4)
    environment = os.environ | {"BEES_HTTP_PORT": str(args.port)}
    command = [
        "docker",
        "compose",
        "--project-name",
        project,
        "--project-directory",
        str(ROOT),
        "-f",
        str(ROOT / "compose.yaml"),
        "-f",
        str(ROOT / "containers/testing/compose.yaml"),
    ]

    def compose(*arguments):
        result = subprocess.run(
            command + list(arguments),
            env=environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=300,
        )
        if result.returncode:
            raise RuntimeError("Comando Docker de teste falhou; nenhum segredo será exibido.")
        return result.stdout

    origin = f"http://localhost:{args.port}"
    password = "senha descartavel de container 12345"
    fake_key = "CHAVE-CONTROLADA-SEM-VALOR"
    try:
        compose("up", "--detach", "--wait", "--wait-timeout", "120")
        with httpx.Client(base_url=origin, trust_env=False, timeout=15) as client:
            assert client.get("/").status_code == 200
            state = client.get("/api/v1/state/status")
            assert state.json()["schema_version"] == 11
            assert client.get("/api/v1/onboarding").status_code == 401
            assert client.get("/api/v1/auth/status").json()["configured"] is False
            authorization = json.loads(
                compose("exec", "-T", "bees", "bees-auth", "bootstrap", "--format", "json")
            )
            setup = {
                "name": "Pessoa de teste Docker",
                "password": password,
                "bootstrap_token": authorization["bootstrap_token"],
            }
            assert (
                client.post(
                    "/api/v1/auth/setup", json=setup, headers={"Origin": origin}
                ).status_code
                == 200
            )
            assert (
                client.post(
                    "/api/v1/auth/setup", json=setup, headers={"Origin": origin}
                ).status_code
                == 409
            )

            def headers():
                return {
                    "Origin": origin,
                    "X-Bees-CSRF": client.get("/api/v1/auth/status").json()["csrf_token"],
                }

            catalog = client.get("/api/v1/providers")
            assert catalog.status_code == 200
            assert {item["id"] for item in catalog.json()["providers"]} == {
                "openai",
                "openrouter",
                "gemini",
                "ollama",
                "custom",
                "custom_ollama",
            }
            for preset in catalog.json()["providers"]:
                if preset["id"] in {"openai", "openrouter", "gemini", "ollama"}:
                    assert preset["models"]
            discovery = {
                "provider_id": "custom",
                "endpoint": "http://127.0.0.1:11434/v1",
                "api_key": fake_key,
            }
            assert (
                client.post(
                    "/api/v1/models/discover", json=discovery, headers={"Origin": origin}
                ).status_code
                == 403
            )
            listed = client.post("/api/v1/models/discover", json=discovery, headers=headers())
            assert listed.status_code == 200
            assert [item["id"] for item in listed.json()["models"]] == ["modelo-controlado"]
            assert fake_key not in listed.text
            assert "validation_token" not in listed.json()
            assert client.get("/api/v1/onboarding").json()["agents"] == []

            # Um catálogo negado não impede configuração nem geração no endpoint escolhido.
            denied_endpoint = "http://127.0.0.1:11434/catalog-denied"
            denied = client.post(
                "/api/v1/models/discover",
                json=discovery | {"endpoint": denied_endpoint},
                headers=headers(),
            )
            assert denied.status_code == 502
            assert denied.json()["error"]["code"] == "access_denied"
            assert denied.json()["error"]["upstream_status"] == 403
            manual = {
                "config": {
                    "kind": "openai_compatible",
                    "endpoint": denied_endpoint,
                    "model": "modelo-manual-nao-listado",
                    "capabilities": {"text": True, "tool_calls": False},
                },
                "api_key": fake_key,
            }
            assert (
                client.post(
                    "/api/v1/models/prepare", json=manual, headers={"Origin": origin}
                ).status_code
                == 403
            )
            prepared = client.post("/api/v1/models/prepare", json=manual, headers=headers())
            assert prepared.status_code == 200
            assert set(prepared.json()) == {"validation_token"}
            manual_created = client.post(
                "/api/v1/agents",
                json=manual
                | {
                    "validation_token": prepared.json()["validation_token"],
                    "name": "Abelha sem catálogo",
                },
                headers=headers(),
            )
            assert manual_created.status_code == 201
            manual_bee = manual_created.json()
            assert (
                client.post(
                    f"/api/v1/agents/{manual_bee['id']}/chat",
                    json={
                        "conversation_id": manual_bee["conversation_id"],
                        "content": "Conversa sem consultar catálogo.",
                    },
                    headers=headers(),
                ).status_code
                == 200
            )

            model = {
                "config": {
                    "kind": "openai_compatible",
                    "endpoint": "http://127.0.0.1:11434/v1",
                    "model": "modelo-controlado",
                    "capabilities": {"text": True, "tool_calls": False},
                },
                "api_key": fake_key,
            }
            assert (
                client.post(
                    "/api/v1/models/test", json=model, headers={"Origin": origin}
                ).status_code
                == 403
            )
            tested = client.post("/api/v1/models/test", json=model, headers=headers())
            assert tested.status_code == 200
            body = model | {
                "validation_token": tested.json()["validation_token"],
                "name": "Abelha Docker",
            }
            created = client.post("/api/v1/agents", json=body, headers=headers())
            assert created.status_code == 201
            bee = created.json()
            assert bee["provider_config"]["secret_ref"].startswith("vault:")
            reused = discovery | {
                "agent_id": bee["id"],
                "secret_ref": bee["provider_config"]["secret_ref"],
            }
            del reused["api_key"]
            listed = client.post("/api/v1/models/discover", json=reused, headers=headers())
            assert listed.status_code == 200
            refused = client.post(
                "/api/v1/models/discover",
                json=reused | {"provider_id": "openai", "endpoint": "https://api.openai.com/v1"},
                headers=headers(),
            )
            assert refused.status_code == 422
            agent_path = f"/api/v1/agents/{bee['id']}"
            manifest = client.get("/api/v1/plugins/catalog").json()["plugins"][0]
            install_body = {"manifest": manifest, "client_request_id": str(uuid4())}
            installed = client.post("/api/v1/plugins", json=install_body, headers=headers())
            assert installed.status_code == 201, installed.text
            plugin = installed.json()
            assert plugin["enabled"] is False
            assert (
                client.post("/api/v1/plugins", json=install_body, headers=headers()).json()["id"]
                == plugin["id"]
            )
            plugin_path = "/api/v1/plugins/" + plugin["id"]
            assert (
                client.patch(
                    plugin_path, json={"expected_revision": plugin["revision"], "enabled": True}
                ).status_code
                == 403
            )
            enabled = client.patch(
                plugin_path,
                json={"expected_revision": plugin["revision"], "enabled": True},
                headers=headers(),
            )
            assert enabled.status_code == 200
            plugin = enabled.json()
            grant = client.put(
                agent_path + f"/tools/{plugin['id']}/text.normalize",
                json={"expected_revision": 0, "enabled": True},
                headers=headers(),
            )
            assert grant.status_code == 200, grant.text
            assert client.get(agent_path + "/tools").json()["tools"][0]["available"] is True
            assert (
                client.get(f"/api/v1/agents/{manual_bee['id']}/tools").json()["tools"][0]["granted"]
                is False
            )
            memory = client.post(
                agent_path + "/memories",
                json={"scope": "user", "content": "Prefiro respostas curtas."},
                headers=headers(),
            )
            assert memory.status_code == 201
            sent = client.post(
                agent_path + "/chat",
                json={"conversation_id": bee["conversation_id"], "content": "Teste do container."},
                headers=headers(),
            )
            assert sent.status_code == 200
            before = client.get(
                agent_path + f"/messages?conversation_id={bee['conversation_id']}"
            ).json()
            # Fila não depende do frontend; comandos aceitos sobrevivem ao worker parado.
            compose("stop", "worker")
            task_body = {
                "client_request_id": str(uuid4()),
                "title": "Plano persistente",
                "objective": "Organize as ideias fornecidas em próximos passos.",
                "expected_result": "Lista curta de passos.",
            }
            task_response = client.post(agent_path + "/tasks", json=task_body, headers=headers())
            assert task_response.status_code == 201
            task = task_response.json()
            task_path = agent_path + "/tasks/" + task["id"]
            assert (
                client.post(agent_path + "/tasks", json=task_body, headers=headers()).json()["id"]
                == task["id"]
            )
            pause = {
                "client_request_id": str(uuid4()),
                "expected_revision": task["revision"],
                "action": "pause",
            }
            paused = client.post(task_path + "/control", json=pause, headers=headers())
            assert paused.status_code == 200 and paused.json()["status"] == "paused"
            resume = pause | {
                "client_request_id": str(uuid4()),
                "expected_revision": paused.json()["revision"],
                "action": "resume",
            }
            assert (
                client.post(task_path + "/control", json=resume, headers=headers()).status_code
                == 200
            )
            compose("up", "--detach", "--wait", "--wait-timeout", "120")
            deadline = time.monotonic() + 30
            while True:
                task_detail = client.get(task_path).json()
                if task_detail["task"]["status"] == "completed":
                    break
                assert time.monotonic() < deadline, "Executor não concluiu tarefa controlada."
                time.sleep(0.2)
            assert "Resposta controlada" in task_detail["task"]["latest_run"]["result"]["content"]
            assert client.get(agent_path + "/tasks").json()["worker"]["available"] is True
            assert (
                client.get(
                    agent_path + f"/messages?conversation_id={bee['conversation_id']}"
                ).json()
                == before
            )
            # Regras humanas persistem; retomada não contorna ask/deny nem executa por GET.
            policy_body = {
                "client_request_id": str(uuid4()),
                "name": "Controle de teste",
                "effect": "ask",
                "scope": {"tool_name": "model", "action": "generate"},
                "reason": "Validação descartável",
            }
            policy_response = client.post(
                agent_path + "/policies", json=policy_body, headers=headers()
            )
            assert policy_response.status_code == 201
            policy = policy_response.json()
            policy_path = agent_path + "/policies/" + policy["id"]
            assert (
                client.post(agent_path + "/policies", json=policy_body, headers=headers()).json()[
                    "id"
                ]
                == policy["id"]
            )
            assert (
                client.patch(
                    policy_path, json={"expected_revision": policy["revision"], "effect": "deny"}
                ).status_code
                == 403
            )
            waiting = client.post(
                agent_path + "/tasks",
                json=task_body
                | {"client_request_id": str(uuid4()), "title": "Tarefa sob política"},
                headers=headers(),
            ).json()
            waiting_path = agent_path + "/tasks/" + waiting["id"]

            def wait_task(path, expected_status):
                deadline = time.monotonic() + 30
                while True:
                    result = client.get(path).json()["task"]
                    if result["status"] == expected_status:
                        return result
                    assert time.monotonic() < deadline, "Política não refletida no worker."
                    time.sleep(0.2)

            waiting = wait_task(waiting_path, "waiting_approval")
            assert waiting["calls_started"] == 0 and "resume" in waiting["available_controls"]
            blocked_chat = client.post(
                agent_path + "/chat",
                json={"conversation_id": bee["conversation_id"], "content": "Não deve gerar."},
                headers=headers(),
            )
            assert blocked_chat.status_code == 409
            denied = client.patch(
                policy_path,
                json={"expected_revision": policy["revision"], "effect": "deny"},
                headers=headers(),
            )
            assert denied.status_code == 200
            policy = denied.json()
            assert (
                client.post(
                    waiting_path + "/control",
                    json={
                        "client_request_id": str(uuid4()),
                        "expected_revision": waiting["revision"],
                        "action": "resume",
                    },
                    headers=headers(),
                ).status_code
                == 200
            )
            paused = wait_task(waiting_path, "paused")
            assert (
                paused["calls_started"] == 0
                and paused["latest_run"]["error_code"] == "policy_denied"
            )
            revoked = client.patch(
                policy_path,
                json={"expected_revision": policy["revision"], "status": "revoked"},
                headers=headers(),
            )
            assert revoked.status_code == 200
            assert client.get(waiting_path).json()["task"]["status"] == "paused"
            assert (
                client.post(
                    waiting_path + "/control",
                    json={
                        "client_request_id": str(uuid4()),
                        "expected_revision": paused["revision"],
                        "action": "resume",
                    },
                    headers=headers(),
                ).status_code
                == 200
            )
            assert wait_task(waiting_path, "completed")["calls_started"] == 1
            assert (
                client.get("/api/v1/auth/status", headers={"Host": "visitante.example"}).status_code
                == 400
            )
            assert (
                client.post(
                    "/api/v1/models/test",
                    json=model,
                    headers=headers() | {"Origin": "http://visitante.example"},
                ).status_code
                == 403
            )
            compose("restart", "bees")
            compose("up", "--detach", "--wait", "--wait-timeout", "120")
            assert client.get("/api/v1/auth/status").json()["authenticated"] is True
            assert json.loads(
                compose("exec", "-T", "bees", "bees-auth", "bootstrap", "--format", "json")
            ) == {"configured": True}
            compose("up", "--detach", "--force-recreate", "--wait", "--wait-timeout", "120")
            stored = next(
                item
                for item in client.get("/api/v1/onboarding").json()["agents"]
                if item["id"] == bee["id"]
            )
            assert stored["id"] == bee["id"] and stored["provider_config"] == bee["provider_config"]
            assert (
                client.get(
                    agent_path + f"/messages?conversation_id={bee['conversation_id']}"
                ).json()
                == before
            )
            assert len(client.get(agent_path + "/memories").json()["memories"]) == 1
            assert client.get(agent_path + "/policies").json()["policies"] == [revoked.json()]
            assert client.get(waiting_path).json()["task"]["status"] == "completed"
            assert (
                client.get(task_path).json()["task"]["latest_run"]["result"]
                == task_detail["task"]["latest_run"]["result"]
            )
            # Pedido de computador persiste sem afirmar VM disponível ou executar no host.
            environment_catalog = client.get("/api/v1/environments/catalog").json()
            assert environment_catalog["host"]["provisionable"] is False
            assert environment_catalog["templates"][0]["status"] == "planned"
            environment_collection = agent_path + "/environments"
            environment_body = {
                "name": "Computador descartável",
                "client_request_id": str(uuid4()),
            }
            assert client.post(environment_collection, json=environment_body).status_code == 403
            planned = client.post(environment_collection, json=environment_body, headers=headers())
            assert planned.status_code == 201, planned.text
            environment_plan = planned.json()
            assert environment_plan["status"] == "awaiting_host"
            assert environment_plan["operation_status"] == "awaiting_host"
            assert environment_plan["usable"] is False
            assert (
                client.post(environment_collection, json=environment_body, headers=headers()).json()
                == environment_plan
            )
            environment_path = environment_collection + "/" + environment_plan["id"]
            assert (
                client.get(
                    f"/api/v1/agents/{manual_bee['id']}/environments/{environment_plan['id']}"
                ).status_code
                == 404
            )
            # Decisão humana durável, pontual e reutilização explícita de regra.
            asked = client.post(
                agent_path + "/policies",
                json=policy_body | {"client_request_id": str(uuid4())},
                headers=headers(),
            )
            assert asked.status_code == 201

            def new_waiting_task(title):
                created = client.post(
                    agent_path + "/tasks",
                    json=task_body | {"client_request_id": str(uuid4()), "title": title},
                    headers=headers(),
                )
                assert created.status_code == 201
                path = agent_path + "/tasks/" + created.json()["id"]
                wait_task(path, "waiting_approval")
                collection = client.get(
                    agent_path + "/approvals", params={"task_id": created.json()["id"]}
                ).json()
                pending = collection["approvals"][0]
                assert pending["valid"] and pending["parameters"] == {
                    "model": bee["provider_config"]["model"]
                }
                assert "prepared" not in pending and "secret_ref" not in str(pending)
                return path, pending

            once_path, once = new_waiting_task("Aprovação única Docker")
            # Reinício API/worker não elimina o pedido humano nem cria efeito.
            # Worker e fixture compartilham a rede da API; após reiniciar a
            # proprietária, precisam ingressar novamente na nova namespace.
            compose("restart", "bees")
            compose("restart", "worker", "fixture")
            compose("up", "--detach", "--wait", "--wait-timeout", "120")
            approve_path = agent_path + "/approvals/" + once["id"]
            assert client.get(approve_path).json()["status"] == "pending"
            once_body = {
                "client_request_id": str(uuid4()),
                "expected_revision": once["revision"],
                "decision": "allow_once",
            }
            assert client.post(approve_path + "/decision", json=once_body).status_code == 403
            approved = client.post(approve_path + "/decision", json=once_body, headers=headers())
            assert approved.status_code == 200, approved.text
            assert wait_task(once_path, "completed")["calls_started"] == 1
            replay = client.post(approve_path + "/decision", json=once_body, headers=headers())
            assert replay.status_code == 200 and replay.json()["consumed_at"]
            rule_task_path, question = new_waiting_task("Regra de escopo Docker")
            rule_approval_path = agent_path + "/approvals/" + question["id"]
            saved = client.post(
                rule_approval_path + "/decision",
                json={
                    "client_request_id": str(uuid4()),
                    "expected_revision": question["revision"],
                    "decision": "allow_rule",
                    "reason": "Escopo controlado de teste",
                },
                headers=headers(),
            )
            assert saved.status_code == 200, saved.text
            assert wait_task(rule_task_path, "completed")["calls_started"] == 1
            next_task = client.post(
                agent_path + "/tasks",
                json=task_body | {"client_request_id": str(uuid4()), "title": "Regra reutilizada"},
                headers=headers(),
            )
            assert next_task.status_code == 201
            assert (
                wait_task(agent_path + "/tasks/" + next_task.json()["id"], "completed")[
                    "calls_started"
                ]
                == 1
            )
            compose("up", "--detach", "--force-recreate", "--wait", "--wait-timeout", "120")
            assert client.get(approve_path).json()["consumed_at"]
            assert client.get(environment_path).json() == environment_plan
            cancel_environment = {
                "client_request_id": str(uuid4()),
                "expected_revision": environment_plan["revision"],
            }
            cancelled_environment = client.post(
                environment_path + "/cancel", json=cancel_environment, headers=headers()
            )
            assert cancelled_environment.status_code == 200
            assert cancelled_environment.json()["status"] == "cancelled"
            assert (
                client.post(
                    environment_path + "/cancel", json=cancel_environment, headers=headers()
                ).json()
                == cancelled_environment.json()
            )
            assert (
                client.post(
                    environment_path + "/cancel",
                    json=cancel_environment | {"client_request_id": str(uuid4())},
                    headers=headers(),
                ).status_code
                == 409
            )
            assert client.get("/api/v1/plugins").json()["plugins"] == [plugin]
            tools = client.get(agent_path + "/tools").json()["tools"]
            assert (
                tools[0]["available"] is True
                and tools[0]["grant_revision"] == grant.json()["revision"]
            )
            disabled = client.patch(
                plugin_path,
                json={"expected_revision": plugin["revision"], "enabled": False},
                headers=headers(),
            )
            assert disabled.status_code == 200
            assert client.get(agent_path + "/tools").json()["tools"][0]["available"] is False
            assert client.get(rule_approval_path).json()["decision"] == "allow_rule"
            assert any(
                value["effect"] == "allow" and value["origin"] == "approval"
                for value in client.get(agent_path + "/policies").json()["policies"]
            )
            continued = client.post(
                agent_path + "/chat",
                json={"conversation_id": bee["conversation_id"], "content": "Após recriar."},
                headers=headers(),
            )
            assert continued.status_code == 200
            logs = compose("logs", "--no-color", "bees", "worker")
            assert (
                fake_key not in logs
                and password not in logs
                and authorization["bootstrap_token"] not in logs
            )
        print(
            "Docker: catálogo opcional, modelo manual, primeiro acesso, cofre, conversa, "
            "tarefas em processo separado, políticas, ferramentas locais, pedidos de computador, "
            "fronteiras e persistência aprovados."
        )
        if args.keep:
            print(f"Projeto de teste mantido: {project}; porta {args.port}.")
    finally:
        if not args.keep:
            # Nome aleatório criado por esta execução; nunca remover o projeto bees do usuário.
            compose("down", "--volumes")


if __name__ == "__main__":
    main()
