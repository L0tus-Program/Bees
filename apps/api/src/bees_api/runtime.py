"""Compatibilidade do transporte com a validação central do driver SQLite."""

from bees_core.storage.database import check_sqlite_runtime


def validate_sqlite_runtime(version: str | None = None) -> None:
    check_sqlite_runtime(version)
