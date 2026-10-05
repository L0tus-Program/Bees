-- Fila individual e journal de modelos, sem execução de ferramentas.
ALTER TABLE tasks ADD COLUMN submission_key TEXT;
ALTER TABLE tasks ADD COLUMN desired_state TEXT NOT NULL DEFAULT 'running'
    CHECK(desired_state IN ('running','paused','cancelled'));
ALTER TABLE tasks ADD COLUMN control_revision INTEGER NOT NULL DEFAULT 0 CHECK(control_revision>=0);
ALTER TABLE tasks ADD COLUMN max_calls INTEGER NOT NULL DEFAULT 3 CHECK(max_calls BETWEEN 1 AND 50);
ALTER TABLE tasks ADD COLUMN max_active_seconds INTEGER NOT NULL DEFAULT 120 CHECK(max_active_seconds BETWEEN 1 AND 1800);
ALTER TABLE tasks ADD COLUMN calls_started INTEGER NOT NULL DEFAULT 0 CHECK(calls_started>=0);
ALTER TABLE tasks ADD COLUMN active_milliseconds INTEGER NOT NULL DEFAULT 0 CHECK(active_milliseconds>=0);
ALTER TABLE runs ADD COLUMN model_config_json TEXT NOT NULL DEFAULT '{}'
    CHECK(json_valid(model_config_json) AND json_type(model_config_json)='object');
CREATE UNIQUE INDEX idx_task_submission_key ON tasks(submission_key) WHERE submission_key IS NOT NULL;

CREATE TABLE task_commands (
    id TEXT PRIMARY KEY NOT NULL,
    task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE RESTRICT,
    client_request_id TEXT NOT NULL,
    kind TEXT NOT NULL CHECK(kind IN ('pause','resume','cancel','redirect')),
    expected_revision INTEGER NOT NULL CHECK(expected_revision>0),
    payload_json TEXT NOT NULL CHECK(json_valid(payload_json) AND json_type(payload_json)='object'),
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
    revision INTEGER NOT NULL DEFAULT 1 CHECK(revision=1),
    metadata_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(metadata_json) AND json_type(metadata_json)='object'),
    UNIQUE(task_id,client_request_id)
) STRICT;

CREATE TABLE model_calls (
    id TEXT PRIMARY KEY NOT NULL,
    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE RESTRICT,
    ordinal INTEGER NOT NULL CHECK(ordinal>0),
    phase TEXT NOT NULL DEFAULT 'final' CHECK(phase IN ('final','draft','review')),
    status TEXT NOT NULL DEFAULT 'prepared' CHECK(status IN ('prepared','dispatch_started','confirmed','failed_no_effect','outcome_unknown','cancelled')),
    task_control_revision INTEGER NOT NULL CHECK(task_control_revision>=0),
    agent_revision INTEGER NOT NULL CHECK(agent_revision>0),
    lease_generation INTEGER NOT NULL CHECK(lease_generation>0),
    model_config_json TEXT NOT NULL CHECK(json_valid(model_config_json) AND json_type(model_config_json)='object'),
    request_json TEXT NOT NULL CHECK(json_valid(request_json) AND json_type(request_json)='object'),
    snapshot_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(snapshot_json) AND json_type(snapshot_json)='object'),
    request_hash TEXT NOT NULL CHECK(length(request_hash)=64 AND request_hash NOT GLOB '*[^0-9a-f]*'),
    response_json TEXT CHECK(response_json IS NULL OR (json_valid(response_json) AND json_type(response_json)='object')),
    output_message_id TEXT REFERENCES messages(id) ON DELETE RESTRICT,
    error_code TEXT, started_at TEXT, finished_at TEXT, unknown_acknowledged_at TEXT,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
    revision INTEGER NOT NULL DEFAULT 1 CHECK(revision>0),
    metadata_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(metadata_json) AND json_type(metadata_json)='object'),
    UNIQUE(run_id,ordinal),
    CHECK((status='confirmed')=(response_json IS NOT NULL)),
    CHECK(output_message_id IS NULL OR status='confirmed'),
    CHECK(unknown_acknowledged_at IS NULL OR status='outcome_unknown')
) STRICT;
CREATE INDEX idx_calls_run_status ON model_calls(run_id,status,ordinal);
CREATE INDEX idx_commands_task_date ON task_commands(task_id,created_at,id);
CREATE INDEX idx_task_queue ON tasks(status,desired_state,created_at,id);

-- Resultado publicado tem escopo da conversa da tarefa; evita vínculo cruzado
-- inclusive quando importadores utilizam SQL diretamente.
CREATE TRIGGER model_calls_output_scope_insert BEFORE INSERT ON model_calls
WHEN NEW.output_message_id IS NOT NULL BEGIN
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1 FROM runs r JOIN tasks t ON t.id=r.task_id
        JOIN messages m ON m.id=NEW.output_message_id
        WHERE r.id=NEW.run_id AND t.conversation_id=m.conversation_id AND m.role='assistant'
    ) THEN RAISE(ABORT,'model_call_output_scope') END;
END;
CREATE TRIGGER model_calls_output_scope_update BEFORE UPDATE OF output_message_id,run_id ON model_calls
WHEN NEW.output_message_id IS NOT NULL BEGIN
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1 FROM runs r JOIN tasks t ON t.id=r.task_id
        JOIN messages m ON m.id=NEW.output_message_id
        WHERE r.id=NEW.run_id AND t.conversation_id=m.conversation_id AND m.role='assistant'
    ) THEN RAISE(ABORT,'model_call_output_scope') END;
END;

-- Gerações nunca são removidas/resetadas; lease coordena trabalhadores conhecidos.
CREATE TABLE execution_leases (
    resource_key TEXT PRIMARY KEY NOT NULL,
    owner_id TEXT, run_id TEXT REFERENCES runs(id) ON DELETE RESTRICT,
    generation INTEGER NOT NULL DEFAULT 0 CHECK(generation>=0),
    acquired_at TEXT, expires_at TEXT,
    CHECK((owner_id IS NULL AND run_id IS NULL AND expires_at IS NULL)
        OR (owner_id IS NOT NULL AND run_id IS NOT NULL AND expires_at IS NOT NULL))
) STRICT;
