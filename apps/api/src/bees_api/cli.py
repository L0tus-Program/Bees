"""Entrypoint local: uv run bees-api."""

import argparse
import os
from collections.abc import Sequence
from pathlib import Path

import uvicorn
from pydantic import ValidationError

from bees_api.app import create_app
from bees_api.config import Settings, default_web_dist


def parse_settings(argv: Sequence[str] | None = None) -> Settings:
    parser = argparse.ArgumentParser(description="Inicia a fundação local do Bees.")
    parser.add_argument(
        "--host",
        default=os.environ.get("BEES_HOST", "127.0.0.1"),
        help="Bind local; padrão: BEES_HOST ou 127.0.0.1. Somente 127.0.0.1 é permitido.",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=os.environ.get("BEES_PORT", "8000"),
        help="Porta; padrão: BEES_PORT ou 8000.",
    )
    parser.add_argument(
        "--web-dist",
        type=Path,
        default=os.environ.get("BEES_WEB_DIST", str(default_web_dist())),
        help="Frontend compilado; padrão: BEES_WEB_DIST ou apps/web/dist do checkout.",
    )
    args = parser.parse_args(argv)
    try:
        return Settings(host=args.host, port=args.port, web_dist=args.web_dist)
    except ValidationError:
        parser.error(
            "Esta fundação permite somente --host 127.0.0.1 e portas entre 1 e 65535. "
            "Acesso remoto exige autenticação ainda não implementada."
        )


def main() -> None:
    settings = parse_settings()
    uvicorn.run(create_app(settings), host=settings.host, port=settings.port)
