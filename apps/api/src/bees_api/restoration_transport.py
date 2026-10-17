"""Somente o upload humano de pacote conhecido recebe transporte ZIP limitado."""

import re

from starlette.types import Scope

_UPLOAD = re.compile(r"/api/v1/restoration/destinations/[a-z][a-z0-9-]{0,62}/restore")


def restoration_upload(scope: Scope) -> bool:
    return scope.get("method") == "POST" and _UPLOAD.fullmatch(scope.get("path", "")) is not None
