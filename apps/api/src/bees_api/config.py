"""Configuração do plano de controle local e do estado persistente."""

import os
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator


def workspace_root() -> Path:
    return Path(__file__).resolve().parents[4]


def default_web_dist() -> Path:
    return workspace_root() / "apps" / "web" / "dist"


def default_data_dir() -> Path:
    return workspace_root() / "data"


class Settings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)

    # Container exige publicação loopback explícita no host e mantém fronteiras HTTP.
    deployment_mode: Literal["local", "container"] = "local"
    host: Literal["127.0.0.1", "0.0.0.0"] = "127.0.0.1"
    port: int = Field(default=8000, ge=1, le=65535)
    browser_port: int | None = Field(default=None, ge=1, le=65535)
    web_dist: Path = Field(default_factory=default_web_dist)
    data_dir: Path = Field(default_factory=default_data_dir)
    cache_ttl_seconds: int = Field(default=86400, ge=1, le=31536000)
    cache_prune_limit: int = Field(default=1000, ge=1, le=1000)
    public_url: str | None = None
    vault_key: SecretStr | None = Field(default=None, repr=False)
    vault_key_file: Path | None = None

    @model_validator(mode="after")
    def deployment_boundary(self) -> Settings:
        if self.host != "127.0.0.1" and self.deployment_mode != "container":
            raise ValueError("Bind externo exige modo container explícito.")
        if self.deployment_mode == "container":
            if self.vault_key_file is None or self.vault_key is not None:
                raise ValueError(
                    "Container exige arquivo privado de chave; não use chave no ambiente."
                )
            data = Path(os.path.abspath(self.data_dir.expanduser()))
            key = Path(os.path.abspath(self.vault_key_file.expanduser()))
            if not self.vault_key_file.is_absolute() or (
                key.is_relative_to(data) or data.is_relative_to(key.parent)
            ):
                raise ValueError("A chave deve ficar em diretório absoluto separado dos dados.")
        elif self.vault_key_file is not None:
            raise ValueError("Arquivo gerenciado de chave exige modo container explícito.")
        return self

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
        port = self.browser_port or self.port
        local = (
            f"http://127.0.0.1:{port}",
            f"http://localhost:{port}",
        )
        if self.deployment_mode == "container":
            return local
        return local + (
            "http://127.0.0.1:5173",
            "http://localhost:5173",
        )

    @property
    def allowed_hosts(self) -> list[str]:
        return ["127.0.0.1", "localhost"] + (
            [urlsplit(self.public_url).hostname] if self.public_url else []
        )
