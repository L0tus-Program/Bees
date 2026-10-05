"""Operação explícita de modelos persistentes pelo terminal."""

import argparse
import asyncio
import json
import os
import sys
from collections.abc import Sequence
from pathlib import Path
from uuid import UUID

from pydantic import SecretStr, ValidationError

from bees_api.config import default_data_dir
from bees_core.models import Agent, Conversation
from bees_core.providers.base import create_adapter
from bees_core.providers.contracts import ProviderConfig
from bees_core.providers.errors import ProviderError
from bees_core.providers.secrets import build_secret_resolver
from bees_core.providers.service import ProviderService
from bees_core.providers.vault import FileSecretVault
from bees_core.storage.database import Database
from bees_core.storage.store import StateStore, StoreError


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Configura e conversa com modelos no Bees.")
    commands = parser.add_subparsers(dest="command", required=True)
    diagnose = commands.add_parser("diagnose", help="Consulta disponibilidade, sem gerar conversa.")
    diagnose.add_argument("--config", type=Path, required=True)
    create = commands.add_parser("create", help="Cria uma abelha e sua primeira conversa.")
    create.add_argument("--config", type=Path, required=True)
    create.add_argument("--name", required=True)
    create.add_argument("--instructions", default="")
    configure = commands.add_parser("configure", help="Troca a configuração sem apagar estado.")
    configure.add_argument("--config", type=Path, required=True)
    configure.add_argument("--agent", type=UUID, required=True)
    configure.add_argument("--expected-revision", type=int, required=True)
    chat = commands.add_parser("chat", help="Envia texto ao modelo configurado explicitamente.")
    chat.add_argument("--agent", type=UUID, required=True)
    chat.add_argument("--conversation", type=UUID, required=True)
    chat.add_argument("--text", help="Sem este argumento, lê o texto da entrada padrão.")
    for command in (diagnose, create, configure, chat):
        command.add_argument(
            "--data-dir",
            type=Path,
            default=os.environ.get("BEES_DATA_DIR", str(default_data_dir())),
        )
    return parser


def _read_config(path: Path) -> ProviderConfig:
    # Nunca exibir ValidationError: pode conter uma chave inserida por engano no JSON.
    with path.open(encoding="utf-8-sig") as source:
        content = source.read(65537)
    if len(content.encode("utf-8")) > 65536:
        raise ValueError("Configuração excede o limite.")
    return ProviderConfig.model_validate_json(content)


def _write(value: dict) -> None:
    print(json.dumps(value, ensure_ascii=False, allow_nan=False))


async def run_cli(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        vault_key = os.environ.get("BEES_VAULT_KEY")
        vault = FileSecretVault(
            args.data_dir / "vault", key=SecretStr(vault_key) if vault_key is not None else None
        )
        resolver = build_secret_resolver(vault)
        if args.command == "diagnose":
            config = _read_config(args.config)
            diagnostic = await create_adapter(config.kind, resolver).diagnose(config)
            _write(diagnostic.model_dump(mode="json"))
            return 0 if diagnostic.status == "ok" else 1

        database = Database(args.data_dir / "bees.sqlite3")
        database.initialize()
        service = ProviderService(database, resolver)
        if args.command == "create":
            config = _read_config(args.config)
            agent = Agent(
                name=args.name,
                instructions=args.instructions,
                provider_config=config.model_dump(mode="json"),
            )
            conversation = Conversation(agent_id=agent.id)
            with StateStore(database).transaction(source="model_cli") as unit:
                unit.agents.create(agent)
                unit.conversations.create(conversation)
            _write(
                {
                    "agent_id": str(agent.id),
                    "conversation_id": str(conversation.id),
                    "revision": agent.revision,
                }
            )
        elif args.command == "configure":
            agent = service.set_config(
                args.agent, _read_config(args.config), expected_revision=args.expected_revision
            )
            _write({"agent_id": str(agent.id), "revision": agent.revision})
        else:
            text = args.text if args.text is not None else sys.stdin.read(1048577)
            if not text.strip() or len(text.encode("utf-8")) > 1048576:
                raise ValueError("Texto vazio ou acima do limite.")
            response = await service.chat(args.agent, args.conversation, text)
            _write(response.model_dump(mode="json"))
        return 0
    except ProviderError as error:
        _write({"status": "error", "code": error.code, "message": str(error)})
    except StoreError:
        _write(
            {
                "status": "error",
                "code": "state_error",
                "message": "Estado incompatível, ausente ou alterado por outro escritor.",
            }
        )
    except OSError, UnicodeError, ValueError, ValidationError:
        _write(
            {
                "status": "error",
                "code": "invalid_configuration",
                "message": "Confira a configuração, os identificadores e os limites de entrada.",
            }
        )
    return 1


def main() -> None:
    # Pipes do Python no Windows podem herdar uma codepage incapaz de representar a resposta.
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    try:
        raise SystemExit(asyncio.run(run_cli()))
    except KeyboardInterrupt:
        _write(
            {
                "status": "cancelled",
                "message": "Operação interrompida; não houve repetição automática.",
            }
        )
        raise SystemExit(130) from None
