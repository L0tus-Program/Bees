"""Configuração explícita da fundação, limitada ao acesso local."""

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


def default_web_dist() -> Path:
    """A distribuição inicial é executada a partir do checkout do workspace."""
    return Path(__file__).resolve().parents[4] / "apps" / "web" / "dist"


class Settings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    # Autenticação e acesso remoto serão entregues em histórias próprias.
    host: Literal["127.0.0.1"] = "127.0.0.1"
    port: int = Field(default=8000, ge=1, le=65535)
    web_dist: Path = Field(default_factory=default_web_dist)
