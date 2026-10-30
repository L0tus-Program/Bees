"""Private logical export under authenticated human session and CSRF."""

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict

from bees_api.auth import require_session
from bees_core.portability import ExportService, PortabilityError
from bees_core.security.identity import Session

_SESSION = Annotated[Session, Depends(require_session)]
router = APIRouter(prefix="/api/v1/portability", tags=["portability"])


class ExportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    consent: Literal["export_private_data"]


def _service(request: Request) -> ExportService:
    artifacts = request.app.state.artifacts
    return ExportService(request.app.state.database, artifacts, artifacts.root.parent / "exports")


def _error(error: PortabilityError):
    status = 404 if error.code == "portability_not_found" else 409
    raise HTTPException(
        status, detail={"code": error.code, "message": "A exportação privada não está disponível."}
    )


@router.post("/exports")
def create(body: ExportRequest, request: Request, session: _SESSION):
    try:
        return _service(request).create()
    except PortabilityError as error:
        _error(error)


@router.get("/exports/{export_id}/download")
def download(export_id: UUID, request: Request, session: _SESSION):
    try:
        content = _service(request).download(export_id)
    except PortabilityError as error:
        _error(error)
    return Response(
        content,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="bees-export-{export_id}.zip"',
            "Cache-Control": "no-store, private",
            "Pragma": "no-cache",
        },
    )
