"""Busca e referência autenticadas e estritamente de leitura (BEES-025.2)."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from bees_api.auth import require_session
from bees_core.delivery_history import DeliveryHistory, DeliveryQueryError
from bees_core.security.identity import Session

router = APIRouter(prefix="/api/v1/agents", tags=["delivery-history"])
_SESSION = Annotated[Session, Depends(require_session)]


@router.get("/{agent_id}/deliveries")
def deliveries(
    agent_id: UUID,
    request: Request,
    session: _SESSION,
    q: Annotated[str, Query(max_length=200)] = "",
    conversation_id: UUID | None = None,
    task_id: UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    cursor: Annotated[str | None, Query(max_length=2048)] = None,
) -> dict:
    try:
        return DeliveryHistory(request.app.state.database).list(
            agent_id,
            query=q,
            conversation_id=conversation_id,
            task_id=task_id,
            limit=limit,
            cursor=cursor,
        )
    except DeliveryQueryError:
        raise HTTPException(
            422,
            detail={
                "code": "delivery_query_invalid",
                "message": "Atualize a consulta e tente novamente.",
            },
        ) from None


@router.get("/{agent_id}/deliveries/results/{message_id}")
def result(
    agent_id: UUID,
    message_id: UUID,
    run_id: UUID,
    request: Request,
    session: _SESSION,
) -> dict:
    return DeliveryHistory(request.app.state.database).result(agent_id, message_id, run_id)


@router.get("/{agent_id}/deliveries/artifacts/{artifact_id}")
def artifact(
    agent_id: UUID,
    artifact_id: UUID,
    request: Request,
    session: _SESSION,
) -> dict:
    from bees_api.artifacts import _view
    from bees_core.storage.store import NotFoundError

    with request.app.state.store.transaction(write=False) as unit:
        item = unit.artifacts.get(artifact_id)
        if item is None or item.agent_id != agent_id:
            raise NotFoundError("Entrega não encontrada nesta abelha.")
        return _view(item)
