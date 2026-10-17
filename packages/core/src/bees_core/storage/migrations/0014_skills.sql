-- Procedimentos humanos declarativos; nenhuma revisão concede acesso ou executa código.
CREATE TABLE skills (
    id TEXT PRIMARY KEY NOT NULL,
    agent_id TEXT NOT NULL REFERENCES agents(id) ON DELETE RESTRICT,
    revision INTEGER NOT NULL CHECK(revision > 0),
    current_version INTEGER NOT NULL CHECK(current_version > 0),
    enabled_version INTEGER,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    CHECK(enabled_version IS NULL OR enabled_version=current_version),
    FOREIGN KEY(id,current_version) REFERENCES skill_revisions(skill_id,version)
        DEFERRABLE INITIALLY DEFERRED
) STRICT;
CREATE INDEX idx_skills_agent ON skills(agent_id,created_at,id);

CREATE TABLE skill_revisions (
    skill_id TEXT NOT NULL REFERENCES skills(id) ON DELETE RESTRICT,
    version INTEGER NOT NULL CHECK(version > 0),
    definition_json TEXT NOT NULL CHECK(json_valid(definition_json)),
    content_hash TEXT NOT NULL CHECK(length(content_hash)=64),
    origin TEXT NOT NULL CHECK(origin IN ('human','import')),
    created_at TEXT NOT NULL,
    PRIMARY KEY(skill_id,version)
) STRICT;
CREATE TABLE skill_commands (
    client_request_id TEXT PRIMARY KEY NOT NULL,
    agent_id TEXT NOT NULL REFERENCES agents(id) ON DELETE RESTRICT,
    target TEXT NOT NULL,
    request_hash TEXT NOT NULL CHECK(length(request_hash)=64),
    response_json TEXT NOT NULL CHECK(json_valid(response_json)),
    created_at TEXT NOT NULL
) STRICT;
CREATE TABLE skill_events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    skill_id TEXT NOT NULL REFERENCES skills(id) ON DELETE RESTRICT,
    revision INTEGER NOT NULL CHECK(revision > 0),
    version INTEGER NOT NULL CHECK(version > 0),
    kind TEXT NOT NULL CHECK(kind IN ('created','revised','enabled','disabled')),
    client_request_id TEXT NOT NULL UNIQUE REFERENCES skill_commands(client_request_id),
    created_at TEXT NOT NULL,
    actor TEXT NOT NULL CHECK(actor='user'),
    UNIQUE(skill_id,revision)
) STRICT;
CREATE TRIGGER skill_update BEFORE UPDATE ON skills
WHEN NEW.id IS NOT OLD.id OR NEW.agent_id IS NOT OLD.agent_id
 OR NEW.created_at IS NOT OLD.created_at OR NEW.revision<>OLD.revision+1
 OR NEW.current_version NOT IN (OLD.current_version,OLD.current_version+1)
 OR (NEW.current_version<>OLD.current_version AND NEW.enabled_version IS NOT NULL)
BEGIN SELECT RAISE(ABORT,'skill identity and revision are immutable'); END;
CREATE TRIGGER skill_no_delete BEFORE DELETE ON skills
BEGIN SELECT RAISE(ABORT,'disable skills explicitly'); END;
CREATE TRIGGER skill_revision_no_update BEFORE UPDATE ON skill_revisions
BEGIN SELECT RAISE(ABORT,'skill revisions are immutable'); END;
CREATE TRIGGER skill_revision_no_delete BEFORE DELETE ON skill_revisions
BEGIN SELECT RAISE(ABORT,'skill revisions are append-only'); END;
CREATE TRIGGER skill_command_no_update BEFORE UPDATE ON skill_commands
BEGIN SELECT RAISE(ABORT,'skill commands are immutable'); END;
CREATE TRIGGER skill_command_no_delete BEFORE DELETE ON skill_commands
BEGIN SELECT RAISE(ABORT,'skill commands are append-only'); END;
CREATE TRIGGER skill_event_no_update BEFORE UPDATE ON skill_events
BEGIN SELECT RAISE(ABORT,'skill events are immutable'); END;
CREATE TRIGGER skill_event_no_delete BEFORE DELETE ON skill_events
BEGIN SELECT RAISE(ABORT,'skill events are append-only'); END;
