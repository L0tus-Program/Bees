"""Convite privado emitido pelo operador local; não provisiona ou migra estado."""

import argparse
import json
import os
import sys
from pathlib import Path
from uuid import uuid4

from bees_api.config import default_data_dir
from bees_core.security.hosts import HostService
from bees_core.storage.database import Database


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("invite",))
    parser.add_argument(
        "--data-dir", type=Path, default=os.environ.get("BEES_DATA_DIR", str(default_data_dir()))
    )
    parser.add_argument("--format", choices=("json",), required=True)
    args = parser.parse_args()
    try:
        database = Database(args.data_dir / "bees.sqlite3")
        database.require_current_schema()
        issued = HostService(database).issue_invite(uuid4())
        # A saída privada é consumida em memória pelo launcher, sem entrar no log.
        print(
            json.dumps(
                {
                    "installation_id": str(issued.installation_id),
                    "invite_id": str(issued.invite_id),
                    "invite_token": issued.invite_token.get_secret_value(),
                    "expires_at": issued.expires_at,
                }
            )
        )
    except Exception:
        # Não renderizar exceções/inputs que possam conter credenciais.
        print(
            "Não foi possível preparar o vínculo. Inicie o Bees e configure seu acesso.",
            file=sys.stderr,
        )
        raise SystemExit(2) from None


if __name__ == "__main__":
    main()
