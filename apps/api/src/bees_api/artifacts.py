"""Artefatos autenticados: entrada humana local, versões e leitura verificada."""

import asyncio
import base64
import binascii
import hashlib
import json
import re
from typing import Annotated
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from starlette.concurrency import run_in_threadpool
from starlette.requests import ClientDisconnect

from bees_api.auth import require_session
from bees_core.artifacts import DEFAULT_MAX_BYTES, MEDIA_TYPES, ArtifactStore, display_name
from bees_core.models import Artifact
from bees_core.security.identity import Session
from bees_core.storage.store import NotFoundError

_SESSION = Annotated[Session, Depends(require_session)]
router = APIRouter(prefix="/api/v1/agents", tags=["artifacts"])


def _store(request: Request) -> ArtifactStore:
    return request.app.state.artifacts


def _view(artifact: Artifact) -> dict:
    # Sem storage_key, caminho, metadados internos ou conteúdo.
    return {
        "id": str(artifact.id),
        "agent_id": str(artifact.agent_id) if artifact.agent_id else None,
        "conversation_id": str(artifact.conversation_id) if artifact.conversation_id else None,
        "task_id": str(artifact.task_id) if artifact.task_id else None,
        "source": artifact.source,
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


class UploadMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    name: str = Field(min_length=1, max_length=200)
    media_type: str
    size_bytes: int = Field(strict=True, ge=0, le=DEFAULT_MAX_BYTES)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    previous_id: UUID | None = None
    expected_revision: int | None = Field(default=None, strict=True, ge=1, le=2**63 - 1)


def _reject(code: str, status: int = 422) -> None:
    raise HTTPException(
        status, detail={"code": code, "message": "Confira o arquivo e atualize a tela."}
    )


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError()
        result[key] = value
    return result


def _metadata(request: Request) -> UploadMetadata:
    values = request.headers.getlist("x-bees-artifact")
    if (
        len(values) != 1
        or len(values[0]) > 2048
        or not re.fullmatch(r"[A-Za-z0-9_-]+={0,2}", values[0])
    ):
        _reject("artifact_metadata_invalid")
    try:
        encoded = values[0]
        decoded = base64.b64decode(
            encoded + "=" * (-len(encoded) % 4), altchars=b"-_", validate=True
        )
        if base64.urlsafe_b64encode(decoded).decode().rstrip("=") != encoded.rstrip("="):
            raise ValueError()
        body = json.loads(decoded.decode("utf-8"), object_pairs_hook=_pairs)
        metadata = UploadMetadata.model_validate(body)
        display_name(metadata.name)
        if metadata.media_type not in MEDIA_TYPES or (metadata.previous_id is None) != (
            metadata.expected_revision is None
        ):
            raise ValueError()
    except ValueError, UnicodeError, binascii.Error, ValidationError:
        _reject("artifact_metadata_invalid")
    return metadata


def _conversation_artifact(
    request: Request, agent_id: UUID, conversation_id: UUID, artifact_id: UUID
) -> Artifact:
    with request.app.state.store.transaction(write=False) as unit:
        conversation = unit.conversations.get(conversation_id)
        artifact = unit.artifacts.get(artifact_id)
        if (
            conversation is None
            or conversation.agent_id != agent_id
            or artifact is None
            or artifact.agent_id != agent_id
            or artifact.conversation_id != conversation_id
        ):
            raise NotFoundError("Artefato não encontrado nesta conversa.")
        return artifact


@router.get("/{agent_id}/conversations/{conversation_id}/artifacts")
def conversation_artifacts(
    agent_id: UUID,
    conversation_id: UUID,
    request: Request,
    session: _SESSION,
    offset: Annotated[int, Query(ge=0, le=1000000)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 100,
) -> dict:
    items = _store(request).list_conversation(
        agent_id, conversation_id, limit=limit + 1, offset=offset
    )
    return {
        "artifacts": [_view(item) for item in items[:limit]],
        "has_more": len(items) > limit,
        "next_offset": offset + limit if len(items) > limit else None,
    }


@router.get("/{agent_id}/conversations/{conversation_id}/artifacts/{artifact_id}")
def conversation_artifact(
    agent_id: UUID, conversation_id: UUID, artifact_id: UUID, request: Request, session: _SESSION
) -> dict:
    return _view(_conversation_artifact(request, agent_id, conversation_id, artifact_id))


@router.get("/{agent_id}/conversations/{conversation_id}/artifacts/{artifact_id}/preview")
def preview_artifact(
    agent_id: UUID,
    conversation_id: UUID,
    artifact_id: UUID,
    request: Request,
    session: _SESSION,
    limit: Annotated[int, Query(ge=1, le=65536)] = 65536,
) -> dict:
    _conversation_artifact(request, agent_id, conversation_id, artifact_id)
    artifact, content = _store(request).read(agent_id, artifact_id)
    if artifact.media_type not in MEDIA_TYPES:
        _reject("artifact_preview_unavailable")
    try:
        content.decode("utf-8")
    except UnicodeError:
        _reject("artifact_preview_unavailable")
    # Apenas a última sequência UTF-8 incompleta pode ser omitida; o blob completo
    # já foi validado na publicação. Nunca interpretar HTML/Markdown no servidor.
    text = content[:limit].decode("utf-8", errors="ignore")
    return {
        "artifact": _view(artifact),
        "text": text,
        "truncated": len(content) > limit,
        "total_bytes": len(content),
    }


@router.put("/{agent_id}/conversations/{conversation_id}/artifacts/{client_request_id}/content")
async def upload_artifact(
    agent_id: UUID,
    conversation_id: UUID,
    client_request_id: UUID,
    request: Request,
    session: _SESSION,
) -> dict:
    # Dependência de sessão/CSRF executada antes de ler qualquer byte volumoso.
    if client_request_id.version != 4:
        _reject("artifact_metadata_invalid")
    metadata = _metadata(request)
    lengths = request.headers.getlist("content-length")
    if len(lengths) != 1 or not re.fullmatch(r"[0-9]{1,10}", lengths[0]):
        _reject("invalid_length", 400)
    length = int(lengths[0])
    if length > DEFAULT_MAX_BYTES:
        _reject("body_too_large", 413)
    if (
        length != metadata.size_bytes
        or request.headers.getlist("content-encoding")
        or request.headers.getlist("transfer-encoding")
    ):
        _reject("artifact_metadata_invalid")
    store = _store(request)
    arguments = {
        "client_request_id": client_request_id,
        "name": metadata.name,
        "media_type": metadata.media_type,
    }
    if metadata.previous_id is None:
        await run_in_threadpool(store.guard_upload, agent_id, conversation_id, **arguments)
    else:
        await run_in_threadpool(
            store.guard_version,
            agent_id,
            conversation_id,
            metadata.previous_id,
            expected_revision=metadata.expected_revision,
            **arguments,
        )
    slots = request.app.state.artifact_upload_slots
    if not slots.acquire(blocking=False):
        _reject("artifact_upload_busy", 429)
    try:
        content = bytearray()
        try:
            async with asyncio.timeout(10):
                async for chunk in request.stream():
                    if len(content) + len(chunk) > length:
                        _reject("invalid_length", 400)
                    content.extend(chunk)
        except TimeoutError:
            _reject("body_timeout", 408)
        except ClientDisconnect:
            _reject("upload_disconnected", 400)
        if len(content) != length or hashlib.sha256(content).hexdigest() != metadata.sha256:
            _reject("artifact_content_mismatch")
        require_session(request)
        if metadata.previous_id is None:
            artifact = await run_in_threadpool(
                store.upload, agent_id, conversation_id, content=bytes(content), **arguments
            )
        else:
            artifact = await run_in_threadpool(
                store.revise,
                agent_id,
                conversation_id,
                metadata.previous_id,
                expected_revision=metadata.expected_revision,
                content=bytes(content),
                **arguments,
            )
    finally:
        slots.release()
    return _view(artifact)


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
