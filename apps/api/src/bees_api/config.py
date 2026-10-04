"""Configuração do plano de controle local e do estado persistente."""

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


def workspace_root() -> Path:
    return Path(__file__).resolve().parents[4]


def default_web_dist() -> Path:
    return workspace_root() / "apps" / "web" / "dist"


def default_data_dir() -> Path:
    return workspace_root() / "data"


class Settings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    # Autenticação e acesso remoto serão entregues em histórias próprias.
    host: Literal["127.0.0.1"] = "127.0.0.1"
    port: int = Field(default=8000, ge=1, le=65535)
    web_dist: Path = Field(default_factory=default_web_dist)
    data_dir: Path = Field(default_factory=default_data_dir)
    cache_ttl_seconds: int = Field(default=86400, ge=1, le=31536000)
    cache_prune_limit: int = Field(default=1000, ge=1, le=1000)
