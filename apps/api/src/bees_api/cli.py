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
        "--deployment-mode",
        choices=("local", "container"),
        default=os.environ.get("BEES_DEPLOYMENT_MODE", "local"),
        help="Modo explícito; BEES_DEPLOYMENT_MODE ou local. Container exige chave em arquivo.",
    )
    parser.add_argument(
        "--host",
        default=os.environ.get("BEES_HOST", "127.0.0.1"),
        help="BEES_HOST ou 127.0.0.1. 0.0.0.0 somente no modo container explícito.",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=os.environ.get("BEES_PORT", "8000"),
        help="Porta; padrão: BEES_PORT ou 8000.",
    )
    parser.add_argument(
        "--browser-port",
        type=int,
        default=os.environ.get("BEES_BROWSER_PORT"),
        help="Porta publicada para o navegador; BEES_BROWSER_PORT ou mesma porta do serviço.",
    )
    parser.add_argument(
        "--vault-key-file",
        type=Path,
        default=os.environ.get("BEES_VAULT_KEY_FILE"),
        help="Chave gerenciada do container em volume separado; BEES_VAULT_KEY_FILE.",
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
            deployment_mode=args.deployment_mode,
            host=args.host,
            port=args.port,
            browser_port=args.browser_port,
            web_dist=args.web_dist,
            data_dir=args.data_dir,
            cache_ttl_seconds=args.cache_ttl_seconds,
            cache_prune_limit=args.cache_prune_limit,
            public_url=args.public_url,
            vault_key=os.environ.get("BEES_VAULT_KEY"),
            vault_key_file=args.vault_key_file,
        )
    except ValidationError:
        parser.error(
            "Configuração inválida. Bind 0.0.0.0 exige modo container e arquivo de chave separado; "
            "modo local usa 127.0.0.1. Portas entre 1 e 65535; "
            "TTL entre 1 e 31536000; limite de limpeza entre 1 e 1000. "
            "URL pública deve ser uma origem HTTPS sem credenciais, caminho ou query."
        )


def main() -> None:
    settings = parse_settings()
    uvicorn.run(
        create_app(settings),
        host=settings.host,
        port=settings.port,
        proxy_headers=settings.deployment_mode == "local",
        forwarded_allow_ips="127.0.0.1" if settings.deployment_mode == "local" else "",
    )
