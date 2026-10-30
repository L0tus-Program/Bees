"""Intenções humanas de arquivos: SQLite/blobs reais, sem modelo ou banco do usuário."""

import hashlib
from concurrent.futures import ThreadPoolExecutor
from importlib import resources
from uuid import uuid4

import pytest
from test_artifacts import REPORT, Crash, Setup

from bees_core.artifacts import ArtifactError, ArtifactStore
from bees_core.models import Conversation, Task, utc_now
from bees_core.storage import database as database_module
from bees_core.storage.database import Database, MigrationError
from bees_core.storage.store import IntegrityError, NotFoundError, StateStore


@pytest.fixture
def human(tmp_path):
    setup = Setup(tmp_path)
    with setup.store.store.transaction() as uow:
        setup.conversation = uow.conversations.create(Conversation(agent_id=setup.agent.id))
        setup.second_conversation = uow.conversations.create(Conversation(agent_id=setup.agent.id))
        setup.foreign_conversation = uow.conversations.create(Conversation(agent_id=setup.other.id))
    return setup


def upload(human, *, request=None, content=REPORT, **values):
    return human.store.upload(
        human.agent.id,
        human.conversation.id,
        client_request_id=request or uuid4(),
        name="relatório.md",
        media_type="text/markdown",
        content=content,
        **values,
    )


def snapshot(human):
    with human.database.transaction(write=False) as connection:
        return {
            name: connection.execute(f"SELECT * FROM {name} ORDER BY 1").fetchall()
            for name in ("artifacts", "artifact_commands", "domain_events")
        }


def test_upload_is_local_conversation_owned_and_replays_after_reopen(human):
    request = uuid4()
    first = upload(human, request=request)
    assert first.id == request and first.source == "user_upload"
    assert first.agent_id == human.agent.id and first.conversation_id == human.conversation.id
    assert first.task_id is first.run_id is None and first.status == "ready"
    before, files = snapshot(human), human.files()
    reopened = ArtifactStore(Database(human.database.path), human.root)
    assert (
        reopened.upload(
            human.agent.id,
            human.conversation.id,
            client_request_id=request,
            name=first.name,
            media_type=first.media_type,
            content=REPORT,
        )
        == first
    )
    assert reopened.read(human.agent.id, first.id) == (first, REPORT)
    assert reopened.list_conversation(human.agent.id, human.conversation.id) == [first]
    assert snapshot(human) == before and human.files() == files
    with human.database.transaction(write=False) as connection:
        assert connection.execute("SELECT count(*) FROM tasks").get == 2
        assert connection.execute(
            "SELECT DISTINCT actor FROM domain_events WHERE entity_type='artifact'"
        ).fetchall() == [("user",)]


@pytest.mark.parametrize("change", ["content", "conversation", "name", "media", "kind"])
def test_intent_uuid_cannot_be_rebound(human, change):
    request = uuid4()
    first = upload(human, request=request)
    before, files = snapshot(human), human.files()
    values = dict(
        client_request_id=request, name=first.name, media_type=first.media_type, content=REPORT
    )
    conversation = human.conversation.id
    if change == "content":
        values["content"] = b"outro"
    elif change == "conversation":
        conversation = human.second_conversation.id
    elif change == "name":
        values["name"] = "outro.md"
    elif change == "media":
        values["media_type"] = "text/plain"
    with pytest.raises(ArtifactError, match="artifact_conflict"):
        if change == "kind":
            human.store.abandon(
                human.agent.id,
                first.id,
                expected_revision=first.revision,
                client_request_id=request,
            )
        else:
            human.store.upload(human.agent.id, conversation, **values)
    assert snapshot(human) == before and human.files() == files


def test_guards_check_scope_and_metadata_without_draft_or_blob(human, monkeypatch):
    first = upload(human)
    before, files = snapshot(human), human.files()
    monkeypatch.setattr(
        ArtifactStore, "_read_verified", lambda *args: (_ for _ in ()).throw(Crash())
    )
    human.store.guard_upload(
        human.agent.id,
        human.conversation.id,
        client_request_id=first.id,
        name=first.name,
        media_type=first.media_type,
    )
    assert (
        human.store.guard_version(
            human.agent.id, human.conversation.id, first.id, expected_revision=first.revision
        )
        == first
    )
    with pytest.raises(NotFoundError):
        human.store.guard_upload(human.other.id, human.conversation.id)
    with pytest.raises(NotFoundError):
        human.store.guard_version(
            human.agent.id, human.second_conversation.id, first.id, expected_revision=first.revision
        )
    with pytest.raises(ArtifactError, match="artifact_conflict"):
        human.store.guard_upload(
            human.agent.id, human.conversation.id, client_request_id=first.id, name="alterado.md"
        )
    with pytest.raises(ArtifactError, match="artifact_version_conflict"):
        human.store.guard_version(
            human.agent.id, human.conversation.id, first.id, expected_revision=first.revision + 1
        )
    assert snapshot(human) == before and human.files() == files


def test_revision_has_human_cas_replay_and_single_concurrent_winner(human):
    first = upload(human)
    before = snapshot(human)
    with pytest.raises(ArtifactError, match="artifact_version_conflict"):
        human.store.revise(
            human.agent.id,
            human.conversation.id,
            first.id,
            client_request_id=uuid4(),
            expected_revision=first.revision + 1,
            name="v2.md",
            media_type=first.media_type,
            content=b"v2",
        )
    assert snapshot(human) == before

    def compete(content):
        try:
            return human.store.revise(
                human.agent.id,
                human.conversation.id,
                first.id,
                client_request_id=uuid4(),
                expected_revision=first.revision,
                name="v2.md",
                media_type=first.media_type,
                content=content,
            )
        except ArtifactError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(compete, [b"v2-A", b"v2-B"]))
    [winner] = [value for value in results if not isinstance(value, str)]
    assert winner.source == "user_revision" and winner.previous_id == first.id
    assert next(value for value in results if isinstance(value, str)) in (
        "artifact_version_conflict",
        "artifact_version_pending",
    )
    _, content = human.store.read(human.agent.id, winner.id)
    before = snapshot(human)
    assert (
        human.store.revise(
            human.agent.id,
            human.conversation.id,
            first.id,
            client_request_id=winner.id,
            expected_revision=first.revision,
            name=winner.name,
            media_type=winner.media_type,
            content=content,
        )
        == winner
    )
    with pytest.raises(ArtifactError, match="artifact_conflict"):
        human.store.revise(
            human.agent.id,
            human.conversation.id,
            first.id,
            client_request_id=winner.id,
            expected_revision=first.revision + 1,
            name=winner.name,
            media_type=winner.media_type,
            content=content,
        )
    assert snapshot(human) == before and human.store.read(human.agent.id, first.id)[1] == REPORT


@pytest.mark.parametrize("boundary", ["before_blob", "after_blob"])
def test_interrupted_human_publication_has_durable_intent_and_explicit_replay(
    human, monkeypatch, boundary
):
    request = uuid4()
    target = "_write_staging" if boundary == "before_blob" else "_read_verified"
    monkeypatch.setattr(ArtifactStore, target, lambda *args: (_ for _ in ()).throw(Crash()))
    with pytest.raises(Crash):
        upload(human, request=request)
    draft = human.row(request)
    assert draft.status == "draft" and draft.source == "user_upload"
    with human.store.store.transaction(write=False) as uow:
        assert uow.artifacts.command(request)[1] == str(draft.id)
    monkeypatch.undo()
    ready = upload(human, request=request)
    assert ready.id == draft.id and ready.status == "ready"
    assert human.store.read(human.agent.id, ready.id)[1] == REPORT


def test_abandon_replay_preserves_origin_and_files(human, monkeypatch):
    request = uuid4()
    monkeypatch.setattr(
        ArtifactStore, "_write_staging", lambda *args: (_ for _ in ()).throw(Crash())
    )
    with pytest.raises(Crash):
        upload(human, request=request)
    monkeypatch.undo()
    draft = human.row(request)
    command = uuid4()
    abandoned = human.store.abandon(
        human.agent.id, draft.id, expected_revision=draft.revision, client_request_id=command
    )
    before, files = snapshot(human), human.files()
    assert abandoned.source == "user_upload" and abandoned.status == "failed"
    assert (
        human.store.abandon(
            human.agent.id, draft.id, expected_revision=draft.revision, client_request_id=command
        )
        == abandoned
    )
    with pytest.raises(ArtifactError, match="artifact_conflict"):
        human.store.abandon(
            human.agent.id,
            draft.id,
            expected_revision=abandoned.revision,
            client_request_id=command,
        )
    assert snapshot(human) == before and human.files() == files


def test_task_conversation_binding_is_derived_and_source_is_sql_immutable(human):
    with human.store.store.transaction() as uow:
        task = uow.tasks.create(
            Task(
                agent_id=human.agent.id,
                conversation_id=human.conversation.id,
                title="Arquivo",
                objective="Entrada",
            )
        )
    first = upload(human)
    assert first.task_id == task.id and human.store.list(human.agent.id, task.id) == [first]
    with human.store.store.transaction() as uow:
        with pytest.raises(IntegrityError):
            uow.artifacts.update(
                first.model_copy(update={"source": "system_generated"}), first.revision
            )
        with pytest.raises(IntegrityError):
            uow.artifacts.update(
                first.model_copy(update={"conversation_id": human.second_conversation.id}),
                first.revision,
            )
    with human.database.transaction() as connection:
        import apsw

        with pytest.raises(apsw.ConstraintError):
            connection.execute(
                "UPDATE artifacts SET source='system_generated' WHERE id=?", (str(first.id),)
            )
        with pytest.raises(apsw.ConstraintError):
            connection.execute("DELETE FROM artifact_commands")


def test_legacy_constructor_cannot_override_conversation_binding(human):
    from bees_core.models import Artifact

    with human.store.store.transaction() as uow:
        with pytest.raises(IntegrityError):
            uow.artifacts.create(
                Artifact(
                    task_id=human.task.id, conversation_id=human.conversation.id, name="incoerente"
                )
            )


@pytest.mark.parametrize(
    "field,value",
    [
        ("name", "changed.md"),
        ("sha256", "b" * 64),
        ("media_type", "text/plain"),
        ("size_bytes", 1),
    ],
)
def test_declared_filename_and_content_are_immutable_in_repository_and_sql(human, field, value):
    import apsw

    first = upload(human)
    before = snapshot(human)
    with human.store.store.transaction() as uow:
        with pytest.raises(IntegrityError):
            uow.artifacts.update(first.model_copy(update={field: value}), first.revision)
    with human.database.transaction() as connection:
        with pytest.raises(apsw.ConstraintError):
            connection.execute(f"UPDATE artifacts SET {field}=? WHERE id=?", (value, str(first.id)))
    assert snapshot(human) == before


def test_version_intake_rejects_occupied_slot_and_accepts_exact_replay(human):
    first = upload(human)
    second = human.store.revise(
        human.agent.id,
        human.conversation.id,
        first.id,
        client_request_id=uuid4(),
        expected_revision=first.revision,
        name="v2.md",
        media_type="text/markdown",
        content=b"v2",
    )
    before = snapshot(human)
    with pytest.raises(ArtifactError, match="artifact_version_conflict"):
        human.store.guard_version(
            human.agent.id,
            human.conversation.id,
            first.id,
            client_request_id=uuid4(),
            expected_revision=first.revision,
        )
    assert (
        human.store.guard_version(
            human.agent.id,
            human.conversation.id,
            first.id,
            client_request_id=second.id,
            expected_revision=first.revision,
            name=second.name,
            media_type=second.media_type,
        )
        == first
    )
    with pytest.raises(ArtifactError, match="artifact_conflict"):
        human.store.guard_version(
            human.agent.id,
            human.conversation.id,
            first.id,
            client_request_id=second.id,
            expected_revision=first.revision + 1,
        )
    assert snapshot(human) == before


@pytest.mark.parametrize("fail", [False, True])
def test_schema12_upgrade_preserves_lineage_blobs_and_rolls_back_atomically(
    tmp_path, monkeypatch, fail
):
    sources = resources.files("bees_core.storage.migrations")
    folder = tmp_path / "migrations"
    folder.mkdir()
    for file in sources.iterdir():
        if file.name.endswith(".sql") and int(file.name[:4]) <= 12:
            (folder / file.name).write_bytes(file.read_bytes())
    monkeypatch.setattr(database_module.resources, "files", lambda _: folder)
    db = Database(tmp_path / "state.sqlite3")
    db.initialize()
    from bees_core.models import Agent

    with StateStore(db).transaction() as uow:
        agent = uow.agents.create(Agent(name="Legado"))
        conversation = uow.conversations.create(Conversation(agent_id=agent.id))
        task = uow.tasks.create(
            Task(
                agent_id=agent.id,
                conversation_id=conversation.id,
                title="Legado",
                objective="Preservar",
            )
        )
    ids = [uuid4() for _ in range(4)]
    root = tmp_path / "artifacts"
    (root / "blobs").mkdir(parents=True)
    for identity in ids[:2]:
        (root / "blobs" / str(identity)).write_bytes(REPORT)
    now = utc_now().isoformat()
    with db.transaction() as connection:
        for position, identity in enumerate(ids):
            connection.execute(
                "INSERT INTO artifacts(id,task_id,name,media_type,storage_key,sha256,size_bytes,"
                "version,status,created_at,updated_at,series_id,previous_id) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    str(identity),
                    str(task.id),
                    "legacy.md",
                    "text/markdown",
                    "blob/v1/" + str(identity),
                    hashlib.sha256(REPORT).hexdigest(),
                    len(REPORT),
                    min(position + 1, 3),
                    ["ready", "ready", "draft", "failed"][position],
                    now,
                    now,
                    None if position == 0 else str(ids[0]),
                    None if position == 0 else str(ids[0] if position == 1 else ids[1]),
                ),
            )
        columns = [
            column[0] for column in connection.execute("SELECT * FROM artifacts").description
        ]
        before = [
            dict(zip(columns, row, strict=True))
            for row in connection.execute("SELECT * FROM artifacts")
        ]
        events = connection.execute("SELECT * FROM domain_events").fetchall()
    migration = (sources / "0013_conversation_artifacts.sql").read_text()
    (folder / "0013_conversation_artifacts.sql").write_text(
        migration + ("\nINSERT INTO nonexistent VALUES(1);" if fail else ""), encoding="utf-8"
    )
    if fail:
        with pytest.raises(MigrationError):
            db.initialize()
        assert db.schema_version() == 12
        with db.transaction(write=False) as connection:
            assert [
                dict(zip(columns, row, strict=True))
                for row in connection.execute("SELECT * FROM artifacts")
            ] == before
        (folder / "0013_conversation_artifacts.sql").write_text(migration, encoding="utf-8")
    db.initialize()
    assert db.schema_version() == 13
    with db.transaction(write=False) as connection:
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert connection.execute("PRAGMA integrity_check").get == "ok"
        assert connection.execute("SELECT * FROM domain_events").fetchall() == events
        current_columns = [x[0] for x in connection.execute("SELECT * FROM artifacts").description]
        after = [
            dict(zip(current_columns, row, strict=True))
            for row in connection.execute("SELECT * FROM artifacts")
        ]
        assert [{key: row[key] for key in columns} for row in after] == before
        assert all(row["source"] == "legacy_unknown" for row in after)
        assert all(
            row["agent_id"] == str(agent.id) and row["conversation_id"] == str(conversation.id)
            for row in after
        )
    store = ArtifactStore(db, root)
    assert store.read(agent.id, ids[0])[1] == store.read(agent.id, ids[1])[1] == REPORT
    assert len(store.list_conversation(agent.id, conversation.id)) == 4
