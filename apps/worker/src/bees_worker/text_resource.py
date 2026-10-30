"""Preparação explícita de processo local de texto; não cria VM nem concede uso."""

import argparse
import json
from pathlib import Path
from uuid import UUID

from bees_api.config import default_data_dir
from bees_core.storage.database import Database
from bees_core.tool_runtime import initialize_resource


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agent", required=True, type=UUID)
    parser.add_argument("--data-dir", type=Path, default=default_data_dir())
    args = parser.parse_args(argv)
    try:
        database = Database(args.data_dir / "bees.sqlite3")
        resource = initialize_resource(database, args.agent)
    except Exception:
        raise SystemExit(
            "Recurso indisponível. Confira instalação, identidade, abelha "
            "e diretório privado POSIX."
        ) from None
    print(
        json.dumps(
            {
                "agent_id": resource.identity.agent_id,
                "resource_id": resource.identity.resource_id,
                "adapter": "local_process_text",
            }
        )
    )


if __name__ == "__main__":
    main()
