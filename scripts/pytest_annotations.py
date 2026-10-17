"""Identifica falhas na CI sem publicar traceback, parâmetros ou conteúdo privado."""

import os
import re


def pytest_runtest_logreport(report):
    if os.environ.get("GITHUB_ACTIONS") != "true" or not report.failed:
        return
    # IDs parametrizados podem conter entradas privadas; somente arquivo/função.
    identifier = report.nodeid.split("[", 1)[0]
    if len(identifier) > 300 or not re.fullmatch(r"[A-Za-z0-9_./:-]+", identifier):
        identifier = "Python test failed (identifier unavailable)"
    metadata = "title=Python regression"
    crash = getattr(report.longrepr, "reprcrash", None)
    line = getattr(crash, "lineno", None)
    filename = identifier.split("::", 1)[0]
    if type(line) is int and line > 0 and filename.endswith(".py"):
        metadata += f",file={filename},line={line}"
    print(f"\n::error {metadata}::{identifier}", flush=True)
