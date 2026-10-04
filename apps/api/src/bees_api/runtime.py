"""Valida a biblioteca efetiva antes de aceitar tráfego; não abre estado."""

import re

import apsw

MINIMUM_SQLITE = (3, 51, 3)
WITHDRAWN_SQLITE = (3, 52, 0)


def validate_sqlite_runtime(version: str | None = None) -> None:
    effective = apsw.sqlitelibversion() if version is None else version
    if not re.fullmatch(r"\d+\.\d+\.\d+", effective):
        raise RuntimeError("Não foi possível validar a versão efetiva do SQLite do APSW.")
    parsed = tuple(int(part) for part in effective.split("."))
    if parsed < MINIMUM_SQLITE or parsed == WITHDRAWN_SQLITE:
        raise RuntimeError(
            f"SQLite {effective} não suportado: necessário >=3.51.3, exceto 3.52.0. "
            "Sincronize as dependências fixadas em uv.lock."
        )
