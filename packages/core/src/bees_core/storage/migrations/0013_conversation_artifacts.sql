-- Escopo canônico de conversa, origem imutável e intenções humanas duráveis.
-- Rebuild fechado: nenhuma alteração nos blobs, UUIDs, revisões ou eventos existentes.
CREATE TABLE artifacts_new (
    id TEXT PRIMARY KEY NOT NULL,
    agent_id TEXT NOT NULL REFERENCES agents(id) ON DELETE RESTRICT,
    conversation_id TEXT,
    task_id TEXT,
    source TEXT NOT NULL CHECK(source IN
        ('legacy_unknown','system_generated','user_upload','user_revision')),
    run_id TEXT,
    name TEXT NOT NULL CHECK(length(trim(name)) > 0),
    media_type TEXT NOT NULL DEFAULT 'application/octet-stream',
    storage_key TEXT,
    sha256 TEXT CHECK(sha256 IS NULL OR (length(sha256)=64 AND sha256 NOT GLOB '*[^0-9a-f]*')),
    size_bytes INTEGER NOT NULL DEFAULT 0 CHECK(size_bytes >= 0),
    version INTEGER NOT NULL DEFAULT 1 CHECK(version > 0),
    status TEXT NOT NULL DEFAULT 'draft' CHECK(status IN ('draft','ready','failed','deleted')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    revision INTEGER NOT NULL DEFAULT 1 CHECK(revision > 0),
    metadata_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(metadata_json) AND json_type(metadata_json)='object'),
    series_id TEXT CHECK(series_id IS NULL OR length(series_id)=36),
    previous_id TEXT REFERENCES artifacts_new(id) ON DELETE RESTRICT
        CHECK(previous_id IS NULL OR (series_id IS NOT NULL AND previous_id <> id)),
    CHECK(task_id IS NOT NULL OR conversation_id IS NOT NULL),
    CHECK(run_id IS NULL OR task_id IS NOT NULL),
    CHECK(source NOT IN ('user_upload','user_revision') OR
        (conversation_id IS NOT NULL AND run_id IS NULL)),
    CHECK(source<>'user_upload' OR (previous_id IS NULL AND version=1)),
    CHECK(source<>'user_revision' OR previous_id IS NOT NULL),
    FOREIGN KEY(task_id,agent_id) REFERENCES tasks(id,agent_id) ON DELETE RESTRICT,
    FOREIGN KEY(conversation_id,agent_id) REFERENCES conversations(id,agent_id) ON DELETE RESTRICT,
    FOREIGN KEY(run_id,task_id) REFERENCES runs(id,task_id) ON DELETE RESTRICT
) STRICT;

INSERT INTO artifacts_new
SELECT a.id,t.agent_id,t.conversation_id,a.task_id,'legacy_unknown',a.run_id,
       a.name,a.media_type,a.storage_key,a.sha256,a.size_bytes,a.version,a.status,
       a.created_at,a.updated_at,a.revision,a.metadata_json,a.series_id,a.previous_id
FROM artifacts a LEFT JOIN tasks t ON t.id=a.task_id;

-- A cópia nova já contém toda a linhagem. Desvincular apenas a tabela antiga
-- permite removê-la mantendo foreign_keys=ON (self-FK RESTRICT).
DROP TRIGGER artifacts_lineage_coherent;
DROP TRIGGER artifacts_lineage_immutable;
DROP TRIGGER artifacts_content_immutable;
DROP TRIGGER artifacts_ready_immutable;
DROP TRIGGER artifacts_terminal_immutable;
DROP TRIGGER artifacts_no_delete;
UPDATE artifacts SET previous_id=NULL;
DROP TABLE artifacts;
ALTER TABLE artifacts_new RENAME TO artifacts;

CREATE INDEX idx_artifacts_task_date ON artifacts(task_id,created_at,id);
CREATE INDEX idx_artifacts_conversation_date ON artifacts(conversation_id,created_at,id);
CREATE INDEX idx_artifacts_agent_date ON artifacts(agent_id,created_at,id);
CREATE INDEX idx_artifacts_run ON artifacts(run_id);
CREATE UNIQUE INDEX idx_artifacts_series_version ON artifacts(series_id,version)
    WHERE series_id IS NOT NULL AND status <> 'failed';
CREATE UNIQUE INDEX idx_artifacts_storage_key ON artifacts(storage_key)
    WHERE storage_key IS NOT NULL;
CREATE INDEX idx_artifacts_previous ON artifacts(previous_id) WHERE previous_id IS NOT NULL;

CREATE TRIGGER artifacts_scope_coherent BEFORE INSERT ON artifacts
WHEN NEW.task_id IS NOT NULL AND NOT EXISTS(
    SELECT 1 FROM tasks t WHERE t.id=NEW.task_id AND t.agent_id=NEW.agent_id
        AND t.conversation_id IS NEW.conversation_id)
BEGIN SELECT RAISE(ABORT,'artifact scope is inconsistent'); END;
CREATE TRIGGER artifacts_lineage_coherent BEFORE INSERT ON artifacts
WHEN NEW.series_id IS NOT NULL AND (
    (NEW.previous_id IS NULL AND (NEW.series_id <> NEW.id OR NEW.version <> 1))
    OR (NEW.previous_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM artifacts p WHERE p.id=NEW.previous_id AND p.agent_id=NEW.agent_id
          AND p.task_id IS NEW.task_id AND p.conversation_id IS NEW.conversation_id
          AND p.status='ready' AND p.version+1=NEW.version
          AND (p.series_id=NEW.series_id OR (p.series_id IS NULL AND p.id=NEW.series_id)))))
BEGIN SELECT RAISE(ABORT,'artifact lineage is inconsistent'); END;
CREATE TRIGGER artifacts_lineage_immutable BEFORE UPDATE ON artifacts
WHEN NEW.id IS NOT OLD.id OR NEW.created_at IS NOT OLD.created_at
 OR NEW.agent_id IS NOT OLD.agent_id OR NEW.conversation_id IS NOT OLD.conversation_id
 OR NEW.task_id IS NOT OLD.task_id OR NEW.source IS NOT OLD.source OR NEW.run_id IS NOT OLD.run_id
 OR NEW.version IS NOT OLD.version OR NEW.series_id IS NOT OLD.series_id
 OR NEW.previous_id IS NOT OLD.previous_id
 OR (OLD.storage_key IS NOT NULL AND NEW.storage_key IS NOT OLD.storage_key)
BEGIN SELECT RAISE(ABORT,'artifact identity is immutable'); END;
CREATE TRIGGER artifacts_content_immutable BEFORE UPDATE ON artifacts
WHEN OLD.sha256 IS NOT NULL AND (NEW.sha256 IS NOT OLD.sha256
 OR NEW.size_bytes IS NOT OLD.size_bytes OR NEW.media_type IS NOT OLD.media_type
 OR NEW.name IS NOT OLD.name)
BEGIN SELECT RAISE(ABORT,'declared artifact content is immutable'); END;
CREATE TRIGGER artifacts_ready_immutable BEFORE UPDATE ON artifacts
WHEN OLD.status='ready' AND NEW.status NOT IN ('ready','deleted')
BEGIN SELECT RAISE(ABORT,'ready artifact content is immutable'); END;
CREATE TRIGGER artifacts_terminal_immutable BEFORE UPDATE ON artifacts
WHEN OLD.status IN ('failed','deleted')
BEGIN SELECT RAISE(ABORT,'terminal artifact is immutable'); END;
CREATE TRIGGER artifacts_no_delete BEFORE DELETE ON artifacts
BEGIN SELECT RAISE(ABORT,'artifact rows are append-only'); END;

CREATE TABLE artifact_commands (
    client_request_id TEXT PRIMARY KEY NOT NULL CHECK(length(client_request_id)=36),
    agent_id TEXT NOT NULL REFERENCES agents(id) ON DELETE RESTRICT,
    artifact_id TEXT NOT NULL REFERENCES artifacts(id) ON DELETE RESTRICT,
    kind TEXT NOT NULL CHECK(kind IN ('upload','revise','abandon')),
    request_hash TEXT NOT NULL CHECK(length(request_hash)=64 AND request_hash NOT GLOB '*[^0-9a-f]*'),
    created_at TEXT NOT NULL,
    actor TEXT NOT NULL CHECK(actor='user')
) STRICT;
CREATE INDEX idx_artifact_commands_artifact ON artifact_commands(artifact_id,created_at);
CREATE TRIGGER artifact_command_binding BEFORE INSERT ON artifact_commands
WHEN NOT EXISTS(SELECT 1 FROM artifacts a WHERE a.id=NEW.artifact_id AND a.agent_id=NEW.agent_id
    AND (NEW.kind='abandon' OR (a.id=NEW.client_request_id
        AND ((NEW.kind='upload' AND a.source='user_upload')
             OR (NEW.kind='revise' AND a.source='user_revision')))))
BEGIN SELECT RAISE(ABORT,'artifact command binding is invalid'); END;
CREATE TRIGGER artifact_commands_immutable BEFORE UPDATE ON artifact_commands
BEGIN SELECT RAISE(ABORT,'artifact commands are immutable'); END;
CREATE TRIGGER artifact_commands_no_delete BEFORE DELETE ON artifact_commands
BEGIN SELECT RAISE(ABORT,'artifact commands are append-only'); END;
