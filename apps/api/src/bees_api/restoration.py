"""Human restore into trusted, explicitly configured inactive destinations only."""

import asyncio
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from starlette.concurrency import run_in_threadpool
from starlette.requests import ClientDisconnect

from bees_api.auth import require_session
from bees_core.restoration import RestorationError, RestorationReport, RestoreService
from bees_core.security.identity import Session

_SESSION = Annotated[Session, Depends(require_session)]
UPLOAD_SECONDS = 30
router = APIRouter(prefix="/api/v1/restoration", tags=["restoration"])


def _service(request: Request) -> RestoreService:
    service = getattr(request.app.state, "restoration", None)
    if not isinstance(service, RestoreService):
        raise HTTPException(
            503,
            detail={
                "code": "restoration_not_configured",
                "message": "Configure um destino inativo na instalação.",
            },
        )
    return service


def _error(error: RestorationError):
    status = (
        404 if error.code in {"restoration_destination_unknown", "restoration_not_found"} else 409
    )
    raise HTTPException(
        status, detail={"code": error.code, "message": "A restauração inativa não está disponível."}
    )


@router.get("/destinations")
def destinations(request: Request, session: _SESSION):
    return _service(request).destinations()


@router.post("/destinations/{destination}/restore", response_model=RestorationReport)
async def restore(destination: str, request: Request, session: _SESSION):
    service = _service(request)
    consent = request.headers.getlist("x-bees-restore-consent")
    if (
        consent != ["restore_inactive_installation"]
        or request.headers.get("content-type", "").split(";")[0].strip().lower()
        != "application/zip"
    ):
        raise HTTPException(
            422,
            detail={
                "code": "restoration_consent_required",
                "message": "Confirme a restauração em destino inativo.",
            },
        )
    if destination not in {item["id"] for item in service.destinations()}:
        _error(RestorationError("restoration_destination_unknown"))
    lengths = request.headers.getlist("content-length")
    if len(lengths) > 1 or lengths and not lengths[0].isascii():
        raise HTTPException(400, detail={"code": "restoration_invalid_length"})
    if lengths and not lengths[0].isdigit():
        raise HTTPException(400, detail={"code": "restoration_invalid_length"})
    try:
        promised = int(lengths[0]) if lengths else None
    except ValueError:
        raise HTTPException(400, detail={"code": "restoration_invalid_length"}) from None
    limit = service.verifier.limits.max_bytes
    if promised is not None and promised > limit:
        raise HTTPException(413, detail={"code": "restoration_limit_exceeded"})
    slots = request.app.state.restoration_upload_slots
    if not slots.acquire(blocking=False):
        raise HTTPException(409, detail={"code": "restoration_busy"})
    try:
        body = bytearray()
        async with asyncio.timeout(UPLOAD_SECONDS):
            async for chunk in request.stream():
                if len(body) + len(chunk) > limit:
                    raise HTTPException(413, detail={"code": "restoration_limit_exceeded"})
                body.extend(chunk)
        if promised is not None and promised != len(body):
            raise HTTPException(400, detail={"code": "restoration_invalid_length"})
        return await run_in_threadpool(service.restore, destination, bytes(body))
    except TimeoutError:
        raise HTTPException(408, detail={"code": "restoration_upload_timeout"}) from None
    except ClientDisconnect:
        raise HTTPException(400, detail={"code": "restoration_upload_interrupted"}) from None
    except RestorationError as error:
        _error(error)
    finally:
        slots.release()


@router.get("/destinations/{destination}/report", response_model=RestorationReport)
def report(destination: str, request: Request, session: _SESSION):
    try:
        return _service(request).report(destination)
    except RestorationError as error:
        _error(error)
