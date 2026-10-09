"""ArtifactStore com SQLite e arquivos reais; falhas injetadas só nas fronteiras."""

import errno
import hashlib
import os
from importlib import resources
from pathlib import Path
from uuid import uuid4

import pytest

from bees_core import artifacts as module
from bees_core.artifacts import KEY_PREFIX, ArtifactError, ArtifactStore
from bees_core.models import Agent, Artifact, Run, Task, utc_now
from bees_core.storage.database import Database
from bees_core.storage.store import NotFoundError, StateStore

REPORT = "# Relatório\n\nConteúdo próprio de teste.\n".encode()


class Crash(BaseException):
    """Interrupção abrupta do processo publicador, sem tratamento de erro."""


class Setup:
    def __init__(self, tmp_path: Path):
        self.root = tmp_path / "artifacts"
        self.database = Database(tmp_path / "state.sqlite3")
        self.database.initialize()
        with StateStore(self.database).transaction() as uow:
            self.agent = uow.agents.create(Agent(name="Abelha de teste"))
            self.other = uow.agents.create(Agent(name="Outra abelha"))
            self.task = uow.tasks.create(
                Task(agent_id=self.agent.id, title="Relatório", objective="Gerar relatório")
            )
            self.other_task = uow.tasks.create(
                Task(agent_id=self.other.id, title="Outra", objective="Outra tarefa")
            )
            self.run = uow.runs.create(Run(task_id=self.task.id))
        self.store = ArtifactStore(self.database, self.root)

    def create(self, content=REPORT, **values):
        values = {"name": "relatório.md", "media_type": "text/markdown"} | values
        return self.store.create(self.agent.id, self.task.id, content=content, **values)

    def row(self, artifact_id):
        with StateStore(self.database).transaction(write=False) as uow:
            return uow.artifacts.get(artifact_id)

    def files(self):
        return sorted(
            (str(path.relative_to(self.root)), path.read_bytes())
            for path in self.root.rglob("*")
            if path.is_file()
        )


@pytest.fixture
def setup(tmp_path):
    return Setup(tmp_path)


def test_ready_only_after_verified_blob_and_survives_restart(setup):
    artifact = setup.create(run_id=setup.run.id)
    assert artifact.status == "ready" and artifact.version == 1
    assert artifact.sha256 == hashlib.sha256(REPORT).hexdigest()
    assert artifact.size_bytes == len(REPORT) and artifact.series_id == artifact.id
    assert artifact.storage_key == KEY_PREFIX + str(artifact.id)
    # Leitor independente: bytes no disco, sem passar pelo store.
    assert (setup.root / "blobs" / str(artifact.id)).read_bytes() == REPORT
    assert not list((setup.root / "staging").iterdir())
    restarted = ArtifactStore(Database(setup.database.path), setup.root)
    assert restarted.read(setup.agent.id, artifact.id) == (artifact, REPORT)
    assert restarted.list(setup.agent.id, setup.task.id) == [artifact]


def test_crash_before_blob_keeps_pending_draft_and_identical_replay_publishes(setup, monkeypatch):
    artifact_id = uuid4()

    def crash(*args):
        raise Crash

    monkeypatch.setattr(ArtifactStore, "_write_staging", crash)
    with pytest.raises(Crash):
        setup.create(artifact_id=artifact_id)
    draft = setup.row(artifact_id)
    assert draft.status == "draft" and draft.sha256 == hashlib.sha256(REPORT).hexdigest()
    assert setup.store.reconcile().pending == [artifact_id]
    monkeypatch.undo()
    with pytest.raises(ArtifactError, match="artifact_conflict"):
        setup.create(b"# Outro\n", artifact_id=artifact_id)
    assert setup.create(artifact_id=artifact_id).status == "ready"


def test_crash_after_blob_before_ready_is_reported_and_finished_without_duplicate(
    setup, monkeypatch
):
    artifact_id = uuid4()

    def crash(path):
        # Somente o fsync logo após a publicação do blob; diretórios novos passam.
        if path.name == "blobs":
            raise Crash

    monkeypatch.setattr(ArtifactStore, "_sync_directory", staticmethod(crash))
    with pytest.raises(Crash):
        setup.create(artifact_id=artifact_id)
    monkeypatch.undo()
    assert setup.row(artifact_id).status == "draft"
    before = setup.files()
    report = setup.store.reconcile()
    assert report.interrupted == [artifact_id] and setup.files() == before
    assert setup.row(artifact_id).status == "draft"
    ready = setup.create(artifact_id=artifact_id)
    assert ready.status == "ready" and ready.revision == 2
    assert setup.files() == [(f"blobs{os.sep}{artifact_id}", REPORT)]


def test_foreign_blob_at_target_fails_closed_and_keeps_evidence(setup, monkeypatch):
    artifact_id = uuid4()

    def crash(*args):
        raise Crash

    monkeypatch.setattr(ArtifactStore, "_write_staging", crash)
    with pytest.raises(Crash):
        setup.create(artifact_id=artifact_id)
    monkeypatch.undo()
    foreign = setup.root / "blobs" / str(artifact_id)
    foreign.parent.mkdir(parents=True, exist_ok=True)
    foreign.write_bytes(b"conteudo de outra origem")
    assert setup.store.reconcile().conflicting == [artifact_id]
    with pytest.raises(ArtifactError, match="artifact_integrity_failed"):
        setup.create(artifact_id=artifact_id)
    failed = setup.row(artifact_id)
    assert failed.status == "failed" and failed.metadata == {
        "error_code": "artifact_integrity_failed"
    }
    assert foreign.read_bytes() == b"conteudo de outra origem"
    assert setup.store.reconcile().failed_with_blob == [artifact_id]
    with pytest.raises(ArtifactError, match="artifact_terminal"):
        setup.create(artifact_id=artifact_id)


def test_disk_full_keeps_retryable_draft_without_partial_file(setup, monkeypatch):
    artifact_id = uuid4()

    def full(descriptor, data):
        raise OSError(errno.ENOSPC, "sem espaço de teste")

    monkeypatch.setattr(module.os, "write", full)
    with pytest.raises(ArtifactError, match="artifact_storage_unavailable"):
        setup.create(artifact_id=artifact_id)
    monkeypatch.undo()
    # Falha do ambiente não é terminal: nada parcial, rascunho pronto para repetição.
    assert setup.row(artifact_id).status == "draft"
    assert setup.files() == []
    assert setup.create(artifact_id=artifact_id).status == "ready"


def test_extra_link_from_interrupted_link_window_is_never_terminal(setup, monkeypatch):
    """Publicador morto entre link e unlink (POSIX) ou concorrente dentro da janela."""
    artifact_id = uuid4()

    def crash(*args):
        raise Crash

    monkeypatch.setattr(ArtifactStore, "_write_staging", crash)
    with pytest.raises(Crash):
        setup.create(artifact_id=artifact_id)
    monkeypatch.undo()
    blob = setup.root / "blobs" / str(artifact_id)
    blob.parent.mkdir(parents=True, exist_ok=True)
    (setup.root / "staging").mkdir(exist_ok=True)
    blob.write_bytes(REPORT)
    stale = setup.root / "staging" / f"{artifact_id}.interrompido.part"
    os.link(blob, stale)
    if os.name == "nt":
        # O protocolo Windows usa rename e não cria esse link; mesmo assim nunca é terminal.
        with pytest.raises(ArtifactError, match="artifact_integrity_failed"):
            setup.create(artifact_id=artifact_id)
        assert setup.row(artifact_id).status == "draft" and stale.exists()
        return
    assert setup.create(artifact_id=artifact_id).status == "ready"
    assert not stale.exists() and blob.read_bytes() == REPORT and blob.stat().st_nlink == 1


@pytest.mark.skipif(os.name != "nt", reason="Retentativa de rename é exclusiva do Windows.")
def test_windows_sharing_violation_is_retried_then_kept_retryable(setup, monkeypatch):
    original = module.os.rename
    calls = []

    def busy(source, target, *, until):
        calls.append(source)
        if len(calls) <= until:
            raise PermissionError(13, "arquivo em uso por outro processo", None, 32)
        return original(source, target)

    monkeypatch.setattr(module.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(module.os, "rename", lambda s, t: busy(s, t, until=2))
    assert setup.create().status == "ready" and len(calls) == 3
    calls.clear()
    artifact_id = uuid4()
    monkeypatch.setattr(module.os, "rename", lambda s, t: busy(s, t, until=99))
    with pytest.raises(ArtifactError, match="artifact_storage_unavailable"):
        setup.create(artifact_id=artifact_id)
    assert len(calls) == module._RENAME_ATTEMPTS
    assert setup.row(artifact_id).status == "draft"
    assert not list((setup.root / "staging").iterdir())
    monkeypatch.setattr(module.os, "rename", original)
    assert setup.create(artifact_id=artifact_id).status == "ready"


def test_replay_of_ready_is_idempotent_and_keeps_single_blob(setup):
    artifact_id = uuid4()
    first = setup.create(artifact_id=artifact_id)
    assert setup.create(artifact_id=artifact_id) == first
    assert setup.row(artifact_id).revision == first.revision
    assert setup.files() == [(f"blobs{os.sep}{artifact_id}", REPORT)]
    with pytest.raises(ArtifactError, match="artifact_conflict"):
        setup.create(artifact_id=artifact_id, name="outro-nome.md")


@pytest.mark.parametrize("change", ["same_size", "missing", "hardlink", "storage_key"])
def test_altered_missing_linked_or_rekeyed_blob_is_never_served(setup, change):
    artifact = setup.create()
    blob = setup.root / "blobs" / str(artifact.id)
    expected = "artifact_integrity_failed"
    if change == "same_size":
        blob.write_bytes(b"!" + REPORT[1:])
    elif change == "missing":
        blob.unlink()
        expected = "artifact_blob_missing"
    elif change == "hardlink":
        os.link(blob, setup.root / "alias")
    else:
        with setup.database.transaction() as connection:
            connection.execute(
                "DROP TRIGGER artifacts_lineage_immutable",
            )
            connection.execute(
                "UPDATE artifacts SET storage_key=? WHERE id=?",
                ("blob/v1/../../escape", str(artifact.id)),
            )
    with pytest.raises(ArtifactError, match=expected):
        setup.store.read(setup.agent.id, artifact.id)
    assert setup.store.reconcile().integrity_failed == (
        [] if change == "storage_key" else [artifact.id]
    )


def test_linked_storage_root_is_refused(tmp_path, setup):
    target = tmp_path / "elsewhere"
    target.mkdir()
    link = tmp_path / "linked-root"
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("Conta sem privilégio para criar symlink.")
    store = ArtifactStore(setup.database, link / "artifacts")
    with pytest.raises(ArtifactError, match="artifact_storage_unavailable"):
        store.create(
            setup.agent.id,
            setup.task.id,
            name="relatório.md",
            media_type="text/markdown",
            content=REPORT,
        )
    assert not any(target.iterdir())


def test_ids_from_other_agent_never_grant_access(setup):
    artifact = setup.create()
    with pytest.raises(NotFoundError):
        setup.store.read(setup.other.id, artifact.id)
    with pytest.raises(NotFoundError):
        setup.store.list(setup.other.id, setup.task.id)
    with pytest.raises(NotFoundError):
        setup.store.create_version(setup.other.id, artifact.id, content=b"# v2\n")
    with pytest.raises(NotFoundError):
        setup.store.create(
            setup.agent.id,
            setup.other_task.id,
            name="cruzado.md",
            media_type="text/markdown",
            content=REPORT,
        )


def test_versions_keep_previous_and_concurrent_writers_have_one_winner(setup):
    first = setup.create()
    second = setup.store.create_version(setup.agent.id, first.id, content=b"# v2\n")
    assert (second.version, second.series_id, second.previous_id) == (2, first.id, first.id)
    assert setup.store.read(setup.agent.id, first.id) == (first, REPORT)
    assert setup.store.read(setup.agent.id, second.id)[1] == b"# v2\n"
    # Outra versão a partir da mesma base perde para a já publicada; anterior intacta.
    with pytest.raises(ArtifactError, match="artifact_version_conflict"):
        setup.store.create_version(setup.agent.id, first.id, content=b"# v2 concorrente\n")
    third = setup.store.create_version(
        setup.agent.id, second.id, content=b"a,b\n1,2\n", media_type="text/csv", name="t.csv"
    )
    assert (third.version, third.series_id, third.previous_id) == (3, first.id, second.id)
    assert [item.version for item in setup.store.list(setup.agent.id, setup.task.id)] == [1, 2, 3]
    with StateStore(setup.database).transaction(write=False) as uow:
        assert uow.artifacts.list(series_id=first.id) == [first, second, third]


def test_interrupted_version_is_readopted_or_abandoned_without_locking_series(setup, monkeypatch):
    first = setup.create()

    def crash(*args):
        raise Crash

    monkeypatch.setattr(ArtifactStore, "_write_staging", crash)
    with pytest.raises(Crash):
        setup.store.create_version(setup.agent.id, first.id, content=b"# v2\n")
    monkeypatch.undo()
    with StateStore(setup.database).transaction(write=False) as uow:
        [pending] = [item for item in uow.artifacts.list(series_id=first.id) if item.version == 2]
    assert pending.status == "draft"
    # Outro conteúdo não ocupa a posição; o mesmo pedido readota o rascunho pelo id.
    with pytest.raises(ArtifactError, match="artifact_version_pending"):
        setup.store.create_version(setup.agent.id, first.id, content=b"# outra v2\n")
    readopted = setup.store.create_version(setup.agent.id, first.id, content=b"# v2\n")
    assert readopted.id == pending.id and readopted.status == "ready"
    # Abandono explícito libera a posição sem tocar arquivos; falha não ocupa versão.
    monkeypatch.setattr(ArtifactStore, "_write_staging", crash)
    with pytest.raises(Crash):
        setup.store.create_version(setup.agent.id, readopted.id, content=b"# v3\n")
    monkeypatch.undo()
    with StateStore(setup.database).transaction(write=False) as uow:
        [draft] = [item for item in uow.artifacts.list(series_id=first.id) if item.version == 3]
    with pytest.raises(NotFoundError):
        setup.store.abandon(setup.other.id, draft.id, expected_revision=draft.revision)
    abandoned = setup.store.abandon(setup.agent.id, draft.id, expected_revision=draft.revision)
    assert abandoned.status == "failed"
    assert abandoned.metadata == {"error_code": "artifact_abandoned"}
    with pytest.raises(ArtifactError, match="artifact_terminal"):
        setup.store.abandon(setup.agent.id, draft.id, expected_revision=abandoned.revision)
    third = setup.store.create_version(setup.agent.id, readopted.id, content=b"# outra v3\n")
    assert third.version == 3 and third.status == "ready"


def test_sql_triggers_protect_declared_content_lineage_and_history(setup, monkeypatch):
    artifact = setup.create()
    draft_id = uuid4()

    def crash(*args):
        raise Crash

    monkeypatch.setattr(ArtifactStore, "_write_staging", crash)
    with pytest.raises(Crash):
        setup.create(artifact_id=draft_id)
    monkeypatch.undo()
    statements = [
        ("UPDATE artifacts SET sha256=? WHERE id=?", ("b" * 64, str(artifact.id))),
        ("UPDATE artifacts SET status='draft' WHERE id=?", (str(artifact.id),)),
        ("UPDATE artifacts SET version=7 WHERE id=?", (str(artifact.id),)),
        ("UPDATE artifacts SET storage_key='blob/v1/x' WHERE id=?", (str(artifact.id),)),
        (
            "UPDATE artifacts SET created_at='2000-01-01T00:00:00+00:00' WHERE id=?",
            (str(artifact.id),),
        ),
        ("DELETE FROM artifacts WHERE id=?", (str(artifact.id),)),
        # Rascunho: conteúdo declarado também é imutável antes da publicação.
        ("UPDATE artifacts SET sha256=? WHERE id=?", ("b" * 64, str(draft_id))),
        ("UPDATE artifacts SET size_bytes=1 WHERE id=?", (str(draft_id),)),
        ("UPDATE artifacts SET media_type='text/plain' WHERE id=?", (str(draft_id),)),
    ]
    for sql, values in statements:
        with pytest.raises(Exception, match="immutable|append-only"):
            with setup.database.transaction() as connection:
                connection.execute(sql, values)
    assert setup.row(artifact.id) == artifact
    now = utc_now().isoformat()
    inserts = [
        # v1 precisa iniciar a própria série; versão anterior precisa estar pronta.
        (str(uuid4()), str(artifact.id), 1, None),
        (str(uuid4()), str(artifact.id), 3, str(artifact.id)),
        (str(uuid4()), str(draft_id), 2, str(draft_id)),
    ]
    for new_id, series, version, previous in inserts:
        with pytest.raises(Exception, match="lineage is inconsistent"):
            with setup.database.transaction() as connection:
                connection.execute(
                    "INSERT INTO artifacts(id,task_id,name,version,series_id,previous_id,"
                    "created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                    (new_id, str(setup.task.id), "x.md", version, series, previous, now, now),
                )


def test_csv_with_large_field_is_valid(setup):
    content = b'"' + b"x" * (200 * 1024) + b'",fim\n'
    artifact = setup.create(content, name="grande.csv", media_type="text/csv")
    assert setup.store.read(setup.agent.id, artifact.id)[1] == content


@pytest.mark.parametrize(
    ("media_type", "content", "name", "code"),
    [
        ("text/html", b"<b>x</b>", "a.html", "artifact_media_type_unsupported"),
        ("text/plain", b"\xff\xfe", "a.txt", "artifact_content_invalid"),
        ("text/plain", b"a\x00b", "a.txt", "artifact_content_invalid"),
        ("application/json", b'{"a":1,"a":2}', "a.json", "artifact_content_invalid"),
        ("application/json", b'{"a":NaN}', "a.json", "artifact_content_invalid"),
        ("text/csv", b'a,"b\n', "a.csv", "artifact_content_invalid"),
        ("text/plain", b"x" * 65, "a.txt", "artifact_too_large"),
        ("text/plain", b"ok", "../a.txt", "artifact_name_invalid"),
        ("text/plain", b"ok", "a‮txt", "artifact_name_invalid"),
        ("text/plain", b"ok", " a.txt", "artifact_name_invalid"),
    ],
)
def test_invalid_content_or_name_creates_nothing(tmp_path, media_type, content, name, code):
    setup = Setup(tmp_path)
    store = ArtifactStore(setup.database, setup.root, max_bytes=64)
    with pytest.raises(ArtifactError, match=code):
        store.create(
            setup.agent.id, setup.task.id, name=name, media_type=media_type, content=content
        )
    with StateStore(setup.database).transaction(write=False) as uow:
        assert uow.artifacts.list() == []
    assert not setup.root.exists()


def test_reconcile_reports_without_mutating_rows_or_files(setup):
    ready = setup.create()
    stray = setup.root / "staging" / "nao-reconhecido.part"
    stray.write_bytes(b"evidencia")
    orphan = setup.root / "blobs" / str(uuid4())
    orphan.write_bytes(b"orfao")
    with setup.database.transaction(write=False) as connection:
        rows = connection.execute("SELECT * FROM artifacts ORDER BY id").fetchall()
    files = setup.files()
    report = setup.store.reconcile()
    assert report.staging_files == ["nao-reconhecido.part"]
    assert report.orphan_blobs == [orphan.name]
    assert ready.id not in report.integrity_failed
    with setup.database.transaction(write=False) as connection:
        assert connection.execute("SELECT * FROM artifacts ORDER BY id").fetchall() == rows
    assert setup.files() == files


def test_upgrade_eight_to_nine_preserves_legacy_artifact_and_starts_lineage(tmp_path, monkeypatch):
    from bees_core.storage import database as database_module

    original = resources.files("bees_core.storage.migrations")
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    for file in original.iterdir():
        if file.name.endswith(".sql") and int(file.name[:4]) < 9:
            (migrations / file.name).write_bytes(file.read_bytes())
    monkeypatch.setattr(database_module.resources, "files", lambda _: migrations)
    database = Database(tmp_path / "legacy.sqlite3")
    database.initialize()
    now = utc_now().isoformat()
    agent_id, task_id, legacy_id = uuid4(), uuid4(), uuid4()
    # O software schema8 não conhece as colunas novas: SQL representa o escritor antigo.
    with database.transaction() as connection:
        connection.execute(
            "INSERT INTO agents(id,name,created_at,updated_at) VALUES(?,?,?,?)",
            (str(agent_id), "Legado", now, now),
        )
        connection.execute(
            "INSERT INTO tasks(id,agent_id,title,objective,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?)",
            (str(task_id), str(agent_id), "Legado", "Texto", now, now),
        )
        connection.execute(
            "INSERT INTO artifacts(id,task_id,name,storage_key,sha256,size_bytes,status,"
            "created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
            (
                str(legacy_id),
                str(task_id),
                "legado.md",
                "artifact/legado",
                "a" * 64,
                3,
                "ready",
                now,
                now,
            ),
        )
        before = connection.execute("SELECT * FROM artifacts").fetchall()
    (migrations / "0009_artifact_versions.sql").write_bytes(
        (original / "0009_artifact_versions.sql").read_bytes()
    )
    database.initialize()
    assert database.schema_version() == 9
    backups = list(tmp_path.glob("legacy.sqlite3.backup-*.sqlite3"))
    assert len(backups) == 1
    assert Database(backups[0]).schema_version() == 8
    with database.transaction(write=False) as connection:
        after = connection.execute("SELECT * FROM artifacts").fetchall()
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    assert [row[: len(before[0])] for row in after] == before
    with StateStore(database).transaction(write=False) as uow:
        legacy = uow.artifacts.get(legacy_id)
    assert legacy.series_id is None and legacy.previous_id is None
    store = ArtifactStore(database, tmp_path / "artifacts")
    # O legado não declara formato suportado; a nova versão escolhe um explicitamente.
    with pytest.raises(ArtifactError, match="artifact_media_type_unsupported"):
        store.create_version(agent_id, legacy_id, content=b"# v2\n")
    version = store.create_version(
        agent_id, legacy_id, content=b"# v2\n", media_type="text/markdown"
    )
    assert (version.version, version.series_id, version.previous_id) == (2, legacy_id, legacy_id)
    assert isinstance(version, Artifact) and version.status == "ready"
