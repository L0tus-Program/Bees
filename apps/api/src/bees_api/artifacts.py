"""Listagem e download autenticados de artefatos publicados; sem upload neste recorte."""

import re
from typing import Annotated
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import Response

from bees_api.auth import require_session
from bees_core.artifacts import ArtifactStore
from bees_core.models import Artifact
from bees_core.security.identity import Session

_SESSION = Annotated[Session, Depends(require_session)]
router = APIRouter(prefix="/api/v1/agents", tags=["artifacts"])


def _store(request: Request) -> ArtifactStore:
    return request.app.state.artifacts


def _view(artifact: Artifact) -> dict:
    # Sem storage_key, caminho, metadados internos ou conteúdo.
    return {
        "id": str(artifact.id),
        "task_id": str(artifact.task_id),
        "run_id": str(artifact.run_id) if artifact.run_id else None,
        "name": artifact.name,
        "media_type": artifact.media_type,
        "size_bytes": artifact.size_bytes,
        "sha256": artifact.sha256,
        "version": artifact.version,
        "series_id": str(artifact.series_id) if artifact.series_id else None,
        "previous_id": str(artifact.previous_id) if artifact.previous_id else None,
        "status": artifact.status,
        "revision": artifact.revision,
        "created_at": artifact.created_at.isoformat(),
        "updated_at": artifact.updated_at.isoformat(),
    }


def _disposition(name: str) -> str:
    """Sempre anexo: o navegador baixa em vez de interpretar conteúdo não confiável."""
    fallback = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._") or "artefato"
    return f"attachment; filename=\"{fallback}\"; filename*=UTF-8''{quote(name, safe='')}"


@router.get("/{agent_id}/tasks/{task_id}/artifacts")
def list_artifacts(
    agent_id: UUID,
    task_id: UUID,
    request: Request,
    session: _SESSION,
    offset: Annotated[int, Query(ge=0, le=1000000)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 100,
) -> dict:
    items = _store(request).list(agent_id, task_id, limit=limit + 1, offset=offset)
    has_more = len(items) > limit
    return {
        "artifacts": [_view(item) for item in items[:limit]],
        "has_more": has_more,
        "next_offset": offset + limit if has_more else None,
    }


@router.get("/{agent_id}/artifacts/{artifact_id}/download")
def download_artifact(
    agent_id: UUID, artifact_id: UUID, request: Request, session: _SESSION
) -> Response:
    # O blob é relido e conferido (tamanho/hash) antes de qualquer byte ser enviado.
    artifact, content = _store(request).read(agent_id, artifact_id)
    media_type = artifact.media_type
    if media_type.startswith("text/"):
        media_type += "; charset=utf-8"
    return Response(
        content,
        media_type=media_type,
        headers={
            "Content-Disposition": _disposition(artifact.name),
            # Soma-se à CSP global: uma abertura direta fica sem scripts nem origem.
            "Content-Security-Policy": "sandbox",
        },
    )
