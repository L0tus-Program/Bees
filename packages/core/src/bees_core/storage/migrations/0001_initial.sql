-- Estado canônico normalizado; JSON somente em contratos extensíveis.
CREATE TABLE agents (
    id TEXT PRIMARY KEY NOT NULL,
    name TEXT NOT NULL CHECK(length(trim(name)) > 0),
    purpose TEXT NOT NULL DEFAULT '',
    instructions TEXT NOT NULL DEFAULT '',
    model_config_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(model_config_json) AND json_type(model_config_json)='object'),
    status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','paused','archived')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    revision INTEGER NOT NULL DEFAULT 1 CHECK(revision > 0),
    metadata_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(metadata_json) AND json_type(metadata_json)='object')
) STRICT;

CREATE TABLE conversations (
    id TEXT PRIMARY KEY NOT NULL,
    agent_id TEXT NOT NULL REFERENCES agents(id) ON DELETE RESTRICT,
    title TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','archived')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    revision INTEGER NOT NULL DEFAULT 1 CHECK(revision > 0),
    metadata_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(metadata_json) AND json_type(metadata_json)='object'),
    UNIQUE(id,agent_id)
) STRICT;

CREATE TABLE messages (
    id TEXT PRIMARY KEY NOT NULL,
    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE RESTRICT,
    role TEXT NOT NULL CHECK(role IN ('user','assistant','system','tool')),
    content TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'user',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    revision INTEGER NOT NULL DEFAULT 1 CHECK(revision > 0),
    metadata_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(metadata_json) AND json_type(metadata_json)='object')
) STRICT;

CREATE TABLE routines (
    id TEXT PRIMARY KEY NOT NULL,
    agent_id TEXT NOT NULL REFERENCES agents(id) ON DELETE RESTRICT,
    name TEXT NOT NULL CHECK(length(trim(name)) > 0),
    instructions TEXT NOT NULL,
    schedule_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(schedule_json) AND json_type(schedule_json)='object'),
    timezone TEXT NOT NULL DEFAULT 'UTC',
    next_run_at TEXT,
    status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','paused','cancelled')),
    misfire_policy TEXT NOT NULL DEFAULT 'skip' CHECK(misfire_policy IN ('skip','catch_up')),
    overlap_policy TEXT NOT NULL DEFAULT 'skip' CHECK(overlap_policy IN ('skip','queue')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    revision INTEGER NOT NULL DEFAULT 1 CHECK(revision > 0),
    metadata_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(metadata_json) AND json_type(metadata_json)='object'),
    UNIQUE(id,agent_id)
) STRICT;

CREATE TABLE tasks (
    id TEXT PRIMARY KEY NOT NULL,
    agent_id TEXT NOT NULL REFERENCES agents(id) ON DELETE RESTRICT,
    conversation_id TEXT,
    routine_id TEXT,
    title TEXT NOT NULL CHECK(length(trim(title)) > 0),
    objective TEXT NOT NULL,
    expected_result TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'queued' CHECK(status IN ('queued','running','waiting_approval','waiting_resource','paused','completed','failed','cancelled')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    revision INTEGER NOT NULL DEFAULT 1 CHECK(revision > 0),
    metadata_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(metadata_json) AND json_type(metadata_json)='object'),
    UNIQUE(id,agent_id),
    FOREIGN KEY(conversation_id,agent_id) REFERENCES conversations(id,agent_id) ON DELETE RESTRICT,
    FOREIGN KEY(routine_id,agent_id) REFERENCES routines(id,agent_id) ON DELETE RESTRICT
) STRICT;

CREATE TABLE runs (
    id TEXT PRIMARY KEY NOT NULL,
    task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE RESTRICT,
    status TEXT NOT NULL DEFAULT 'queued' CHECK(status IN ('queued','running','waiting_approval','waiting_resource','paused','completed','failed','cancelled')),
    checkpoint_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(checkpoint_json) AND json_type(checkpoint_json)='object'),
    provider TEXT NOT NULL DEFAULT '',
    model TEXT NOT NULL DEFAULT '',
    environment_id TEXT,
    started_at TEXT,
    finished_at TEXT,
    error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    revision INTEGER NOT NULL DEFAULT 1 CHECK(revision > 0),
    metadata_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(metadata_json) AND json_type(metadata_json)='object'),
    UNIQUE(id,task_id)
) STRICT;

CREATE TABLE actions (
    id TEXT PRIMARY KEY NOT NULL,
    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE RESTRICT,
    tool_name TEXT NOT NULL CHECK(length(trim(tool_name)) > 0),
    parameters_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(parameters_json) AND json_type(parameters_json)='object'),
    status TEXT NOT NULL DEFAULT 'prepared' CHECK(status IN ('prepared','awaiting_approval','ready','dispatch_started','confirmed','failed_no_effect','outcome_unknown','cancelled')),
    result_json TEXT CHECK(result_json IS NULL OR (json_valid(result_json) AND json_type(result_json)='object')),
    idempotency_key TEXT UNIQUE,
    attempt INTEGER NOT NULL DEFAULT 1 CHECK(attempt > 0),
    policy_revision INTEGER CHECK(policy_revision IS NULL OR policy_revision > 0),
    lease_generation INTEGER CHECK(lease_generation IS NULL OR lease_generation > 0),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    revision INTEGER NOT NULL DEFAULT 1 CHECK(revision > 0),
    metadata_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(metadata_json) AND json_type(metadata_json)='object')
) STRICT;

CREATE TABLE policies (
    id TEXT PRIMARY KEY NOT NULL,
    agent_id TEXT REFERENCES agents(id) ON DELETE RESTRICT,
    name TEXT NOT NULL CHECK(length(trim(name)) > 0),
    effect TEXT NOT NULL CHECK(effect IN ('allow','ask','deny')),
    scope_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(scope_json) AND json_type(scope_json)='object'),
    status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','revoked')),
    source TEXT NOT NULL DEFAULT 'user',
    reason TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    revision INTEGER NOT NULL DEFAULT 1 CHECK(revision > 0),
    metadata_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(metadata_json) AND json_type(metadata_json)='object')
) STRICT;

CREATE TABLE approvals (
    id TEXT PRIMARY KEY NOT NULL,
    action_id TEXT NOT NULL REFERENCES actions(id) ON DELETE RESTRICT,
    policy_id TEXT REFERENCES policies(id) ON DELETE RESTRICT,
    status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','approved','denied','revoked')),
    decision TEXT CHECK(decision IS NULL OR decision IN ('allow_once','allow_rule','ask','deny')),
    scope_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(scope_json) AND json_type(scope_json)='object'),
    actor TEXT NOT NULL DEFAULT 'user',
    reason TEXT NOT NULL DEFAULT '',
    decided_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    revision INTEGER NOT NULL DEFAULT 1 CHECK(revision > 0),
    metadata_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(metadata_json) AND json_type(metadata_json)='object')
) STRICT;

CREATE TABLE memories (
    id TEXT PRIMARY KEY NOT NULL,
    agent_id TEXT REFERENCES agents(id) ON DELETE RESTRICT,
    task_id TEXT,
    scope TEXT NOT NULL CHECK(scope IN ('user','agent','task')),
    content TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'user',
    status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','archived')),
    deleted_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    revision INTEGER NOT NULL DEFAULT 1 CHECK(revision > 0),
    metadata_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(metadata_json) AND json_type(metadata_json)='object'),
    CHECK((scope='user' AND agent_id IS NULL AND task_id IS NULL)
        OR (scope='agent' AND agent_id IS NOT NULL AND task_id IS NULL)
        OR (scope='task' AND agent_id IS NOT NULL AND task_id IS NOT NULL)),
    FOREIGN KEY(task_id,agent_id) REFERENCES tasks(id,agent_id) ON DELETE RESTRICT
) STRICT;

CREATE TABLE artifacts (
    id TEXT PRIMARY KEY NOT NULL,
    task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE RESTRICT,
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
    FOREIGN KEY(run_id,task_id) REFERENCES runs(id,task_id) ON DELETE RESTRICT
) STRICT;

-- Eventos de domínio são fatos duráveis; não são logs de token nem cache.
CREATE TABLE domain_events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    id TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(payload_json) AND json_type(payload_json)='object'),
    actor TEXT NOT NULL,
    source TEXT NOT NULL,
    correlation_id TEXT
) STRICT;

CREATE TABLE cache_entries (
    key TEXT PRIMARY KEY NOT NULL,
    value_json TEXT NOT NULL CHECK(json_valid(value_json)),
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
) STRICT;

CREATE INDEX idx_conversations_agent_date ON conversations(agent_id,created_at,id);
CREATE INDEX idx_messages_conversation_date ON messages(conversation_id,created_at,id);
CREATE INDEX idx_tasks_agent_status_date ON tasks(agent_id,status,created_at,id);
CREATE INDEX idx_tasks_conversation ON tasks(conversation_id);
CREATE INDEX idx_tasks_routine ON tasks(routine_id);
CREATE INDEX idx_runs_task_date ON runs(task_id,created_at,id);
CREATE INDEX idx_runs_status_date ON runs(status,created_at,id);
CREATE INDEX idx_actions_run_date ON actions(run_id,created_at,id);
CREATE INDEX idx_actions_status_date ON actions(status,created_at,id);
CREATE INDEX idx_approvals_action ON approvals(action_id);
CREATE INDEX idx_approvals_policy ON approvals(policy_id);
CREATE INDEX idx_approvals_status_date ON approvals(status,created_at,id);
CREATE INDEX idx_policies_agent_status ON policies(agent_id,status);
CREATE INDEX idx_routines_due ON routines(status,next_run_at);
CREATE INDEX idx_routines_agent ON routines(agent_id);
CREATE INDEX idx_memories_scope_agent ON memories(scope,agent_id,status);
CREATE INDEX idx_memories_task ON memories(task_id);
CREATE INDEX idx_artifacts_task_date ON artifacts(task_id,created_at,id);
CREATE INDEX idx_artifacts_run ON artifacts(run_id);
CREATE INDEX idx_events_entity ON domain_events(entity_type,entity_id,seq);
CREATE INDEX idx_cache_expiration ON cache_entries(expires_at);

-- Estes gatilhos fazem apenas integridade relacional, não autorização ou auditoria.
-- Sem duplicar agent_id em toda a árvore, a FK não consegue validar este vínculo.
CREATE TRIGGER approvals_policy_agent_insert
BEFORE INSERT ON approvals WHEN NEW.policy_id IS NOT NULL
BEGIN
    SELECT RAISE(ABORT,'approval policy belongs to another agent')
    WHERE EXISTS (
        SELECT 1 FROM policies p, actions a
        JOIN runs r ON r.id=a.run_id JOIN tasks t ON t.id=r.task_id
        WHERE p.id=NEW.policy_id AND a.id=NEW.action_id
          AND p.agent_id IS NOT NULL AND p.agent_id != t.agent_id
    );
END;

CREATE TRIGGER approvals_policy_agent_update
BEFORE UPDATE OF policy_id,action_id ON approvals WHEN NEW.policy_id IS NOT NULL
BEGIN
    SELECT RAISE(ABORT,'approval policy belongs to another agent')
    WHERE EXISTS (
        SELECT 1 FROM policies p, actions a
        JOIN runs r ON r.id=a.run_id JOIN tasks t ON t.id=r.task_id
        WHERE p.id=NEW.policy_id AND a.id=NEW.action_id
          AND p.agent_id IS NOT NULL AND p.agent_id != t.agent_id
    );
END;

-- Vínculos que determinam a identidade autorizadora são imutáveis neste ciclo.
CREATE TRIGGER tasks_agent_immutable BEFORE UPDATE OF agent_id ON tasks
WHEN NEW.agent_id IS NOT OLD.agent_id
BEGIN SELECT RAISE(ABORT,'task agent is immutable'); END;

CREATE TRIGGER runs_task_immutable BEFORE UPDATE OF task_id ON runs
WHEN NEW.task_id IS NOT OLD.task_id
BEGIN SELECT RAISE(ABORT,'run task is immutable'); END;

CREATE TRIGGER actions_run_immutable BEFORE UPDATE OF run_id ON actions
WHEN NEW.run_id IS NOT OLD.run_id
BEGIN SELECT RAISE(ABORT,'action run is immutable'); END;

CREATE TRIGGER policies_agent_immutable BEFORE UPDATE OF agent_id ON policies
WHEN NEW.agent_id IS NOT OLD.agent_id
BEGIN SELECT RAISE(ABORT,'policy agent is immutable'); END;
