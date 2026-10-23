-- Gatilho externo (webhook) de uma rotina. O segredo existe só como hash; cada disparo
-- usa o caminho de execução manual, com intervalo mínimo entre disparos.
CREATE TABLE routine_hooks (
    id TEXT PRIMARY KEY NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    revision INTEGER NOT NULL DEFAULT 1 CHECK(revision>0),
    agent_id TEXT NOT NULL REFERENCES agents(id) ON DELETE RESTRICT,
    routine_id TEXT NOT NULL REFERENCES routines(id) ON DELETE RESTRICT,
    token_hash TEXT NOT NULL UNIQUE CHECK(length(token_hash)=64),
    status TEXT NOT NULL CHECK(status IN ('active','revoked')),
    min_interval_seconds INTEGER NOT NULL DEFAULT 60 CHECK(min_interval_seconds BETWEEN 10 AND 86400),
    last_triggered_at TEXT,
    last_result TEXT CHECK(last_result IS NULL OR length(last_result) <= 64),
    trigger_count INTEGER NOT NULL DEFAULT 0 CHECK(trigger_count>=0)
) STRICT;
CREATE UNIQUE INDEX idx_routine_hooks_active ON routine_hooks(routine_id) WHERE status='active';
CREATE TRIGGER routine_hooks_identity_immutable BEFORE UPDATE ON routine_hooks
WHEN NEW.agent_id IS NOT OLD.agent_id OR NEW.routine_id IS NOT OLD.routine_id
  OR NEW.token_hash IS NOT OLD.token_hash
BEGIN SELECT RAISE(ABORT,'routine hook identity is immutable'); END;
