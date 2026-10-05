"""Credenciais de teste nunca são enviadas a serviços externos."""

import pytest
from pydantic import SecretStr

from bees_core.providers.errors import ProviderError
from bees_core.providers.secrets import EnvSecretResolver, EnvSecretStore


def test_resolves_reference_without_exposing_value() -> None:
    resolver = EnvSecretResolver({"BEES_TEST_KEY": "private-test-value"})
    value = resolver.resolve("env:BEES_TEST_KEY")
    assert isinstance(value, SecretStr)
    assert value.get_secret_value() == "private-test-value"
    assert "private-test-value" not in str(value)
    assert "private-test-value" not in repr(value)
    assert "private-test-value" not in repr(resolver)
    assert EnvSecretStore is EnvSecretResolver


@pytest.mark.parametrize(
    "reference",
    ["BEES_TEST_KEY", "file:/tmp/key", "env:", "env:1KEY", "env:KEY\n", "env:KEY=value"],
)
def test_invalid_references_produce_safe_error(reference: str) -> None:
    with pytest.raises(ProviderError) as result:
        EnvSecretResolver({"KEY": "private-test-value"}).resolve(reference)
    assert result.value.code == "invalid_secret_reference"
    assert "private-test-value" not in str(result.value)
    if reference != "env:":
        assert reference not in str(result.value)


@pytest.mark.parametrize("value", [None, "", "   "])
def test_missing_credentials_are_diagnosed_without_variable_name(value: str | None) -> None:
    environment = {} if value is None else {"PRIVATE_VARIABLE": value}
    with pytest.raises(ProviderError) as result:
        EnvSecretResolver(environment).resolve("env:PRIVATE_VARIABLE")
    assert result.value.code == "secret_unavailable"
    assert "PRIVATE_VARIABLE" not in str(result.value)


@pytest.mark.parametrize("value", ["test\nvalue", "test\rvalue", "test\x00value", "x" * 8193])
def test_header_unsafe_values_are_rejected_without_echo(value: str) -> None:
    with pytest.raises(ProviderError) as result:
        EnvSecretResolver({"KEY": value}).resolve("env:KEY")
    assert result.value.code == "invalid_secret"
    assert value not in str(result.value)


def test_resolves_current_environment_without_caching_secret(monkeypatch) -> None:
    resolver = EnvSecretResolver()
    monkeypatch.setenv("BEES_TEST_ROTATING_SECRET", "first-test-key")
    assert resolver.resolve("env:BEES_TEST_ROTATING_SECRET").get_secret_value() == "first-test-key"
    monkeypatch.setenv("BEES_TEST_ROTATING_SECRET", "second-test-key")
    assert resolver.resolve("env:BEES_TEST_ROTATING_SECRET").get_secret_value() == "second-test-key"
    monkeypatch.delenv("BEES_TEST_ROTATING_SECRET")
    with pytest.raises(ProviderError) as result:
        resolver.resolve("env:BEES_TEST_ROTATING_SECRET")
    assert result.value.code == "secret_unavailable"
