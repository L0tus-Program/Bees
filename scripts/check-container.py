"""Aceite Docker explícito com projeto/volumes próprios e servidor de protocolo local.

Executar com a .venv do repositório. Não configura a instalação do usuário,
não baixa modelos e não imprime bootstrap, senha, cookie ou chave.
"""

import argparse
import json
import os
import secrets
import subprocess
from pathlib import Path

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
            assert state.json()["schema_version"] == 2
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
            agent_path = f"/api/v1/agents/{bee['id']}"
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
            stored = client.get("/api/v1/onboarding").json()["agents"][0]
            assert stored["id"] == bee["id"] and stored["provider_config"] == bee["provider_config"]
            assert (
                client.get(
                    agent_path + f"/messages?conversation_id={bee['conversation_id']}"
                ).json()
                == before
            )
            assert len(client.get(agent_path + "/memories").json()["memories"]) == 1
            continued = client.post(
                agent_path + "/chat",
                json={"conversation_id": bee["conversation_id"], "content": "Após recriar."},
                headers=headers(),
            )
            assert continued.status_code == 200
            logs = compose("logs", "--no-color", "bees")
            assert (
                fake_key not in logs
                and password not in logs
                and authorization["bootstrap_token"] not in logs
            )
        print("Docker: primeiro acesso, cofre, conversa, fronteiras e persistência aprovados.")
        if args.keep:
            print(f"Projeto de teste mantido: {project}; porta {args.port}.")
    finally:
        if not args.keep:
            # Nome aleatório criado por esta execução; nunca remover o projeto bees do usuário.
            compose("down", "--volumes")


if __name__ == "__main__":
    main()
