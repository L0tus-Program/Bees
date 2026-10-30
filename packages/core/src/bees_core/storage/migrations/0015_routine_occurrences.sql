-- Rotinas legadas não recebem definição operacional nem cursor automaticamente.
CREATE TABLE routine_definitions (
    routine_id TEXT NOT NULL REFERENCES routines(id) ON DELETE RESTRICT,
    revision INTEGER NOT NULL CHECK(revision > 0),
    definition_json TEXT NOT NULL CHECK(json_valid(definition_json)),
    content_hash TEXT NOT NULL CHECK(length(content_hash)=64),
    origin TEXT NOT NULL CHECK(origin='human'),
    created_at TEXT NOT NULL,
    PRIMARY KEY(routine_id,revision)
) STRICT;
CREATE TABLE routine_cursors (
    routine_id TEXT PRIMARY KEY NOT NULL REFERENCES routines(id) ON DELETE RESTRICT,
    routine_revision INTEGER NOT NULL,
    next_run_at TEXT,
    evaluated_through TEXT NOT NULL,
    FOREIGN KEY(routine_id,routine_revision)
        REFERENCES routine_definitions(routine_id,revision) ON DELETE RESTRICT
) STRICT;
CREATE TABLE routine_commands (
    client_request_id TEXT PRIMARY KEY NOT NULL,
    agent_id TEXT NOT NULL REFERENCES agents(id) ON DELETE RESTRICT,
    target TEXT NOT NULL,
    request_hash TEXT NOT NULL CHECK(length(request_hash)=64),
    response_json TEXT NOT NULL CHECK(json_valid(response_json)),
    created_at TEXT NOT NULL
) STRICT;
CREATE TABLE routine_occurrences (
    id TEXT PRIMARY KEY NOT NULL,
    routine_id TEXT NOT NULL REFERENCES routines(id) ON DELETE RESTRICT,
    routine_revision INTEGER NOT NULL,
    scheduled_at TEXT NOT NULL,
    window_end TEXT NOT NULL CHECK(window_end>=scheduled_at),
    status TEXT NOT NULL CHECK(status IN (
        'pending','running','completed','failed','cancelled','skipped','outcome_unknown'
    )),
    reason TEXT NOT NULL,
    task_id TEXT REFERENCES tasks(id) ON DELETE RESTRICT,
    revision INTEGER NOT NULL CHECK(revision>0),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(routine_id,routine_revision)
        REFERENCES routine_definitions(routine_id,revision) ON DELETE RESTRICT,
    UNIQUE(routine_id,routine_revision,scheduled_at)
) STRICT;
CREATE INDEX idx_routine_occurrences_history
    ON routine_occurrences(routine_id,scheduled_at DESC,id);
CREATE INDEX idx_routine_occurrences_pending ON routine_occurrences(status,scheduled_at,id);
CREATE TRIGGER routine_definition_no_update BEFORE UPDATE ON routine_definitions
BEGIN SELECT RAISE(ABORT,'routine definitions are immutable'); END;
CREATE TRIGGER routine_definition_no_delete BEFORE DELETE ON routine_definitions
BEGIN SELECT RAISE(ABORT,'routine definitions are append-only'); END;
CREATE TRIGGER routine_command_no_update BEFORE UPDATE ON routine_commands
BEGIN SELECT RAISE(ABORT,'routine commands are immutable'); END;
CREATE TRIGGER routine_command_no_delete BEFORE DELETE ON routine_commands
BEGIN SELECT RAISE(ABORT,'routine commands are append-only'); END;
CREATE TRIGGER routine_occurrence_update BEFORE UPDATE ON routine_occurrences
WHEN NEW.id IS NOT OLD.id OR NEW.routine_id IS NOT OLD.routine_id
 OR NEW.routine_revision IS NOT OLD.routine_revision
 OR NEW.scheduled_at IS NOT OLD.scheduled_at OR NEW.window_end IS NOT OLD.window_end
 OR NEW.created_at IS NOT OLD.created_at OR NEW.revision<>OLD.revision+1
 OR (OLD.task_id IS NOT NULL AND NEW.task_id IS NOT OLD.task_id)
 OR NOT ((OLD.status='pending' AND NEW.status IN ('running','cancelled','skipped'))
      OR (OLD.status='running' AND NEW.status IN ('completed','failed','outcome_unknown')))
BEGIN SELECT RAISE(ABORT,'invalid routine occurrence transition'); END;
CREATE TRIGGER routine_occurrence_no_delete BEFORE DELETE ON routine_occurrences
BEGIN SELECT RAISE(ABORT,'routine occurrences are append-only'); END;
