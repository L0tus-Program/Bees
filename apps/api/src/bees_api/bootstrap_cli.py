"""Emite bootstrap no terminal do operador; nunca nos logs do serviço HTTP."""

import argparse
import json
import os
import sys
from pathlib import Path

from bees_api.config import default_data_dir
from bees_core.security.identity import AuthError, IdentityService
from bees_core.storage.database import Database, DatabaseError


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Gera o código privado do primeiro acesso.")
    parser.add_argument("command", choices=["bootstrap"])
    parser.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        help="JSON privado para launcher: configured e bootstrap_token quando necessário.",
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=os.environ.get("BEES_DATA_DIR", str(default_data_dir())),
    )
    args = parser.parse_args()
    database = Database(args.data_dir / "bees.sqlite3")
    if not database.path.is_file():
        parser.error("Inicie bees-api neste diretório de dados antes de gerar o código.")
    try:
        if database.schema_version() < 2:
            parser.error("Pare e atualize bees-api para migrar a identidade antes de continuar.")
        identity = IdentityService(database)
        token = identity.issue_bootstrap()
    except AuthError as error:
        if args.format == "json" and error.code == "already_configured":
            print(json.dumps({"configured": True}, separators=(",", ":")))
            return
        parser.error(str(error))
    except DatabaseError:
        parser.error("Estado indisponível; confira o diretório de dados e as migrações.")
    if args.format == "json":
        print(json.dumps({"configured": False, "bootstrap_token": token}, separators=(",", ":")))
        return
    print("Código de configuração, válido por 15 minutos e uma única utilização:")
    print(token)
    print("Cole no primeiro acesso do Bees. Gerar outro código invalida este.")
