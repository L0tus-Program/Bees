-- Executores remotos pareados (runtime de computadores e conector pessoal), computadores
-- das abelhas e comandos duráveis. Credenciais ficam somente como hash.
CREATE TABLE executors (
    id TEXT PRIMARY KEY NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    revision INTEGER NOT NULL DEFAULT 1 CHECK(revision>0),
    metadata_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(metadata_json) AND json_type(metadata_json)='object'),
    kind TEXT NOT NULL CHECK(kind IN ('computer_runtime','personal')),
    name TEXT NOT NULL CHECK(length(name) BETWEEN 1 AND 100),
    status TEXT NOT NULL CHECK(status IN ('active','revoked')),
    credential_hash TEXT NOT NULL UNIQUE CHECK(length(credential_hash)=64),
    last_seen_at TEXT,
    connection_id TEXT
) STRICT;
CREATE INDEX idx_executors_kind ON executors(kind,status,created_at);

CREATE TABLE executor_invites (
    id TEXT PRIMARY KEY NOT NULL,
    created_at TEXT NOT NULL,
    kind TEXT NOT NULL CHECK(kind IN ('computer_runtime','personal')),
    name TEXT NOT NULL CHECK(length(name) BETWEEN 1 AND 100),
    token_hash TEXT NOT NULL UNIQUE CHECK(length(token_hash)=64),
    expires_at TEXT NOT NULL,
    used_at TEXT,
    executor_id TEXT REFERENCES executors(id) ON DELETE RESTRICT,
    CHECK((used_at IS NULL)=(executor_id IS NULL))
) STRICT;

CREATE TABLE computers (
    id TEXT PRIMARY KEY NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    revision INTEGER NOT NULL DEFAULT 1 CHECK(revision>0),
    metadata_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(metadata_json) AND json_type(metadata_json)='object'),
    agent_id TEXT NOT NULL REFERENCES agents(id) ON DELETE RESTRICT,
    executor_id TEXT NOT NULL REFERENCES executors(id) ON DELETE RESTRICT,
    name TEXT NOT NULL CHECK(length(name) BETWEEN 1 AND 100),
    status TEXT NOT NULL CHECK(status IN ('provisioning','ready','failed','removing','removed')),
    control_holder TEXT NOT NULL DEFAULT 'agent' CHECK(control_holder IN ('agent','human')),
    control_revision INTEGER NOT NULL DEFAULT 1 CHECK(control_revision>0),
    reason_code TEXT
) STRICT;
CREATE UNIQUE INDEX idx_computers_active_agent ON computers(agent_id) WHERE status!='removed';
CREATE TRIGGER computers_identity_immutable BEFORE UPDATE ON computers
WHEN NEW.agent_id IS NOT OLD.agent_id OR NEW.executor_id IS NOT OLD.executor_id
BEGIN SELECT RAISE(ABORT,'computer identity is immutable'); END;

-- Concessões da máquina pessoal por abelha; raízes e recursos são explícitos.
CREATE TABLE executor_grants (
    id TEXT PRIMARY KEY NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    revision INTEGER NOT NULL DEFAULT 1 CHECK(revision>0),
    executor_id TEXT NOT NULL REFERENCES executors(id) ON DELETE RESTRICT,
    agent_id TEXT NOT NULL REFERENCES agents(id) ON DELETE RESTRICT,
    enabled INTEGER NOT NULL CHECK(enabled IN (0,1)),
    scope_json TEXT NOT NULL CHECK(json_valid(scope_json) AND json_type(scope_json)='object'),
    UNIQUE(executor_id,agent_id)
) STRICT;

CREATE TABLE executor_commands (
    id TEXT PRIMARY KEY NOT NULL REFERENCES actions(id) ON DELETE RESTRICT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    executor_id TEXT NOT NULL REFERENCES executors(id) ON DELETE RESTRICT,
    computer_id TEXT REFERENCES computers(id) ON DELETE RESTRICT,
    agent_id TEXT NOT NULL REFERENCES agents(id) ON DELETE RESTRICT,
    tool_name TEXT NOT NULL,
    arguments_json TEXT NOT NULL CHECK(json_valid(arguments_json) AND json_type(arguments_json)='object'),
    deadline_at TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('queued','sent','confirmed','failed','unknown','expired')),
    result_json TEXT CHECK(result_json IS NULL OR json_valid(result_json)),
    error_code TEXT,
    sent_at TEXT,
    finished_at TEXT
) STRICT;
CREATE INDEX idx_executor_commands_queue ON executor_commands(executor_id,status,created_at);
CREATE TRIGGER executor_commands_terminal BEFORE UPDATE ON executor_commands
WHEN OLD.status IN ('confirmed','failed','unknown','expired')
BEGIN SELECT RAISE(ABORT,'executor command is terminal'); END;
