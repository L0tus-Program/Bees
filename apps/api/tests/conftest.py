import pytest


@pytest.fixture(autouse=True)
def isolated_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    for variable in (
        "BEES_HOST",
        "BEES_PORT",
        "BEES_WEB_DIST",
        "BEES_DATA_DIR",
        "BEES_CACHE_TTL_SECONDS",
        "BEES_CACHE_PRUNE_LIMIT",
        "BEES_DEPLOYMENT_MODE",
        "BEES_BROWSER_PORT",
        "BEES_VAULT_KEY_FILE",
        "BEES_VAULT_KEY",
        "BEES_PUBLIC_URL",
    ):
        monkeypatch.delenv(variable, raising=False)
