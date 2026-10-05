"""Configuração do plano de controle local e do estado persistente."""

from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator


def workspace_root() -> Path:
    return Path(__file__).resolve().parents[4]


def default_web_dist() -> Path:
    return workspace_root() / "apps" / "web" / "dist"


def default_data_dir() -> Path:
    return workspace_root() / "data"


class Settings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)

    # Mesmo autenticado, o serviço se expõe remotamente apenas via proxy TLS de loopback.
    host: Literal["127.0.0.1"] = "127.0.0.1"
    port: int = Field(default=8000, ge=1, le=65535)
    web_dist: Path = Field(default_factory=default_web_dist)
    data_dir: Path = Field(default_factory=default_data_dir)
    cache_ttl_seconds: int = Field(default=86400, ge=1, le=31536000)
    cache_prune_limit: int = Field(default=1000, ge=1, le=1000)
    public_url: str | None = None
    vault_key: SecretStr | None = Field(default=None, repr=False)

    @field_validator("public_url")
    @classmethod
    def https_origin(cls, value: str | None) -> str | None:
        if value is None:
            return None
        parsed = urlsplit(value)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in ("", "/")
            or parsed.query
            or parsed.fragment
            or "*" in value
            or "\\" in value
            or any(ord(character) <= 32 for character in value)
        ):
            raise ValueError("URL pública precisa ser uma origem HTTPS explícita.")
        if parsed.port is not None and not 1 <= parsed.port <= 65535:
            raise ValueError("Porta pública inválida.")
        hostname = parsed.hostname.encode("idna").decode("ascii").lower()
        if ":" in hostname:
            hostname = f"[{hostname}]"
        port = f":{parsed.port}" if parsed.port not in (None, 443) else ""
        return f"https://{hostname}{port}"

    @property
    def secure_cookie(self) -> bool:
        return self.public_url is not None

    @property
    def allowed_origins(self) -> tuple[str, ...]:
        if self.public_url is not None:
            return (self.public_url,)
        return (
            f"http://127.0.0.1:{self.port}",
            f"http://localhost:{self.port}",
            "http://127.0.0.1:5173",
            "http://localhost:5173",
        )

    @property
    def allowed_hosts(self) -> list[str]:
        return ["127.0.0.1", "localhost"] + (
            [urlsplit(self.public_url).hostname] if self.public_url else []
        )
