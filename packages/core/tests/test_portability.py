"""Real SQLite/files: coherent private export, failures and hostile packages."""

import hashlib
import io
import json
import os
import zipfile
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest

from bees_core import portability as module
from bees_core.artifacts import ArtifactStore
from bees_core.models import Agent, Conversation, Memory, Task
from bees_core.portability import COLUMNS, EXCLUDED, ExportService, Limits, PortabilityError
from bees_core.skills import CreateSkill, SkillDefinition, SkillService
from bees_core.storage.database import Database
from bees_core.storage.store import StateStore


@pytest.fixture
def state(tmp_path):
    db = Database(tmp_path / "state.sqlite3")
    db.initialize()
    with StateStore(db).transaction() as unit:
        agent = unit.agents.create(
            Agent(
                name="Portável",
                provider_config={
                    "kind": "openai_compatible",
                    "endpoint": "https://model.example/v1",
                    "model": "test",
                    "secret_ref": "env:TEST_ONLY",
                    "capabilities": {"text": True, "tool_calls": False},
                },
            )
        )
        conversation = unit.conversations.create(Conversation(agent_id=agent.id))
        task = unit.tasks.create(
            Task(
                agent_id=agent.id, conversation_id=conversation.id, title="Test", objective="Export"
            )
        )
        unit.memories.create(Memory(agent_id=agent.id, scope="agent", content="Fontes próprias"))
    artifacts = ArtifactStore(db, tmp_path / "artifacts")
    artifact = artifacts.create(
        agent.id,
        task.id,
        name="fonte.md",
        media_type="text/markdown",
        content="# Fonte\nConteúdo com acentuação".encode(),
    )
    skill = SkillService(db).create(
        agent.id,
        CreateSkill(
            client_request_id=uuid4(),
            definition=SkillDefinition(
                name="Verificação",
                purpose="Conferir fontes",
                inputs=[],
                steps=[{"title": "Analisar", "instruction": "Conferir referências"}],
                resources={"environment": "none", "tools": []},
                output="Relatório",
                completion="Referências verificadas",
            ),
        ),
    )
    return db, agent, artifacts, artifact, skill, ExportService(db, artifacts, tmp_path / "exports")


def members(raw):
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


def repack(path, values):
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_STORED) as archive:
        for key, value in values.items():
            archive.writestr(key, value)


def test_complete_inventory_preserves_sources_skills_blobs_and_does_not_modify_state(state):
    db, agent, artifacts, artifact, skill, service = state
    with db.transaction(write=False) as connection:
        before = {table: list(connection.execute(f'SELECT * FROM "{table}"')) for table in COLUMNS}
    result = service.create()
    assert result.status == "ready" and result.blobs == 1
    path = service.root / "ready" / f"{result.id}.zip"
    raw = service.download(result.id)
    assert hashlib.sha256(raw).hexdigest() == result.sha256
    data = members(raw)
    manifest = service.verify(path)
    assert set(manifest["tables"]) == set(COLUMNS)
    assert set(manifest["excluded_tables"]) == EXCLUDED
    assert manifest["sanitized_fields"] >= 1
    assert json.loads(data["data/agents.jsonl"])["id"] == str(agent.id)
    config = json.loads(json.loads(data["data/agents.jsonl"])["model_config_json"])
    assert config["secret_ref"] is None and b"TEST_ONLY" not in raw
    revision = json.loads(data["data/skill_revisions.jsonl"])
    assert revision["content_hash"] == skill.current.content_hash
    assert (
        hashlib.sha256(revision["definition_json"].encode()).hexdigest() == revision["content_hash"]
    )
    assert data[f"blobs/{artifact.id}"] == artifacts.read(agent.id, artifact.id)[1]
    with db.transaction(write=False) as connection:
        after = {table: list(connection.execute(f'SELECT * FROM "{table}"')) for table in COLUMNS}
    assert before == after
    if os.name != "nt":
        assert path.stat().st_mode & 0o777 == 0o600
        assert path.parent.stat().st_mode & 0o777 == 0o700


@pytest.mark.parametrize("failure", ["missing", "corrupt"])
def test_blob_failure_has_explicit_error_and_no_ready_package(state, failure):
    _, _, artifacts, artifact, _, service = state
    path = artifacts.root / "blobs" / str(artifact.id)
    if failure == "missing":
        path.unlink()
    else:
        path.write_bytes(b"wrong")
    with pytest.raises(PortabilityError, match="portability_blob_"):
        service.create()
    assert not list((service.root / "ready").iterdir())
    assert len(list((service.root / "staging").glob("*/error.json"))) == 1


def test_concurrent_content_is_excluded_consistently_from_snapshot(state, monkeypatch):
    db, agent, artifacts, _, _, service = state
    original = module._read
    with ThreadPoolExecutor(max_workers=1) as executor:
        inserted = []

        def intercept(path, limit):
            if path.parent.name == "blobs" and not inserted:

                def writer():
                    with StateStore(db).transaction() as unit:
                        task = unit.tasks.create(
                            Task(agent_id=agent.id, title="Concurrent", objective="New data")
                        )
                    return artifacts.create(
                        agent.id,
                        task.id,
                        name="new.txt",
                        media_type="text/plain",
                        content=b"concurrent",
                    )

                inserted.append(executor.submit(writer).result(timeout=10))
            return original(path, limit)

        monkeypatch.setattr(module, "_read", intercept)
        result = service.create()
    data = members(service.download(result.id))
    assert inserted and f"blobs/{inserted[0].id}" not in data
    assert len(data["data/artifacts.jsonl"].splitlines()) == 1


def test_interruption_keeps_private_staging_without_publication(state, monkeypatch):
    *_, service = state

    def crash(*args):
        raise KeyboardInterrupt()

    monkeypatch.setattr(service, "verify", crash)
    with pytest.raises(KeyboardInterrupt):
        service.create()
    assert not list((service.root / "ready").iterdir())
    assert list((service.root / "staging").glob("*/package.part"))
    assert list((service.root / "staging").glob("*/error.json"))


@pytest.mark.parametrize(
    "mutate",
    ["extra", "missing", "hash", "graph", "version", "truncated", "compressed", "secret", "link"],
)
def test_closed_verification_rejects_hostile_packages(state, tmp_path, mutate):
    *_, service = state
    result = service.create()
    data = members(service.download(result.id))
    path = tmp_path / "hostile.zip"
    manifest = json.loads(data["manifest.json"])
    if mutate == "extra":
        data["../escape"] = b"untrusted"
    elif mutate == "missing":
        del data[next(name for name in data if name.startswith("blobs/"))]
    elif mutate == "hash":
        data["data/agents.jsonl"] += b" "
    elif mutate in {"graph", "secret"}:
        table = "tasks" if mutate == "graph" else "agents"
        record = json.loads(data[f"data/{table}.jsonl"])
        record["agent_id" if mutate == "graph" else "name"] = (
            str(uuid4()) if mutate == "graph" else "bp_" + "x" * 43
        )
        payload = json.dumps(record).encode() + b"\n"
        data[f"data/{table}.jsonl"] = payload
        manifest["tables"][table].update(
            bytes=len(payload), sha256=hashlib.sha256(payload).hexdigest()
        )
    elif mutate == "version":
        manifest["version"] = 999
    data["manifest.json"] = json.dumps(manifest).encode()
    repack(path, data)
    if mutate == "truncated":
        path.write_bytes(path.read_bytes()[:300])
    elif mutate == "compressed":
        with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for key, value in data.items():
                archive.writestr(key, value)
    elif mutate == "link":
        path.unlink()
        path.symlink_to(service.root / "ready" / f"{result.id}.zip")
    with pytest.raises(PortabilityError):
        service.verify(path)


def test_limits_unknown_schema_and_sensitive_logs_fail_closed(state):
    db, agent, artifacts, _, _, service = state
    with pytest.raises(PortabilityError, match="limit_exceeded"):
        ExportService(db, artifacts, service.root, limits=Limits(max_bytes=100)).create()
    with db.transaction() as connection:
        connection.execute("CREATE TABLE unreviewed_secret (payload TEXT)")
    with pytest.raises(PortabilityError, match="unsupported_schema"):
        service.create()
    with db.transaction() as connection:
        connection.execute("DROP TABLE unreviewed_secret")
    with StateStore(db).transaction() as unit:
        unit.memories.create(
            Memory(agent_id=agent.id, scope="agent", content="password=secret-test")
        )
    with pytest.raises(PortabilityError, match="sensitive_content"):
        service.create()
    assert not list((service.root / "ready").iterdir())


def test_private_operational_state_and_external_vault_files_never_enter_package(state, tmp_path):
    db, _, _, _, _, service = state
    # Values are synthetic and deliberately recognizable even if accidentally copied.
    with db.transaction() as connection:
        connection.execute(
            "INSERT INTO identity_users VALUES(1,?,?,0)",
            ("private-human-name", "private-password-hash"),
        )
        connection.execute("INSERT INTO identity_bootstrap VALUES(1,?,0,100,NULL)", ("a" * 64,))
        connection.execute(
            "INSERT INTO identity_sessions VALUES(?,1,?,0,100,NULL)", ("b" * 64, "c" * 43)
        )
        connection.execute(
            "INSERT INTO host_link_invites VALUES(?,?,?,0,100,NULL,NULL)",
            (str(uuid4()), str(uuid4()), "d" * 64),
        )
        connection.execute(
            "INSERT INTO cache_entries VALUES(?,?,?,?)",
            (
                "private-cache",
                json.dumps({"token": "bp_" + "x" * 43}),
                "2026-01-01T00:00:00+00:00",
                "2027-01-01T00:00:00+00:00",
            ),
        )
    (tmp_path / "vault.json").write_text("private-vault-secret bh_" + "y" * 43)
    (tmp_path / "bootstrap.log").write_text("private-bootstrap-secret")
    result = service.create()
    raw = service.download(result.id)
    for value in (
        "private-human-name",
        "private-password-hash",
        "a" * 64,
        "b" * 64,
        "c" * 43,
        "d" * 64,
        "private-cache",
        "private-vault-secret",
        "private-bootstrap-secret",
        "bp_" + "x" * 43,
        "bh_" + "y" * 43,
    ):
        assert value.encode() not in raw


@pytest.mark.parametrize("kind", ["max_files", "max_rows", "max_depth"])
def test_independent_configured_limits_are_enforced(state, kind):
    db, _, artifacts, _, _, service = state
    with pytest.raises(PortabilityError, match="limit_exceeded"):
        ExportService(db, artifacts, service.root, limits=Limits(**{kind: 1})).create()
    assert not list((service.root / "ready").iterdir())


def test_unknown_canonical_column_fails_closed(state):
    db, _, _, _, _, service = state
    with db.transaction() as connection:
        connection.execute("ALTER TABLE agents ADD COLUMN unreviewed_secret TEXT")
    with pytest.raises(PortabilityError, match="unsupported_schema"):
        service.create()


def test_structured_secrets_are_sanitized_and_tokens_budget_preserved(state):
    db, _, _, _, _, service = state
    with db.transaction() as connection:
        connection.execute(
            "UPDATE agents SET metadata_json=?",
            (
                json.dumps(
                    {
                        "source": "human",
                        "password_hash": "private-hash",
                        "cookies": ["private-cookie"],
                        "nested": {"api_key": "private-api-key", "max_tokens": 300},
                    }
                ),
            ),
        )
    result = service.create()
    data = members(service.download(result.id))
    metadata = json.loads(json.loads(data["data/agents.jsonl"])["metadata_json"])
    assert metadata == {
        "source": "human",
        "password_hash": None,
        "cookies": None,
        "nested": {"api_key": None, "max_tokens": 300},
    }


@pytest.mark.parametrize("prefix", ["bp_", "bh_", "bx_"])
@pytest.mark.parametrize("location", ["json_key", "nested_key", "value", "title"])
def test_tokens_in_json_keys_values_and_text_titles_are_never_exported(state, prefix, location):
    db, agent, _, _, _, service = state
    token = prefix + "A" * 43
    if location == "json_key":
        metadata = {token: "Synthetic non-secret value"}
    elif location == "nested_key":
        metadata = {"nested": [{token: "Synthetic non-secret value"}]}
    elif location == "value":
        metadata = {"note": token}
    else:
        metadata = {"title": json.dumps({token: "Synthetic title"})}
    with db.transaction() as connection:
        connection.execute(
            "UPDATE agents SET metadata_json=? WHERE id=?", (json.dumps(metadata), str(agent.id))
        )
    with pytest.raises(PortabilityError, match="portability_sensitive_content") as error:
        service.create()
    assert token not in str(error.value)
    assert not list((service.root / "ready").iterdir())
    for path in (service.root / "staging").glob("*/error.json"):
        assert token.encode() not in path.read_bytes()


@pytest.mark.parametrize(
    "case",
    [
        ("created_at", {}),
        ("created_at", "2026-01-01"),
        ("created_at", "2026-01-01T12:00:00"),
        ("created_at", "2026-02-30T12:00:00+00:00"),
        ("created_at", "2026-01-01T12:00:00+01:00"),
        ("created_at", 0),
        ("sanitized_fields", -1),
        ("sanitized_fields", True),
        ("sanitized_fields", "0"),
        ("content_policy", "unrecognized-policy"),
        ("content_policy", "bx_" + "A" * 43),
        ("id", "bad"),
        ("id", True),
        ("version", True),
        ("version", "1"),
        ("database_schema", True),
        ("database_schema", 0),
        ("database_schema", 1),
        ("database_schema", "17"),
        ("tables", []),
        ("blobs", []),
    ],
)
def test_manifest_scalars_and_containers_are_closed_and_strict(state, tmp_path, case):
    *_, service = state
    result = service.create()
    data = members(service.download(result.id))
    manifest = json.loads(data["manifest.json"])
    key, value = case
    manifest[key] = value
    data["manifest.json"] = json.dumps(manifest).encode()
    path = tmp_path / "bad-manifest.zip"
    repack(path, data)
    with pytest.raises(PortabilityError):
        service.verify(path)


@pytest.mark.parametrize(
    "section,key,value",
    [
        ("tables", "rows", True),
        ("tables", "rows", -1),
        ("tables", "rows", "1"),
        ("tables", "bytes", True),
        ("tables", "bytes", -1),
        ("tables", "bytes", "1"),
        ("tables", "sha256", "G" * 64),
        ("tables", "sha256", ["a" * 64]),
        ("blobs", "bytes", True),
        ("blobs", "bytes", -1),
        ("blobs", "bytes", "1"),
        ("blobs", "sha256", "G" * 64),
        ("blobs", "sha256", ["a" * 64]),
    ],
)
def test_manifest_inventory_fields_are_typed_and_bounded(state, tmp_path, section, key, value):
    *_, service = state
    result = service.create()
    data = members(service.download(result.id))
    manifest = json.loads(data["manifest.json"])
    item = next(iter(manifest[section].values()))
    item[key] = value
    data["manifest.json"] = json.dumps(manifest).encode()
    path = tmp_path / "bad-inventory.zip"
    repack(path, data)
    with pytest.raises(PortabilityError):
        service.verify(path)


@pytest.mark.parametrize(
    "key,value",
    [
        ("revision", True),
        ("revision", -1),
        ("revision", "1"),
        ("created_at", {}),
        ("created_at", "2026-01-01T12:00:00"),
        ("id", "bad"),
        ("name", []),
        ("metadata_json", "[]"),
        ("metadata_json", json.dumps({"bx_" + "A" * 43: "synthetic"})),
    ],
)
def test_record_types_and_secret_keys_are_rejected_even_with_consistent_hash(
    state, tmp_path, key, value
):
    *_, service = state
    result = service.create()
    data = members(service.download(result.id))
    manifest = json.loads(data["manifest.json"])
    record = json.loads(data["data/agents.jsonl"])
    record[key] = value
    data["data/agents.jsonl"] = json.dumps(record).encode() + b"\n"
    payload = data["data/agents.jsonl"]
    manifest["tables"]["agents"].update(
        bytes=len(payload), sha256=hashlib.sha256(payload).hexdigest()
    )
    data["manifest.json"] = json.dumps(manifest).encode()
    path = tmp_path / "bad-record.zip"
    repack(path, data)
    with pytest.raises(PortabilityError):
        service.verify(path)


def test_export_flush_uses_writable_file_handle_for_native_windows_contract(state, monkeypatch):
    if os.name != "posix":
        pytest.skip("Injeção de flags POSIX; exportações nativas são exercitadas no Windows.")
    import fcntl
    import stat

    original = os.fsync
    regular_flushes = []

    def flush(descriptor):
        if stat.S_ISREG(os.fstat(descriptor).st_mode):
            writable = fcntl.fcntl(descriptor, fcntl.F_GETFL) & os.O_ACCMODE != os.O_RDONLY
            regular_flushes.append(writable)
            if not writable:
                raise OSError("Native file flush requires a writable handle")
        return original(descriptor)

    monkeypatch.setattr(os, "fsync", flush)
    service = state[-1]
    exported = service.create()
    assert exported.status == "ready" and service.download(exported.id)
    assert regular_flushes and all(regular_flushes)
