"""Readiness interna: banco e frontend presentes, sem autenticação ou dados pessoais."""

import json
import sys
from pathlib import Path
from urllib.request import ProxyHandler, build_opener

try:
    with build_opener(ProxyHandler({})).open(
        "http://127.0.0.1:8000/api/v1/state/status", timeout=3
    ) as response:
        state = json.load(response)
    ready = (
        state.get("status") == "ready"
        and state.get("schema_version", 0) >= 2
        and Path("/app/web/index.html").is_file()
    )
except OSError, ValueError:
    ready = False
sys.exit(0 if ready else 1)
