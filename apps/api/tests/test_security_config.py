from pathlib import Path

import pytest
from pydantic import ValidationError

from bees_api.config import Settings


@pytest.mark.parametrize(
    ("supplied", "expected"),
    [
        ("https://BEES.example:443/", "https://bees.example"),
        ("https://bees.example:8443", "https://bees.example:8443"),
        ("https://abelhão.example", "https://xn--abelho-7ta.example"),
        ("https://[::1]:443/", "https://[::1]"),
    ],
)
def test_public_origin_matches_browser_canonicalization(supplied: str, expected: str) -> None:
    settings = Settings(public_url=supplied)
    assert settings.public_url == expected
    assert settings.allowed_origins == (expected,)
    assert settings.secure_cookie


@pytest.mark.parametrize(
    "value",
    [
        "http://bees.example",
        "https://user:password@bees.example",
        "https://bees.example/path",
        "https://bees.example?key=private",
        "https://bees.example#fragment",
        "https://*.bees.example",
        "https://bees.example\\path",
        "https://bees.example:0",
        "https://bees.example:65536",
        "https://bees.example\n",
    ],
)
def test_public_origin_rejects_ambiguous_or_insecure_values(value: str) -> None:
    with pytest.raises(ValidationError) as caught:
        Settings(public_url=value)
    assert value not in str(caught.value)


def test_config_repr_hides_external_vault_key(tmp_path: Path) -> None:
    key = "chave-privada-de-teste"
    settings = Settings(data_dir=tmp_path, vault_key=key)
    assert key not in repr(settings)
    assert key not in str(settings)
