"""Executar a fila com a mesma configuração/cofre, sem iniciar ou migrar a API."""

import asyncio
from datetime import UTC, datetime

from bees_api.cli import parse_settings
from bees_api.config import Settings
from bees_api.managed_key import managed_vault_key, prepare_managed_directories
from bees_api.runtime import validate_sqlite_runtime
from bees_api.worker_status import HEARTBEAT_KEY, HEARTBEAT_TTL, worker_status
from bees_core.execution import TaskWorker
from bees_core.providers.secrets import build_secret_resolver
from bees_core.providers.vault import FernetBackend, FileSecretVault
from bees_core.storage.database import Database
from bees_core.storage.store import StateStore


def existing_database(settings: Settings) -> Database:
    validate_sqlite_runtime()
    if settings.deployment_mode == "container":
        prepare_managed_directories(settings.vault_key_file, settings.data_dir)
    path = settings.data_dir / "bees.sqlite3"
    if not path.is_file():
        raise RuntimeError("Inicie a API antes do executor de tarefas.")
    database = Database(path)
    database.require_current_schema()
    return database


def create_worker(settings: Settings, database: Database) -> TaskWorker:
    if settings.deployment_mode == "container":
        key = managed_vault_key(
            settings.vault_key_file, settings.data_dir, database, provision=False
        )
        vault = FileSecretVault(settings.data_dir / "vault", backend=FernetBackend(key))
    else:
        vault = FileSecretVault(settings.data_dir / "vault", key=settings.vault_key)
    return TaskWorker(database, build_secret_resolver(vault))


async def serve(settings: Settings) -> None:
    database = existing_database(settings)
    worker = create_worker(settings, database)
    store = StateStore(database)

    async def heartbeat() -> None:
        while True:
            store.put_cache(
                HEARTBEAT_KEY,
                {"last_seen": datetime.now(UTC).isoformat()},
                ttl_seconds=HEARTBEAT_TTL,
            )
            await asyncio.sleep(3)

    async with asyncio.TaskGroup() as group:
        group.create_task(heartbeat())
        group.create_task(worker.run_forever())


def main() -> None:
    try:
        asyncio.run(serve(parse_settings()))
    except KeyboardInterrupt:
        pass
    except Exception:
        # Nunca imprimir exceção contendo parâmetros/contexto/chaves de rede.
        raise SystemExit(
            "Executor indisponível. Confira API, estado e cofre correspondentes."
        ) from None


def health() -> None:
    try:
        database = existing_database(parse_settings())
        if not worker_status(StateStore(database))["available"]:
            raise RuntimeError("Heartbeat ausente.")
    except Exception:
        raise SystemExit(1) from None
