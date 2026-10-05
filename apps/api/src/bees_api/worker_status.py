"""Presença derivada do processo, sem confundir heartbeat com trabalho concluído."""

from datetime import UTC, datetime

from bees_core.storage.store import StateStore

HEARTBEAT_KEY = "execution:worker:heartbeat"
HEARTBEAT_TTL = 20


def worker_status(store: StateStore) -> dict:
    value = store.get_cache(HEARTBEAT_KEY)
    if not isinstance(value, dict) or not isinstance(value.get("last_seen"), str):
        return {"available": False, "last_seen": None}
    try:
        last_seen = datetime.fromisoformat(value["last_seen"])
        age = (datetime.now(UTC) - last_seen).total_seconds()
        if not 0 <= age < HEARTBEAT_TTL:
            return {"available": False, "last_seen": None}
    except ValueError, TypeError:
        return {"available": False, "last_seen": None}
    return {"available": True, "last_seen": last_seen.isoformat()}
