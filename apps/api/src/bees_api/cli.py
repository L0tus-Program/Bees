"""Entrypoint local: uv run bees-api."""

import argparse
import os
from collections.abc import Sequence
from pathlib import Path

import uvicorn
from pydantic import ValidationError

from bees_api.app import create_app
from bees_api.config import Settings, default_data_dir, default_web_dist


def parse_settings(argv: Sequence[str] | None = None) -> Settings:
    parser = argparse.ArgumentParser(description="Inicia o serviço local do Bees.")
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
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=os.environ.get("BEES_DATA_DIR", str(default_data_dir())),
        help="Estado local; padrão: BEES_DATA_DIR ou data no checkout.",
    )
    parser.add_argument(
        "--public-url",
        default=os.environ.get("BEES_PUBLIC_URL"),
        help="Origem pública HTTPS explícita, atrás de proxy TLS local. Opcional.",
    )
    parser.add_argument(
        "--cache-ttl-seconds",
        type=int,
        default=os.environ.get("BEES_CACHE_TTL_SECONDS", "86400"),
        help="Retenção padrão de cache; BEES_CACHE_TTL_SECONDS ou 86400 segundos.",
    )
    parser.add_argument(
        "--cache-prune-limit",
        type=int,
        default=os.environ.get("BEES_CACHE_PRUNE_LIMIT", "1000"),
        help="Máximo de caches expirados removidos no startup; BEES_CACHE_PRUNE_LIMIT ou 1000.",
    )
    args = parser.parse_args(argv)
    try:
        return Settings(
            host=args.host,
            port=args.port,
            web_dist=args.web_dist,
            data_dir=args.data_dir,
            cache_ttl_seconds=args.cache_ttl_seconds,
            cache_prune_limit=args.cache_prune_limit,
            public_url=args.public_url,
            vault_key=os.environ.get("BEES_VAULT_KEY"),
        )
    except ValidationError:
        parser.error(
            "Configuração inválida. Bind deve ser 127.0.0.1; porta entre 1 e 65535; "
            "TTL entre 1 e 31536000; limite de limpeza entre 1 e 1000. "
            "URL pública deve ser uma origem HTTPS sem credenciais, caminho ou query."
        )


def main() -> None:
    settings = parse_settings()
    uvicorn.run(
        create_app(settings),
        host=settings.host,
        port=settings.port,
        proxy_headers=True,
        forwarded_allow_ips="127.0.0.1",
    )
