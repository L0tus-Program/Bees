"""Resolução privada de referências; valores não pertencem ao estado canônico."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from typing import TYPE_CHECKING

from pydantic import SecretStr

from bees_core.providers.contracts import SecretResolver
from bees_core.providers.errors import ProviderError

if TYPE_CHECKING:
    from bees_core.providers.vault import FileSecretVault


class EnvSecretResolver:
    """Resolver mínimo multiplataforma, sem persistir ou registrar valores.

    A referência env:VAR é privada à instalação e exige provisionamento explícito
    da variável no processo do serviço. Não substitui um cofre com onboarding.
    """

    def __init__(self, environment: Mapping[str, str] | None = None) -> None:
        self._environment = os.environ if environment is None else environment

    def resolve(self, secret_ref: str) -> SecretStr:
        if not isinstance(secret_ref, str) or not re.fullmatch(
            r"env:[A-Za-z_][A-Za-z0-9_]{0,127}", secret_ref
        ):
            raise ProviderError(
                "invalid_secret_reference", "Referência de credencial inválida ou não suportada."
            )
        value = self._environment.get(secret_ref[4:])
        if not isinstance(value, str) or not value.strip():
            raise ProviderError(
                "secret_unavailable", "Credencial não provisionada para esta instalação."
            )
        if len(value) > 8192 or any(character in value for character in "\r\n\x00"):
            raise ProviderError("invalid_secret", "Credencial possui formato não suportado.")
        return SecretStr(value)


# Nomes equivalentes para quem chama o contrato de resolver ou secret store.
EnvSecretStore = EnvSecretResolver
SecretStore = SecretResolver


class CompositeSecretResolver:
    """Despacha pela referência explícita, sem fallback entre fontes de segredo."""

    def __init__(
        self,
        vault: FileSecretVault | None = None,
        environment: Mapping[str, str] | None = None,
    ) -> None:
        self._environment = EnvSecretResolver(environment)
        self._vault = vault

    def resolve(self, secret_ref: str) -> SecretStr:
        if isinstance(secret_ref, str) and secret_ref.startswith("env:"):
            return self._environment.resolve(secret_ref)
        if isinstance(secret_ref, str) and secret_ref.startswith("vault:"):
            if self._vault is None:
                raise ProviderError("secret_unavailable")
            return self._vault.resolve(secret_ref)
        raise ProviderError("invalid_secret_reference")


def build_secret_resolver(
    vault: FileSecretVault | None = None,
    environment: Mapping[str, str] | None = None,
) -> CompositeSecretResolver:
    return CompositeSecretResolver(vault, environment)
