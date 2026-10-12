-- Projeção durável dos fatos de tarefa. Nenhum texto privado ou transporte externo.
CREATE TABLE attention_items (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    id TEXT NOT NULL UNIQUE,
    agent_id TEXT NOT NULL REFERENCES agents(id) ON DELETE RESTRICT,
    task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE RESTRICT,
    approval_id TEXT REFERENCES approvals(id) ON DELETE RESTRICT,
    kind TEXT NOT NULL CHECK(kind IN
        ('task_completed','task_failed','approval_required','outcome_unknown')),
    entity_type TEXT NOT NULL CHECK(entity_type IN ('task','run','approval','model_call','action')),
    entity_id TEXT NOT NULL,
    source_event_id TEXT NOT NULL UNIQUE REFERENCES domain_events(id) ON DELETE RESTRICT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    revision INTEGER NOT NULL DEFAULT 1 CHECK(revision > 0),
    is_read INTEGER NOT NULL DEFAULT 0 CHECK(is_read IN (0,1)),
    CHECK((kind='approval_required') = (approval_id IS NOT NULL)),
    CHECK((kind IN ('task_completed','task_failed') AND entity_type IN ('task','run'))
        OR (kind='approval_required' AND entity_type='approval' AND entity_id=approval_id)
        OR (kind='outcome_unknown' AND entity_type IN ('model_call','action'))),
    UNIQUE(kind,entity_type,entity_id)
) STRICT;
CREATE INDEX idx_attention_agent_seq ON attention_items(agent_id,seq DESC);
CREATE INDEX idx_attention_unread ON attention_items(agent_id,kind,seq)
    WHERE is_read=0;

CREATE TABLE attention_preferences (
    agent_id TEXT NOT NULL REFERENCES agents(id) ON DELETE RESTRICT,
    kind TEXT NOT NULL CHECK(kind IN
        ('task_completed','task_failed','approval_required','outcome_unknown')),
    enabled INTEGER NOT NULL CHECK(enabled IN (0,1)),
    revision INTEGER NOT NULL CHECK(revision > 0),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(agent_id,kind)
) STRICT;

CREATE TABLE attention_commands (
    client_request_id TEXT PRIMARY KEY NOT NULL,
    agent_id TEXT NOT NULL REFERENCES agents(id) ON DELETE RESTRICT,
    target TEXT NOT NULL,
    request_hash TEXT NOT NULL CHECK(length(request_hash)=64),
    created_at TEXT NOT NULL,
    actor TEXT NOT NULL CHECK(actor='user')
) STRICT;

CREATE TRIGGER attention_item_binding BEFORE INSERT ON attention_items
WHEN NOT EXISTS(SELECT 1 FROM tasks t WHERE t.id=NEW.task_id AND t.agent_id=NEW.agent_id)
 OR (NEW.entity_type='task' AND NEW.entity_id<>NEW.task_id)
 OR (NEW.entity_type='run' AND NOT EXISTS(
    SELECT 1 FROM runs r WHERE r.id=NEW.entity_id AND r.task_id=NEW.task_id))
 OR (NEW.entity_type='model_call' AND NOT EXISTS(
    SELECT 1 FROM model_calls c JOIN runs r ON r.id=c.run_id
    WHERE c.id=NEW.entity_id AND r.task_id=NEW.task_id))
 OR (NEW.entity_type='action' AND NOT EXISTS(
    SELECT 1 FROM actions a JOIN runs r ON r.id=a.run_id
    WHERE a.id=NEW.entity_id AND r.task_id=NEW.task_id))
 OR (NEW.approval_id IS NOT NULL AND NOT EXISTS(
    SELECT 1 FROM approvals p JOIN actions a ON a.id=p.action_id
    JOIN runs r ON r.id=a.run_id WHERE p.id=NEW.approval_id AND r.task_id=NEW.task_id))
BEGIN SELECT RAISE(ABORT,'attention binding is invalid'); END;

CREATE TRIGGER attention_item_update BEFORE UPDATE ON attention_items
WHEN NEW.id IS NOT OLD.id OR NEW.seq IS NOT OLD.seq
 OR NEW.agent_id IS NOT OLD.agent_id OR NEW.task_id IS NOT OLD.task_id
 OR NEW.approval_id IS NOT OLD.approval_id OR NEW.kind IS NOT OLD.kind
 OR NEW.entity_type IS NOT OLD.entity_type OR NEW.entity_id IS NOT OLD.entity_id
 OR NEW.source_event_id IS NOT OLD.source_event_id OR NEW.created_at IS NOT OLD.created_at
 OR NEW.revision <> OLD.revision+1
BEGIN SELECT RAISE(ABORT,'attention identity is immutable'); END;
CREATE TRIGGER attention_item_no_delete BEFORE DELETE ON attention_items
BEGIN SELECT RAISE(ABORT,'attention is append-only'); END;
CREATE TRIGGER attention_preference_update BEFORE UPDATE ON attention_preferences
WHEN NEW.agent_id IS NOT OLD.agent_id OR NEW.kind IS NOT OLD.kind
 OR NEW.created_at IS NOT OLD.created_at OR NEW.revision <> OLD.revision+1
BEGIN SELECT RAISE(ABORT,'attention preference identity is immutable'); END;
CREATE TRIGGER attention_preference_no_delete BEFORE DELETE ON attention_preferences
BEGIN SELECT RAISE(ABORT,'disable attention preference explicitly'); END;
CREATE TRIGGER attention_command_update BEFORE UPDATE ON attention_commands
BEGIN SELECT RAISE(ABORT,'attention commands are immutable'); END;
CREATE TRIGGER attention_command_no_delete BEFORE DELETE ON attention_commands
BEGIN SELECT RAISE(ABORT,'attention commands are append-only'); END;
