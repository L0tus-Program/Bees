"""Preferências de perfil, sem conceder permissões ou recursos de execução."""

from pydantic import BaseModel, ConfigDict, Field, field_validator

from bees_core.models import Agent


class AgentProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    name: str = Field(min_length=1, max_length=200)
    purpose: str = Field(max_length=4096)
    instructions: str = Field(max_length=16384)
    memory_enabled: bool = Field(strict=True)

    @field_validator("name")
    @classmethod
    def useful_name(cls, value: str) -> str:
        value = value.strip()
        if not value or any(ord(character) < 32 or ord(character) == 127 for character in value):
            raise ValueError("Informe um nome válido para a abelha.")
        return value


def memory_enabled(agent: Agent) -> bool:
    if "profile" not in agent.metadata:
        return True
    profile = agent.metadata["profile"]
    if not isinstance(profile, dict):
        return False
    value = profile.get("memory_enabled", True)
    return value if isinstance(value, bool) else False
