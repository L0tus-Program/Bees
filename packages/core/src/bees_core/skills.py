"""Biblioteca humana de procedimentos declarativos; salvar/habilitar não executa ações."""

import hashlib
import json
import re
from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from bees_core.models import utc_now
from bees_core.storage.database import Database
from bees_core.storage.store import IntegrityError, NotFoundError, RevisionConflict, StateStore

MAX_DOCUMENT_BYTES = 32768
Text = Annotated[str, Field(strict=True, min_length=1, max_length=2048)]
Name = Annotated[str, Field(strict=True, min_length=1, max_length=100)]
Identifier = Annotated[str, Field(strict=True, pattern=r"^[a-z][a-z0-9_.-]{0,63}$")]
# Defesa adicional para tokens conhecidos. Não substitui a revisão humana do conteúdo.
_SECRET = re.compile(
    r"(?:sk-(?:or-v1-)?[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9_]{20,}"
    r"|github_pat_[A-Za-z0-9_]{20,}|Bearer\s+[A-Za-z0-9_.-]{20,}"
    r"|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----)",
    re.IGNORECASE,
)


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    @field_validator("*", mode="before")
    @classmethod
    def safe_text(cls, value):
        if isinstance(value, str) and (
            not value.strip()
            or _SECRET.search(value)
            or any(ord(char) < 32 and char not in "\n\r\t" for char in value)
        ):
            raise ValueError("Texto inválido ou possível credencial; revise o procedimento.")
        return value


class SkillInput(Contract):
    name: Identifier
    description: Text
    required: bool = Field(default=True, strict=True)


class SkillStep(Contract):
    title: Name
    instruction: Text


class SkillResources(Contract):
    environment: Literal["none", "own", "personal"] = "none"
    tools: list[Identifier] = Field(default_factory=list, max_length=20)


class SkillDefinition(Contract):
    name: Name
    purpose: Text
    inputs: list[SkillInput] = Field(default_factory=list, max_length=20)
    steps: list[SkillStep] = Field(min_length=1, max_length=30)
    resources: SkillResources = Field(default_factory=SkillResources)
    output: Text
    completion: Text

    @model_validator(mode="after")
    def bounded_declaration(self):
        raw = self.model_dump_json()
        if len(raw.encode()) > 32000 or _SECRET.search(raw):
            raise ValueError("Procedimento excede o limite ou contém possível credencial.")
        if len({item.name for item in self.inputs}) != len(self.inputs):
            raise ValueError("Nomes de entrada devem ser únicos.")
        if len(set(self.resources.tools)) != len(self.resources.tools):
            raise ValueError("Ferramentas declaradas devem ser únicas.")
        for value in (self.name, self.purpose, self.output, self.completion):
            if not value.strip():
                raise ValueError("Os campos precisam de conteúdo.")
        for item in self.steps:
            if not item.title.strip() or not item.instruction.strip():
                raise ValueError("Cada passo precisa de título e instrução.")
        return self


class SkillDocument(Contract):
    format: Literal["bees.skill/v1"]
    scope: Literal["agent"]
    definition: SkillDefinition

    @model_validator(mode="after")
    def bounded_document(self):
        if len(self.model_dump_json().encode()) > MAX_DOCUMENT_BYTES:
            raise ValueError("Documento acima do limite.")
        return self


class Command(Contract):
    client_request_id: UUID

    @field_validator("client_request_id")
    @classmethod
    def non_nil(cls, value):
        if not value.int:
            raise ValueError("O identificador deve ser único e não nulo.")
        return value


class CreateSkill(Command):
    definition: SkillDefinition


class ImportSkill(Command):
    document: SkillDocument


class ReviseSkill(CreateSkill):
    expected_revision: int = Field(ge=1, strict=True)


class EnableSkill(Command):
    expected_revision: int = Field(ge=1, strict=True)
    version: int = Field(ge=1, strict=True)
    enabled: bool = Field(strict=True)


class SkillRevision(Contract):
    skill_id: UUID
    version: int
    definition: SkillDefinition
    content_hash: str
    origin: Literal["human", "import"]
    created_at: datetime


class Skill(Contract):
    id: UUID
    agent_id: UUID
    revision: int
    current_version: int
    enabled_version: int | None
    created_at: datetime
    updated_at: datetime
    current: SkillRevision


def _canonical(value: BaseModel) -> str:
    return json.dumps(value.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))


class SkillsRepository:
    """Repositório usado somente dentro da transação da instalação/abelha."""

    def __init__(self, connection, agent_id: UUID):
        self.connection, self.agent_id = connection, agent_id

    def revision(self, skill_id: UUID, version: int) -> SkillRevision:
        row = self.connection.execute(
            "SELECT r.version,r.definition_json,r.content_hash,r.origin,r.created_at "
            "FROM skill_revisions r JOIN skills s ON s.id=r.skill_id "
            "WHERE r.skill_id=? AND r.version=? AND s.agent_id=?",
            (str(skill_id), version, str(self.agent_id)),
        ).fetchone()
        if row is None:
            raise NotFoundError("Revisão da skill não encontrada nesta abelha.")
        if hashlib.sha256(row[1].encode()).hexdigest() != row[2]:
            raise IntegrityError("Integridade da revisão de skill inválida.")
        return SkillRevision(
            skill_id=skill_id,
            version=row[0],
            definition=json.loads(row[1]),
            content_hash=row[2],
            origin=row[3],
            created_at=row[4],
        )

    def get(self, skill_id: UUID) -> Skill:
        row = self.connection.execute(
            "SELECT revision,current_version,enabled_version,created_at,updated_at "
            "FROM skills WHERE id=? AND agent_id=?",
            (str(skill_id), str(self.agent_id)),
        ).fetchone()
        if row is None:
            raise NotFoundError("Skill não encontrada nesta abelha.")
        return Skill(
            id=skill_id,
            agent_id=self.agent_id,
            revision=row[0],
            current_version=row[1],
            enabled_version=row[2],
            created_at=row[3],
            updated_at=row[4],
            current=self.revision(skill_id, row[1]),
        )

    def append(self, skill_id, version, definition, origin, stamp):
        text = _canonical(definition)
        self.connection.execute(
            "INSERT INTO skill_revisions VALUES(?,?,?,?,?,?)",
            (
                str(skill_id),
                version,
                text,
                hashlib.sha256(text.encode()).hexdigest(),
                origin,
                stamp,
            ),
        )


class SkillService:
    def __init__(self, database: Database):
        self.store = StateStore(database)

    @staticmethod
    def _repository(unit, agent_id) -> SkillsRepository:
        agent_id = UUID(str(agent_id))
        if unit.agents.get(agent_id) is None:
            raise NotFoundError("Abelha não encontrada.")
        return SkillsRepository(unit.agents._context.connection, agent_id)

    @staticmethod
    def _page(limit, offset):
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("Página deve ter de 1 a 100 itens.")
        if type(offset) is not int or not 0 <= offset <= 2**31 - 1:
            raise ValueError("Deslocamento inválido.")

    def get(self, agent_id, skill_id) -> Skill:
        with self.store.transaction(write=False) as unit:
            return self._repository(unit, agent_id).get(UUID(str(skill_id)))

    def list(self, agent_id, *, limit=50, offset=0) -> list[Skill]:
        self._page(limit, offset)
        with self.store.transaction(write=False) as unit:
            repo = self._repository(unit, agent_id)
            ids = list(
                repo.connection.execute(
                    "SELECT id FROM skills WHERE agent_id=? "
                    "ORDER BY created_at,id LIMIT ? OFFSET ?",
                    (str(repo.agent_id), limit, offset),
                )
            )
            return [repo.get(UUID(row[0])) for row in ids]

    def revisions(self, agent_id, skill_id, *, limit=50, offset=0) -> list[SkillRevision]:
        self._page(limit, offset)
        with self.store.transaction(write=False) as unit:
            repo = self._repository(unit, agent_id)
            skill = repo.get(UUID(str(skill_id)))
            rows = list(
                repo.connection.execute(
                    "SELECT version FROM skill_revisions WHERE skill_id=? "
                    "ORDER BY version DESC LIMIT ? OFFSET ?",
                    (str(skill.id), limit, offset),
                )
            )
            return [repo.revision(skill.id, row[0]) for row in rows]

    def export(self, agent_id, skill_id, version: int) -> SkillDocument:
        if type(version) is not int or version < 1:
            raise ValueError("Versão inválida.")
        with self.store.transaction(write=False) as unit:
            repo = self._repository(unit, agent_id)
            revision = repo.revision(UUID(str(skill_id)), version)
            return SkillDocument(
                format="bees.skill/v1", scope="agent", definition=revision.definition
            )

    def _mutate(self, agent_id, skill_id, value, operation) -> Skill:
        target = operation + (":" + str(skill_id) if skill_id is not None else "")
        digest = hashlib.sha256(_canonical(value).encode()).hexdigest()
        with self.store.transaction(actor="user", source="skill_human") as unit:
            repo = self._repository(unit, agent_id)
            connection = repo.connection
            if skill_id is not None:
                previous = repo.get(skill_id)
            replay = connection.execute(
                "SELECT agent_id,target,request_hash,response_json FROM skill_commands "
                "WHERE client_request_id=?",
                (str(value.client_request_id),),
            ).fetchone()
            if replay is not None:
                if replay[:3] != (str(repo.agent_id), target, digest):
                    raise RevisionConflict("Identificador já usado para outra intenção de skill.")
                return Skill.model_validate_json(replay[3])
            stamp = utc_now().isoformat(timespec="microseconds")
            if skill_id is None:
                skill_id = uuid4()
                connection.execute(
                    "INSERT INTO skills VALUES(?,?,1,1,NULL,?,?)",
                    (str(skill_id), str(repo.agent_id), stamp, stamp),
                )
                definition = (
                    value.document.definition if operation == "import" else value.definition
                )
                repo.append(
                    skill_id, 1, definition, "import" if operation == "import" else "human", stamp
                )
                kind = "created"
            else:
                if previous.revision != value.expected_revision:
                    raise RevisionConflict("A skill mudou; atualize antes de editar.")
                if operation == "revise":
                    version = previous.current_version + 1
                    repo.append(skill_id, version, value.definition, "human", stamp)
                    enabled, kind = None, "revised"
                else:
                    if value.version != previous.current_version:
                        raise RevisionConflict("Habilitação exige a versão atual revisada.")
                    version = previous.current_version
                    enabled = version if value.enabled else None
                    kind = "enabled" if value.enabled else "disabled"
                connection.execute(
                    "UPDATE skills SET revision=revision+1,current_version=?,enabled_version=?,"
                    "updated_at=? WHERE id=? AND revision=?",
                    (version, enabled, stamp, str(skill_id), previous.revision),
                )
            result = repo.get(skill_id)
            connection.execute(
                "INSERT INTO skill_commands VALUES(?,?,?,?,?,?)",
                (
                    str(value.client_request_id),
                    str(repo.agent_id),
                    target,
                    digest,
                    result.model_dump_json(),
                    stamp,
                ),
            )
            connection.execute(
                "INSERT INTO skill_events(skill_id,revision,version,kind,client_request_id,"
                "created_at,actor) VALUES(?,?,?,?,?,?,'user')",
                (
                    str(skill_id),
                    result.revision,
                    result.current_version,
                    kind,
                    str(value.client_request_id),
                    stamp,
                ),
            )
            return result

    def create(self, agent_id, value: CreateSkill) -> Skill:
        return self._mutate(
            agent_id, None, CreateSkill.model_validate(value.model_dump()), "create"
        )

    def import_skill(self, agent_id, value: ImportSkill) -> Skill:
        return self._mutate(
            agent_id, None, ImportSkill.model_validate(value.model_dump()), "import"
        )

    def revise(self, agent_id, skill_id, value: ReviseSkill) -> Skill:
        return self._mutate(
            agent_id, UUID(str(skill_id)), ReviseSkill.model_validate(value.model_dump()), "revise"
        )

    def enable(self, agent_id, skill_id, value: EnableSkill) -> Skill:
        return self._mutate(
            agent_id, UUID(str(skill_id)), EnableSkill.model_validate(value.model_dump()), "enable"
        )
