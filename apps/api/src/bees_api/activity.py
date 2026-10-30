"""Timeline e consumo autenticados, sem endpoint de execução ou recuperação."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from bees_api.auth import require_session
from bees_core.activity import ActivityPage, ActivityService
from bees_core.security.identity import Session

router = APIRouter(prefix="/api/v1/agents", tags=["activity"])


class ActivityQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: UUID | None = None
    run_id: UUID | None = None
    since: AwareDatetime | None = None
    until: AwareDatetime | None = None
    window_seconds: int | None = None
    before: int | None = Field(default=None, ge=1, le=2**63 - 1)
    snapshot_seq: int | None = Field(default=None, ge=0, le=2**63 - 1)
    limit: int = Field(default=50, ge=1, le=100)


@router.get("/{agent_id}/activity", response_model=ActivityPage)
def get_activity(
    agent_id: UUID,
    query: Annotated[ActivityQuery, Query()],
    request: Request,
    session: Annotated[Session, Depends(require_session)],
) -> ActivityPage:
    try:
        return ActivityService(request.app.state.database).list(agent_id, **query.model_dump())
    except ValueError:
        raise HTTPException(
            422,
            detail={"code": "validation_failed", "message": "Confira intervalo, escopo e cursor."},
        ) from None
