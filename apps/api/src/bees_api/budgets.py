"""Consulta de consumo e edição humana do limite de tokens por abelha."""

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from pydantic import Field

from bees_api.auth import require_session
from bees_api.onboarding import Input
from bees_core.budgets import BudgetService, LimitInput
from bees_core.security.identity import Session

_SESSION = Annotated[Session, Depends(require_session)]
router = APIRouter(prefix="/api/v1/agents", tags=["budgets"])


class BudgetInput(Input):
    # Ausente cria o primeiro limite; editar exige a revisão lida (CAS).
    expected_revision: int | None = Field(default=None, ge=1, strict=True)
    token_limit: int = Field(ge=1, le=1_000_000_000, strict=True)
    window_seconds: int = Field(default=86400, ge=3600, le=2_592_000, strict=True)
    output_allowance: int = Field(default=4096, ge=1, le=1_000_000, strict=True)
    status: Literal["active", "disabled"] = "active"


def _service(request: Request) -> BudgetService:
    return BudgetService(request.app.state.database)


def _view(summary: dict) -> dict:
    limit = summary["limit"]
    return {
        "limit": (
            {
                "token_limit": limit.token_limit,
                "window_seconds": limit.window_seconds,
                "output_allowance": limit.output_allowance,
                "status": limit.status,
                "revision": limit.revision,
                "updated_at": limit.updated_at.isoformat(),
            }
            if limit is not None
            else None
        ),
        "window_seconds": summary["window_seconds"],
        # Contado = informado quando completo; reserva quando ausente, parcial ou incerto.
        "counted_tokens": summary["counted_tokens"],
        "reported_tokens": summary["reported_tokens"],
        "remaining_tokens": summary["remaining_tokens"],
        "open_reservations": summary["open_reservations"],
        "unknown_entries": summary["unknown_entries"],
        "entries": summary["entries"],
    }


@router.get("/{agent_id}/budget")
def get_budget(agent_id: UUID, request: Request, session: _SESSION) -> dict:
    return _view(_service(request).summary(agent_id))


@router.put("/{agent_id}/budget")
def put_budget(agent_id: UUID, body: BudgetInput, request: Request, session: _SESSION) -> dict:
    service = _service(request)
    service.set_limit(
        agent_id,
        LimitInput(
            token_limit=body.token_limit,
            window_seconds=body.window_seconds,
            output_allowance=body.output_allowance,
            status=body.status,
        ),
        expected_revision=body.expected_revision,
    )
    return _view(service.summary(agent_id))
