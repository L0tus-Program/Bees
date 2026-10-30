"""Exceção fechada de transporte binário; não amplia o limite de JSON."""

import re

from starlette.types import Scope

_UUID = r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
_UPLOAD = re.compile(rf"/api/v1/agents/{_UUID}/conversations/{_UUID}/artifacts/{_UUID}/content")


def artifact_upload(scope: Scope) -> bool:
    return scope.get("method") == "PUT" and _UPLOAD.fullmatch(scope.get("path", "")) is not None
