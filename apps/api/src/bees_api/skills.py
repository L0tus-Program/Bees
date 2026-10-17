"""Biblioteca de skills sob sessão humana; nenhum endpoint inicia execução."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query, Request

from bees_api.auth import require_session
from bees_core.security.identity import Session
from bees_core.skills import (
    CreateProcessSkill,
    CreateSkill,
    EnableSkill,
    ImportSkill,
    ReviseSkill,
    Skill,
    SkillDocument,
    SkillProcessOrigin,
    SkillRevision,
    SkillService,
)

_SESSION = Annotated[Session, Depends(require_session)]
_LIMIT = Annotated[int, Query(ge=1, le=100)]
_OFFSET = Annotated[int, Query(ge=0, le=2**31 - 1)]
router = APIRouter(prefix="/api/v1/agents/{agent_id}/skills", tags=["skills"])


@router.get("", response_model=list[Skill])
def list_skills(
    agent_id: UUID, request: Request, session: _SESSION, limit: _LIMIT = 50, offset: _OFFSET = 0
):
    return SkillService(request.app.state.database).list(agent_id, limit=limit, offset=offset)


@router.post("", response_model=Skill)
def create(agent_id: UUID, body: CreateSkill, request: Request, session: _SESSION):
    return SkillService(request.app.state.database).create(agent_id, body)


@router.post("/import", response_model=Skill)
def import_skill(agent_id: UUID, body: ImportSkill, request: Request, session: _SESSION):
    return SkillService(request.app.state.database).import_skill(agent_id, body)


@router.post("/from-process", response_model=Skill)
def from_process(agent_id: UUID, body: CreateProcessSkill, request: Request, session: _SESSION):
    return SkillService(request.app.state.database).create_from_process(agent_id, body)


@router.get("/{skill_id}/revisions/{version}/process", response_model=SkillProcessOrigin | None)
def process_origin(
    agent_id: UUID,
    skill_id: UUID,
    version: Annotated[int, Path(ge=1, le=2**31 - 1)],
    request: Request,
    session: _SESSION,
):
    return SkillService(request.app.state.database).process_origin(agent_id, skill_id, version)


@router.get("/{skill_id}", response_model=Skill)
def get_skill(agent_id: UUID, skill_id: UUID, request: Request, session: _SESSION):
    return SkillService(request.app.state.database).get(agent_id, skill_id)


@router.post("/{skill_id}/revisions", response_model=Skill)
def revise(agent_id: UUID, skill_id: UUID, body: ReviseSkill, request: Request, session: _SESSION):
    return SkillService(request.app.state.database).revise(agent_id, skill_id, body)


@router.get("/{skill_id}/revisions", response_model=list[SkillRevision])
def revisions(
    agent_id: UUID,
    skill_id: UUID,
    request: Request,
    session: _SESSION,
    limit: _LIMIT = 50,
    offset: _OFFSET = 0,
):
    return SkillService(request.app.state.database).revisions(
        agent_id, skill_id, limit=limit, offset=offset
    )


@router.get("/{skill_id}/revisions/{version}/export", response_model=SkillDocument)
def export(
    agent_id: UUID,
    skill_id: UUID,
    version: Annotated[int, Path(ge=1, le=2**31 - 1)],
    request: Request,
    session: _SESSION,
):
    return SkillService(request.app.state.database).export(agent_id, skill_id, version)


@router.put("/{skill_id}/enabled", response_model=Skill)
def enable(agent_id: UUID, skill_id: UUID, body: EnableSkill, request: Request, session: _SESSION):
    return SkillService(request.app.state.database).enable(agent_id, skill_id, body)
