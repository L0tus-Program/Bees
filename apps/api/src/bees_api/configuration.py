"""Troca explícita do modelo sem perder perfil, conversa ou memórias."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from pydantic import Field

from bees_api.auth import require_session
from bees_api.onboarding import (
    ModelInput,
    _binding,
    _connection,
    _signature,
    _summary,
    validate_existing_reference,
)
from bees_core.providers.contracts import ProviderConfig
from bees_core.providers.errors import ProviderError
from bees_core.security.identity import Session
from bees_core.storage.store import NotFoundError, RevisionConflict

router = APIRouter(prefix="/api/v1", tags=["configuration"])
_SESSION = Annotated[Session, Depends(require_session)]


class ConfigurationInput(ModelInput):
    expected_revision: int = Field(ge=1, strict=True)
    validation_token: str = Field(min_length=43, max_length=43, repr=False)


@router.post("/agents/{agent_id}/configuration")
def configure(
    agent_id: UUID, body: ConfigurationInput, request: Request, session: _SESSION
) -> dict:
    if body.agent_id is not None and body.agent_id != agent_id:
        raise ProviderError("invalid_config")
    bound = body.model_copy(update={"agent_id": agent_id})
    with request.app.state.store.transaction(write=False) as unit:
        current = unit.agents.get(agent_id)
        if current is None:
            raise NotFoundError("Abelha não encontrada.")
        if current.revision != body.expected_revision:
            raise RevisionConflict("Perfil alterado; atualize antes de salvar.")
        validate_existing_reference(current, bound.config)
    _connection(request, bound)
    request.app.state.receipts.consume(body.validation_token, _signature(bound, _binding(request)))
    reference = None
    committed = False
    try:
        config = body.config
        if body.api_key is not None:
            reference = request.app.state.vault.put(body.api_key)
            config = ProviderConfig.model_validate(config.model_dump() | {"secret_ref": reference})
        updated = request.app.state.providers.update_profile(
            agent_id, expected_revision=body.expected_revision, config=config
        )
        committed = True
        with request.app.state.store.transaction(write=False) as unit:
            return _summary(unit, updated)
    finally:
        # Referências anteriores podem ser usadas por outra abelha ou por um snapshot ativo.
        if reference is not None and not committed:
            request.app.state.vault.delete(reference)
