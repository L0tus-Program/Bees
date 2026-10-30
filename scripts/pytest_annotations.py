"""Identifica falhas na CI sem publicar traceback, parâmetros ou conteúdo privado."""

import os
import re
import sys
from pathlib import PurePosixPath

import pytest

_CASES = {}
_PUBLIC_CODES = frozenset(
    {"provision_command_timeout", "provision_command_invalid", "provision_command_unknown"}
)


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(item, call):
    report = yield
    # No imports of the host runtime, no exception formatting or cause traversal.
    module = sys.modules.get("bees_host.provisioning.contracts")
    error = call.excinfo.value if call.excinfo is not None else None
    expected = getattr(module, "ProvisionError", None) if module is not None else None
    if expected is not None and type(error) is expected:
        arguments = BaseException.args.__get__(error)
        if len(arguments) == 1 and type(arguments[0]) is str and arguments[0] in _PUBLIC_CODES:
            report.bees_public_error_code = arguments[0]
    return report


def pytest_collection_modifyitems(items):
    counts = {}
    _CASES.clear()
    for item in items:
        group = item.nodeid.split("[", 1)[0]
        counts[group] = counts.get(group, 0) + 1
        _CASES[item.nodeid] = counts[group]


def _identifier(nodeid):
    identifier = nodeid.split("[", 1)[0].replace("\\", "/")
    filename, separator, function = identifier.partition("::")
    if filename.startswith("/") or re.match(r"^[A-Za-z]:", filename) or ".." in filename.split("/"):
        filename = PurePosixPath(filename).name
    identifier = filename + separator + function
    if len(identifier) > 300 or not re.fullmatch(r"[A-Za-z0-9_./:-]+", identifier):
        return "Python test failed (identifier unavailable)"
    return identifier


def pytest_runtest_logreport(report):
    if os.environ.get("GITHUB_ACTIONS") != "true" or not report.failed:
        return
    # IDs parametrizados podem conter entradas privadas; somente arquivo/função.
    identifier = _identifier(report.nodeid)
    metadata = "title=Python regression"
    crash = getattr(report.longrepr, "reprcrash", None)
    line = getattr(crash, "lineno", None)
    filename = identifier.split("::", 1)[0]
    crash_path = str(getattr(crash, "path", "")).replace("\\", "/")
    if PurePosixPath(crash_path).name != PurePosixPath(filename).name:
        location = getattr(report, "location", None)
        line = location[1] + 1 if location and type(location[1]) is int else None
    if type(line) is int and line > 0 and filename.endswith(".py"):
        metadata += f",file={filename},line={line}"
    case = _CASES.get(report.nodeid)
    suffix = f" (case {case})" if case is not None else ""
    code = getattr(report, "bees_public_error_code", None)
    if type(code) is str and code in _PUBLIC_CODES:
        suffix += f" [code={code}]"
    print(f"\n::error {metadata}::{identifier}{suffix}", flush=True)
