"""Provas nativas do bootstrap isolado da CI, sem modificar guardas do produto."""

import importlib.util
import os
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[1] / "run-ci-tests.py"
SPEC = importlib.util.spec_from_file_location("bees_ci_tests", SCRIPT)
bootstrap = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bootstrap)

pytestmark = pytest.mark.skipif(os.name != "nt", reason="TokenOwner nativo Windows")


def assert_same_owner(owner, previous):
    current = owner._information(4)
    assert owner.adv.EqualSid(owner._sid(current), owner._sid(previous))


@pytest.mark.parametrize("exit_code", [0, 1, 2])
def test_bootstrap_propagates_pytest_failure_and_restores_owner(monkeypatch, exit_code):
    owner = bootstrap.WindowsOwner()
    try:
        previous = owner._information(4)
        calls = []

        def run(arguments):
            assert owner.owner_is_user()
            calls.append(arguments)
            return exit_code

        monkeypatch.setattr(pytest, "main", run)
        assert bootstrap.main(["-q", "apps/api/tests"]) == exit_code
        assert calls == [["-q", "apps/api/tests"]]
        assert_same_owner(owner, previous)
    finally:
        owner.close()


def test_bootstrap_restores_owner_on_interruption(monkeypatch):
    owner = bootstrap.WindowsOwner()
    try:
        previous = owner._information(4)

        def interrupt(arguments):
            assert owner.owner_is_user()
            raise KeyboardInterrupt

        monkeypatch.setattr(pytest, "main", interrupt)
        with pytest.raises(KeyboardInterrupt):
            bootstrap.main([])
        assert_same_owner(owner, previous)
    finally:
        owner.close()


def test_check_owner_proves_python_and_powershell_children_without_pytest(monkeypatch):
    def unexpected(arguments):
        raise AssertionError("Check-only não deve executar pytest.")

    monkeypatch.setattr(pytest, "main", unexpected)
    assert bootstrap.main(["--check-owner"]) == 0
