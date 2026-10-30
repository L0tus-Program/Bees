"""Revisões imutáveis, intenção humana idempotente e importação sem execução."""

from concurrent.futures import ThreadPoolExecutor
from importlib import resources
from uuid import UUID, uuid4

import apsw
import pytest
from pydantic import ValidationError

from bees_core.models import Agent, Memory
from bees_core.skills import (
    CreateSkill,
    EnableSkill,
    ImportSkill,
    ReviseSkill,
    SkillDefinition,
    SkillDocument,
    SkillService,
)
from bees_core.storage import database as database_module
from bees_core.storage.database import Database, MigrationError
from bees_core.storage.store import NotFoundError, RevisionConflict, StateStore


def definition(name="Pesquisa com fontes"):
    return SkillDefinition(
        name=name,
        purpose="Comparar alternativas usando fontes verificáveis.",
        inputs=[{"name": "tema", "description": "Tema escolhido pelo usuário."}],
        steps=[{"title": "Conferir fontes", "instruction": "Registrar URL e data de consulta."}],
        resources={"environment": "own", "tools": ["browser.read"]},
        output="Relatório editável com fontes.",
        completion="Cada afirmação tem uma fonte conferida.",
    )


@pytest.fixture
def state(tmp_path):
    db = Database(tmp_path / "bees.sqlite3")
    db.initialize()
    with StateStore(db).transaction() as unit:
        agent = unit.agents.create(Agent(name="Teste"))
        other = unit.agents.create(Agent(name="Outra"))
    return db, agent, other, SkillService(db)


def create(service, agent, name="Pesquisa com fontes"):
    return service.create(
        agent.id, CreateSkill(client_request_id=uuid4(), definition=definition(name))
    )


def test_history_survives_restart_and_edit_disables_only_current_revision(state):
    db, agent, _, service = state
    first = create(service, agent)
    assert first.current_version == 1 and first.enabled_version is None
    enabled = service.enable(
        agent.id,
        first.id,
        EnableSkill(
            client_request_id=uuid4(),
            expected_revision=1,
            version=1,
            enabled=True,
        ),
    )
    assert enabled.revision == 2 and enabled.enabled_version == 1
    edited = service.revise(
        agent.id,
        first.id,
        ReviseSkill(
            client_request_id=uuid4(),
            expected_revision=2,
            definition=definition("Nova revisão"),
        ),
    )
    assert edited.revision == 3 and edited.current_version == 2 and edited.enabled_version is None
    reopened = SkillService(Database(db.path))
    assert reopened.get(agent.id, first.id) == edited
    history = reopened.revisions(agent.id, first.id)
    assert [item.version for item in history] == [2, 1]
    assert history[1] == first.current and history[1].origin == "human"
    assert history[0].content_hash != history[1].content_hash
    with pytest.raises(RevisionConflict):
        service.enable(
            agent.id,
            first.id,
            EnableSkill(
                client_request_id=uuid4(),
                expected_revision=3,
                version=1,
                enabled=True,
            ),
        )


def test_exact_replay_returns_original_snapshot_without_reactivation(state):
    _, agent, other, service = state
    request = CreateSkill(client_request_id=uuid4(), definition=definition())
    first = service.create(agent.id, request)
    assert service.create(agent.id, request) == first
    enable = EnableSkill(client_request_id=uuid4(), expected_revision=1, version=1, enabled=True)
    enabled = service.enable(agent.id, first.id, enable)
    changed = service.revise(
        agent.id,
        first.id,
        ReviseSkill(
            client_request_id=uuid4(),
            expected_revision=2,
            definition=definition("Mudou"),
        ),
    )
    assert service.enable(agent.id, first.id, enable) == enabled
    assert service.get(agent.id, first.id) == changed
    assert service.create(agent.id, request) == first
    for attempt in (
        lambda: service.create(other.id, request),
        lambda: service.create(
            agent.id, request.model_copy(update={"definition": definition("Outra")})
        ),
        lambda: service.enable(agent.id, first.id, enable.model_copy(update={"enabled": False})),
        lambda: service.revise(
            agent.id,
            first.id,
            ReviseSkill(
                client_request_id=uuid4(),
                expected_revision=1,
                definition=definition("Antiga"),
            ),
        ),
    ):
        with pytest.raises(RevisionConflict):
            attempt()
    assert len(service.list(agent.id)) == 1


def test_cas_serializes_concurrent_edits(state):
    db, agent, _, service = state
    first = create(service, agent)

    def write(index):
        try:
            return (
                SkillService(Database(db.path))
                .revise(
                    agent.id,
                    first.id,
                    ReviseSkill(
                        client_request_id=uuid4(),
                        expected_revision=1,
                        definition=definition(str(index)),
                    ),
                )
                .revision
            )
        except RevisionConflict:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(write, range(2)), key=str) == [2, "conflict"]
    assert len(service.revisions(agent.id, first.id)) == 2


def test_failed_audit_rolls_back_skill_revision_and_idempotency_command(state):
    db, agent, _, service = state
    with db.transaction() as connection:
        connection.execute(
            "CREATE TRIGGER fail_skill_audit BEFORE INSERT ON skill_events "
            "BEGIN SELECT RAISE(ABORT,'test storage failure'); END;"
        )
    with pytest.raises(apsw.ConstraintError, match="test storage failure"):
        create(service, agent)
    assert service.list(agent.id) == []
    with db.transaction(write=False) as connection:
        for table in ("skills", "skill_revisions", "skill_commands", "skill_events"):
            assert connection.execute(f"SELECT count(*) FROM {table}").get == 0


def test_roundtrip_import_has_explicit_scope_and_no_memory_grants_or_activation(state):
    db, agent, other, service = state
    with StateStore(db).transaction() as unit:
        memory = unit.memories.create(
            Memory(scope="agent", agent_id=agent.id, content="Preferência")
        )
    first = create(service, agent)
    document = service.export(agent.id, first.id, 1)
    request = ImportSkill(client_request_id=uuid4(), document=document)
    imported = service.import_skill(other.id, request)
    assert imported.id != first.id and imported.agent_id == other.id
    assert imported.current.definition == first.current.definition
    assert imported.current.origin == "import" and imported.enabled_version is None
    assert service.import_skill(other.id, request) == imported
    assert service.export(other.id, imported.id, 1) == document
    assert set(document.model_dump()) == {"format", "scope", "definition"}
    with StateStore(db).transaction(write=False) as unit:
        assert unit.memories.get(memory.id) == memory
    with db.transaction(write=False) as connection:
        assert connection.execute("SELECT count(*) FROM tool_grants").get == 0
        assert connection.execute("SELECT count(*) FROM tasks").get == 0
        events = connection.execute("SELECT kind,actor FROM skill_events").fetchall()
        assert events == [("created", "user"), ("created", "user")]


@pytest.mark.parametrize(
    "extra",
    [
        {"command": "touch /tmp/not-authorized"},
        {"script": "print('not executed')"},
        {"grants": ["filesystem.write"]},
        {"secret_ref": "env:PRIVATE"},
    ],
)
def test_executable_or_authority_fields_are_rejected(extra):
    with pytest.raises(ValidationError):
        SkillDefinition.model_validate(definition().model_dump() | extra)
    with pytest.raises(ValidationError):
        SkillDocument.model_validate(
            {
                "format": "bees.skill/v1",
                "scope": "agent",
                "definition": definition().model_dump(),
                **extra,
            }
        )


@pytest.mark.parametrize(
    "change",
    [
        {"name": " "},
        {"purpose": "x\x00y"},
        {"output": "x" * 2049},
        {"steps": []},
        {"steps": [{"title": " ", "instruction": "lido"}]},
        {"steps": [{"title": "x", "instruction": "x" * 2000}] * 30},
        {"purpose": "sk-or-v1-" + "a" * 64},
        {"output": "-----BEGIN PRIVATE KEY-----"},
        {"inputs": [{"name": "tema", "description": "Fonte"}] * 2},
        {"resources": {"environment": "personal", "tools": ["browser.read"] * 2}},
        {"resources": {"environment": "system", "tools": []}},
        {"steps": [{"title": "x", "instruction": "y", "code": "exec()"}]},
    ],
)
def test_invalid_oversize_and_credential_inputs_are_rejected(change):
    with pytest.raises(ValidationError):
        SkillDefinition.model_validate(definition().model_dump() | change)


def test_binding_pagination_and_immutable_rows(state):
    db, agent, other, service = state
    skills = [create(service, agent, str(index)) for index in range(3)]
    assert service.list(agent.id, limit=2) == skills[:2]
    assert service.list(agent.id, limit=2, offset=2) == skills[2:]
    for limit, offset in ((0, 0), (101, 0), (True, 0), (1, -1), (1, True)):
        with pytest.raises(ValueError):
            service.list(agent.id, limit=limit, offset=offset)
    for read in (
        lambda: service.get(other.id, skills[0].id),
        lambda: service.export(other.id, skills[0].id, 1),
        lambda: service.revisions(other.id, skills[0].id),
        lambda: service.list(uuid4()),
    ):
        with pytest.raises(NotFoundError):
            read()
    with pytest.raises(ValidationError):
        CreateSkill(client_request_id=UUID(int=0), definition=definition())
    for table in ("skill_revisions", "skill_commands", "skill_events"):
        with db.transaction() as connection:
            with pytest.raises(apsw.ConstraintError):
                connection.execute(f"DELETE FROM {table}")
    with db.transaction() as connection:
        with pytest.raises(apsw.ConstraintError):
            connection.execute("UPDATE skill_revisions SET origin='import'")
        with pytest.raises(apsw.ConstraintError):
            connection.execute("UPDATE skills SET agent_id=?", (str(other.id),))


@pytest.mark.parametrize("failure", [False, True])
def test_migration_from_13_preserves_existing_state_and_rolls_back(tmp_path, monkeypatch, failure):
    sources = resources.files("bees_core.storage.migrations")
    folder = tmp_path / "migrations"
    folder.mkdir()
    for file in sources.iterdir():
        if file.name.endswith(".sql") and int(file.name[:4]) <= 13:
            (folder / file.name).write_bytes(file.read_bytes())
    monkeypatch.setattr(database_module.resources, "files", lambda _: folder)
    db = Database(tmp_path / "state.sqlite3")
    db.initialize()
    with StateStore(db).transaction() as unit:
        agent = unit.agents.create(Agent(name="Preservada"))
        memory = unit.memories.create(Memory(scope="agent", agent_id=agent.id, content="Fato"))
    sql = (sources / "0014_skills.sql").read_text()
    path = folder / "0014_skills.sql"
    path.write_text(sql + ("\nINSERT INTO nonexistent VALUES(1);" if failure else ""))
    if failure:
        with pytest.raises(MigrationError):
            db.initialize()
        assert db.schema_version() == 13
        with db.transaction(write=False) as connection:
            assert (
                connection.execute("SELECT name FROM sqlite_schema WHERE name='skills'").get is None
            )
        path.write_text(sql)
    db.initialize()
    assert db.schema_version() == 14
    with StateStore(db).transaction(write=False) as unit:
        assert unit.agents.get(agent.id) == agent and unit.memories.get(memory.id) == memory
    assert SkillService(db).list(agent.id) == []
