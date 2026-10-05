"""Cofre em diretórios de teste, com Fernet e DPAPI reais."""

import json
import os
import subprocess
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from cryptography.fernet import Fernet
from pydantic import SecretStr, ValidationError

from bees_core.providers import vault as vault_module
from bees_core.providers.contracts import ProviderCapabilities, ProviderConfig
from bees_core.providers.errors import ProviderError
from bees_core.providers.secrets import build_secret_resolver
from bees_core.providers.vault import DPAPIBackend, FernetBackend, FileSecretVault

TEST_VALUE = "isolated-test-provider-key"


@pytest.fixture
def key() -> SecretStr:
    return SecretStr(Fernet.generate_key().decode("ascii"))


@pytest.fixture
def vault(tmp_path, key) -> FileSecretVault:
    return FileSecretVault(tmp_path / "vault", backend=FernetBackend(key))


def secret_path(vault: FileSecretVault, reference: str):
    return vault.directory / f"{reference[6:]}.secret"


def symlink(source, target, *, directory=False):
    try:
        target.symlink_to(source, target_is_directory=directory)
    except OSError as error:
        pytest.skip(f"Symlink não disponível no ambiente de teste: {error.errno}")


def test_put_resolve_reopen_and_cleanup_without_plaintext(vault, key) -> None:
    reference = vault.put(SecretStr(TEST_VALUE))
    assert reference.startswith("vault:")
    encrypted = secret_path(vault, reference).read_bytes()
    assert TEST_VALUE.encode() not in encrypted
    assert key.get_secret_value().encode() not in encrypted
    assert reference.encode() not in encrypted
    assert vault.resolve(reference).get_secret_value() == TEST_VALUE
    assert TEST_VALUE not in repr(vault.resolve(reference))
    assert TEST_VALUE not in str(vault.status())
    assert vault.available
    assert vault.status().backend == "fernet"
    reopened = FileSecretVault(vault.directory, backend=FernetBackend(key))
    assert reopened.resolve(reference).get_secret_value() == TEST_VALUE
    reopened.delete(reference)
    reopened.delete(reference)
    assert not secret_path(vault, reference).exists()
    with pytest.raises(ProviderError) as result:
        vault.resolve(reference)
    assert result.value.code == "secret_unavailable"
    assert list(vault.directory.iterdir()) == [vault.directory / ".lock"]


def test_ciphertext_tampering_and_wrong_key_fail_closed(vault) -> None:
    reference = vault.put(SecretStr(TEST_VALUE))
    wrong = FileSecretVault(
        vault.directory, backend=FernetBackend(SecretStr(Fernet.generate_key().decode()))
    )
    with pytest.raises(ProviderError) as result:
        wrong.resolve(reference)
    assert result.value.code == "secret_unavailable"
    path = secret_path(vault, reference)
    encrypted = bytearray(path.read_bytes())
    encrypted[len(encrypted) // 2] ^= 1
    path.write_bytes(encrypted)
    with pytest.raises(ProviderError) as result:
        vault.resolve(reference)
    assert TEST_VALUE not in str(result.value)
    assert result.value.code == "secret_unavailable"


def test_swapping_valid_ciphertexts_between_references_is_rejected(vault) -> None:
    first = vault.put(SecretStr("first-isolated-value"))
    second = vault.put(SecretStr("second-isolated-value"))
    secret_path(vault, first).write_bytes(secret_path(vault, second).read_bytes())
    with pytest.raises(ProviderError) as result:
        vault.resolve(first)
    assert result.value.code == "secret_unavailable"
    assert "second-isolated-value" not in str(result.value)


@pytest.mark.parametrize(
    "field,value",
    [
        ("version", 2),
        ("version", True),
        ("digest", "0" * 64),
        ("reference", f"vault:{uuid4()}"),
        ("value", "changed-value"),
    ],
)
def test_payload_version_identity_and_digest_are_checked(vault, key, field, value) -> None:
    reference = vault.put(SecretStr(TEST_VALUE))
    cipher = Fernet(key.get_secret_value().encode())
    path = secret_path(vault, reference)
    payload = json.loads(cipher.decrypt(path.read_bytes()))
    payload[field] = value
    path.write_bytes(cipher.encrypt(json.dumps(payload).encode()))
    with pytest.raises(ProviderError):
        vault.resolve(reference)


@pytest.mark.parametrize(
    "reference",
    [
        "vault:../outside",
        "vault:../../outside",
        "vault:C:\\private",
        "vault:",
        "env:KEY",
        "file:/outside",
        "vault:00000000-0000-0000-0000-000000000000",
    ],
)
def test_reference_traversal_and_unsupported_ids_are_rejected(vault, reference, tmp_path) -> None:
    outside = tmp_path / "outside"
    outside.write_text("Arquivo preservado", encoding="utf-8")
    for operation in [vault.resolve, vault.delete]:
        with pytest.raises(ProviderError) as result:
            operation(reference)
        assert result.value.code == "invalid_secret_reference"
        assert reference not in str(result.value)
    assert outside.read_text(encoding="utf-8") == "Arquivo preservado"


@pytest.mark.parametrize(
    "value", ["", "   ", "key\nvalue", "key\rvalue", "key\x00value", "á", "x" * 8193]
)
def test_invalid_secret_values_never_create_vault_files(vault, value) -> None:
    with pytest.raises(ProviderError) as result:
        vault.put(SecretStr(value))
    assert result.value.code == "invalid_secret"
    assert not vault.directory.exists()


def test_put_requires_secretstr_and_respects_count_and_byte_limits(tmp_path, key) -> None:
    vault = FileSecretVault(tmp_path / "vault", backend=FernetBackend(key), max_secrets=1)
    with pytest.raises(ProviderError) as result:
        vault.put(TEST_VALUE)
    assert result.value.code == "invalid_secret"
    first = vault.put(SecretStr(TEST_VALUE))
    with pytest.raises(ProviderError) as result:
        vault.put(SecretStr("another-test-key"))
    assert result.value.code == "secret_unavailable"
    assert vault.resolve(first).get_secret_value() == TEST_VALUE
    assert len(list(vault.directory.glob("*.secret"))) == 1
    oversized_cipher = FileSecretVault(
        tmp_path / "small", backend=FernetBackend(key), max_ciphertext_bytes=10
    )
    with pytest.raises(ProviderError):
        oversized_cipher.put(SecretStr(TEST_VALUE))
    assert not oversized_cipher.directory.exists()


def test_atomic_publish_failure_cleans_staging_and_preserves_existing_secret(
    vault, monkeypatch
) -> None:
    original = vault.put(SecretStr(TEST_VALUE))

    def fail_publish(*args, **kwargs):
        raise OSError("Falha controlada sem dados privados")

    monkeypatch.setattr(vault_module.os, "replace", fail_publish)
    with pytest.raises(ProviderError) as result:
        vault.put(SecretStr("new-isolated-key"))
    assert result.value.code == "secret_unavailable"
    assert not list(vault.directory.glob(".staging-*"))
    assert len(list(vault.directory.glob("*.secret"))) == 1
    assert vault.resolve(original).get_secret_value() == TEST_VALUE


def test_new_reference_cannot_overwrite_existing_secret(vault, monkeypatch) -> None:
    from uuid import UUID

    original = vault.put(SecretStr(TEST_VALUE))
    monkeypatch.setattr(vault_module, "uuid4", lambda: UUID(original[6:]))
    with pytest.raises(ProviderError):
        vault.put(SecretStr("replacement-test-value"))
    assert vault.resolve(original).get_secret_value() == TEST_VALUE


def test_concurrent_instances_respect_vault_volume_limit(tmp_path, key) -> None:
    directory = tmp_path / "vault"
    vaults = [
        FileSecretVault(directory, backend=FernetBackend(key), max_secrets=1) for _ in range(2)
    ]

    def store_one(vault):
        try:
            return vault.put(SecretStr(TEST_VALUE))
        except ProviderError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        result = list(pool.map(store_one, vaults))
    assert len([ref for ref in result if ref.startswith("vault:")]) == 1
    assert len([code for code in result if code == "secret_unavailable"]) == 1
    assert len(list(directory.glob("*.secret"))) == 1


def test_linked_secret_cannot_read_or_delete_file_outside_vault(vault, tmp_path) -> None:
    reference = vault.put(SecretStr(TEST_VALUE))
    path = secret_path(vault, reference)
    encrypted = path.read_bytes()
    outside = tmp_path / "outside.secret"
    outside.write_bytes(encrypted)
    path.unlink()
    symlink(outside, path)
    for operation in [vault.resolve, vault.delete]:
        with pytest.raises(ProviderError):
            operation(reference)
    assert outside.read_bytes() == encrypted


def test_hardlinked_secret_is_rejected_without_mutating_outside_file(vault, tmp_path) -> None:
    reference = vault.put(SecretStr(TEST_VALUE))
    path = secret_path(vault, reference)
    outside = tmp_path / "outside.secret"
    os.link(path, outside)
    for operation in [vault.resolve, vault.delete]:
        with pytest.raises(ProviderError):
            operation(reference)
    assert outside.exists()
    assert outside.read_bytes() == path.read_bytes()


def test_linked_vault_directory_is_unavailable_and_never_written(tmp_path, key) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    link = tmp_path / "vault"
    symlink(outside, link, directory=True)
    vault = FileSecretVault(link, backend=FernetBackend(key))
    assert not vault.status().available
    with pytest.raises(ProviderError):
        vault.put(SecretStr(TEST_VALUE))
    assert list(outside.iterdir()) == []


@pytest.mark.skipif(os.name != "nt", reason="Junctions são um recurso do filesystem Windows.")
def test_windows_junction_blocks_read_delete_and_write_outside_vault(tmp_path, key) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    legitimate = FileSecretVault(tmp_path / "legitimate", backend=FernetBackend(key))
    reference = legitimate.put(SecretStr(TEST_VALUE))
    encrypted = secret_path(legitimate, reference).read_bytes()
    outside_file = outside / f"{reference[6:]}.secret"
    outside_file.write_bytes(encrypted)
    junction = tmp_path / "vault-junction"
    assert junction.absolute().is_relative_to(tmp_path.absolute())
    assert outside.absolute().is_relative_to(tmp_path.absolute())
    subprocess.run(
        ["cmd", "/d", "/c", "mklink", "/J", str(junction), str(outside)],
        check=True,
        capture_output=True,
    )
    vault = FileSecretVault(junction, backend=FernetBackend(key))
    assert not vault.available
    for operation in [vault.resolve, vault.delete]:
        with pytest.raises(ProviderError):
            operation(reference)
    with pytest.raises(ProviderError):
        vault.put(SecretStr("another-test-key"))
    assert list(outside.iterdir()) == [outside_file]
    assert outside_file.read_bytes() == encrypted


@pytest.mark.skipif(os.name != "nt", reason="Junctions são um recurso do filesystem Windows.")
def test_windows_parent_junction_cannot_redirect_new_vault(tmp_path, key) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    junction = tmp_path / "redirected-data"
    subprocess.run(
        ["cmd", "/d", "/c", "mklink", "/J", str(junction), str(outside)],
        check=True,
        capture_output=True,
    )
    vault = FileSecretVault(junction / "vault", backend=FernetBackend(key))
    assert not vault.available
    with pytest.raises(ProviderError):
        vault.put(SecretStr(TEST_VALUE))
    assert list(outside.iterdir()) == []


@pytest.mark.skipif(os.name != "nt", reason="Este caso verifica o handle da raiz Windows.")
def test_windows_root_cannot_be_replaced_while_vault_operation_holds_handle(
    vault, tmp_path
) -> None:
    vault.put(SecretStr(TEST_VALUE))
    moved = tmp_path / "moved-vault"
    assert vault.directory.absolute().is_relative_to(tmp_path.absolute())
    assert moved.absolute().is_relative_to(tmp_path.absolute())
    with vault._locked(create=False):
        with pytest.raises(OSError):
            vault.directory.rename(moved)
    assert vault.directory.exists()
    assert not moved.exists()


def test_oversized_ciphertext_is_refused_before_decryption(vault) -> None:
    reference = vault.put(SecretStr(TEST_VALUE))
    secret_path(vault, reference).write_bytes(b"x" * 65537)
    with pytest.raises(ProviderError) as result:
        vault.resolve(reference)
    assert result.value.code == "secret_unavailable"


@pytest.mark.skipif(os.name == "nt", reason="Permissões POSIX não são ACLs Windows.")
def test_private_posix_permissions(tmp_path, key) -> None:
    vault = FileSecretVault(tmp_path / "data" / "vault", backend=FernetBackend(key))
    reference = vault.put(SecretStr(TEST_VALUE))
    assert vault.directory.stat().st_mode & 0o777 == 0o700
    assert secret_path(vault, reference).stat().st_mode & 0o777 == 0o600
    assert (vault.directory / ".lock").stat().st_mode & 0o777 == 0o600


@pytest.mark.skipif(os.name == "nt", reason="Windows usa DPAPI e não exige chave Fernet.")
def test_linux_without_external_key_fails_closed_without_creating_directory(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.delenv("BEES_VAULT_KEY", raising=False)
    vault = FileSecretVault(tmp_path / "vault")
    assert not vault.available
    assert vault.status().reason == "Chave externa do cofre não provisionada."
    with pytest.raises(ProviderError):
        vault.put(SecretStr(TEST_VALUE))
    assert not vault.directory.exists()


@pytest.mark.skipif(os.name == "nt", reason="Este caso valida seleção automática Fernet Linux.")
def test_linux_uses_explicit_environment_key_without_writing_it(tmp_path, monkeypatch, key) -> None:
    monkeypatch.setenv("BEES_VAULT_KEY", key.get_secret_value())
    vault = FileSecretVault(tmp_path / "vault")
    reference = vault.put(SecretStr(TEST_VALUE))
    assert vault.status().backend == "fernet"
    assert vault.resolve(reference).get_secret_value() == TEST_VALUE
    assert all(
        key.get_secret_value().encode() not in path.read_bytes()
        for path in vault.directory.iterdir()
    )


def test_invalid_fernet_key_has_safe_diagnostic() -> None:
    invalid = "isolated-invalid-key"
    with pytest.raises(ProviderError) as result:
        FernetBackend(SecretStr(invalid))
    assert invalid not in str(result.value)
    assert result.value.code == "secret_unavailable"


@pytest.mark.skipif(os.name != "nt", reason="DPAPI depende da conta Windows do processo.")
def test_actual_windows_dpapi_current_user_roundtrip_and_tamper(tmp_path) -> None:
    backend = DPAPIBackend()
    assert backend.UI_FORBIDDEN == 0x1
    vault = FileSecretVault(tmp_path / "dpapi-vault")
    assert vault.status().backend == "dpapi_current_user"
    reference = vault.put(SecretStr(TEST_VALUE))
    reopened = FileSecretVault(vault.directory)
    assert reopened.resolve(reference).get_secret_value() == TEST_VALUE
    path = secret_path(vault, reference)
    encrypted = path.read_bytes()
    assert TEST_VALUE.encode() not in encrypted
    path.write_bytes(encrypted[: len(encrypted) // 2])
    with pytest.raises(ProviderError):
        reopened.resolve(reference)
    reopened.delete(reference)
    assert not path.exists()


def test_composite_resolver_keeps_environment_and_vault_references_separate(vault) -> None:
    reference = vault.put(SecretStr(TEST_VALUE))
    resolver = build_secret_resolver(vault, {"BEES_TEST_KEY": "env-test-value"})
    assert resolver.resolve("env:BEES_TEST_KEY").get_secret_value() == "env-test-value"
    assert resolver.resolve(reference).get_secret_value() == TEST_VALUE
    with pytest.raises(ProviderError):
        build_secret_resolver(None, {"BEES_TEST_KEY": TEST_VALUE}).resolve(reference)
    with pytest.raises(ProviderError) as result:
        resolver.resolve("unsupported:BEES_TEST_KEY")
    assert result.value.code == "invalid_secret_reference"


def test_provider_config_accepts_only_supported_vault_uuid_references(vault) -> None:
    reference = vault.put(SecretStr(TEST_VALUE))
    data = {
        "kind": "openai_compatible",
        "endpoint": "https://provider.test/v1",
        "model": "test-model",
        "secret_ref": reference,
        "capabilities": ProviderCapabilities().model_dump(),
    }
    assert ProviderConfig.model_validate(data).secret_ref == reference
    for invalid in ["vault:../outside", "vault:key", reference.upper()]:
        with pytest.raises(ValidationError):
            ProviderConfig.model_validate(data | {"secret_ref": invalid})
