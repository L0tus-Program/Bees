-- Consumo canônico das gerações e limite de tokens por abelha. Aditiva.
-- Reserva é gravada no mesmo commit da autorização imediatamente antes da rede.
CREATE TABLE budget_limits (
    id TEXT PRIMARY KEY NOT NULL,
    agent_id TEXT NOT NULL UNIQUE REFERENCES agents(id) ON DELETE RESTRICT,
    token_limit INTEGER NOT NULL CHECK(token_limit BETWEEN 1 AND 1000000000),
    window_seconds INTEGER NOT NULL DEFAULT 86400 CHECK(window_seconds BETWEEN 3600 AND 2592000),
    output_allowance INTEGER NOT NULL DEFAULT 4096 CHECK(output_allowance BETWEEN 1 AND 1000000),
    status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','disabled')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    revision INTEGER NOT NULL DEFAULT 1 CHECK(revision > 0),
    metadata_json TEXT NOT NULL DEFAULT '{}'
        CHECK(json_valid(metadata_json) AND json_type(metadata_json)='object')
) STRICT;

CREATE TABLE usage_entries (
    id TEXT PRIMARY KEY NOT NULL,
    agent_id TEXT NOT NULL REFERENCES agents(id) ON DELETE RESTRICT,
    source TEXT NOT NULL CHECK(source IN ('chat','task')),
    conversation_id TEXT REFERENCES conversations(id) ON DELETE RESTRICT,
    model_call_id TEXT UNIQUE REFERENCES model_calls(id) ON DELETE RESTRICT,
    provider_kind TEXT NOT NULL CHECK(length(provider_kind) BETWEEN 1 AND 64),
    model TEXT NOT NULL CHECK(length(model) BETWEEN 1 AND 256),
    status TEXT NOT NULL DEFAULT 'reserved'
        CHECK(status IN ('reserved','confirmed','unknown','released')),
    reserved_tokens INTEGER NOT NULL CHECK(reserved_tokens >= 0),
    estimate_method TEXT NOT NULL CHECK(length(estimate_method) BETWEEN 1 AND 64),
    usage_kind TEXT CHECK(usage_kind IS NULL OR usage_kind IN ('reported','estimated','unknown')),
    input_tokens INTEGER CHECK(input_tokens IS NULL OR input_tokens >= 0),
    output_tokens INTEGER CHECK(output_tokens IS NULL OR output_tokens >= 0),
    total_tokens INTEGER CHECK(total_tokens IS NULL OR total_tokens >= 0),
    settled_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    revision INTEGER NOT NULL DEFAULT 1 CHECK(revision > 0),
    metadata_json TEXT NOT NULL DEFAULT '{}'
        CHECK(json_valid(metadata_json) AND json_type(metadata_json)='object'),
    CHECK((status = 'reserved') = (settled_at IS NULL)),
    CHECK((status = 'confirmed') = (usage_kind IS NOT NULL)),
    CHECK(status = 'confirmed' OR (input_tokens IS NULL AND output_tokens IS NULL
                                    AND total_tokens IS NULL)),
    CHECK((source = 'task') = (model_call_id IS NOT NULL))
) STRICT;

CREATE INDEX idx_usage_agent_time ON usage_entries(agent_id, created_at, id);

CREATE TRIGGER usage_entries_identity_immutable BEFORE UPDATE ON usage_entries
WHEN NEW.id IS NOT OLD.id OR NEW.agent_id IS NOT OLD.agent_id OR NEW.source IS NOT OLD.source
 OR NEW.conversation_id IS NOT OLD.conversation_id OR NEW.model_call_id IS NOT OLD.model_call_id
 OR NEW.provider_kind IS NOT OLD.provider_kind OR NEW.model IS NOT OLD.model
 OR NEW.reserved_tokens IS NOT OLD.reserved_tokens
 OR NEW.estimate_method IS NOT OLD.estimate_method OR NEW.created_at IS NOT OLD.created_at
BEGIN SELECT RAISE(ABORT,'usage entry identity is immutable'); END;

-- Uma liquidação é final: consumo informado ou incerto nunca volta a reserva/zero.
CREATE TRIGGER usage_entries_settlement_final BEFORE UPDATE ON usage_entries
WHEN OLD.status <> 'reserved'
BEGIN SELECT RAISE(ABORT,'usage settlement is final'); END;

CREATE TRIGGER usage_entries_no_delete BEFORE DELETE ON usage_entries
BEGIN SELECT RAISE(ABORT,'usage entries are append-only'); END;

CREATE TRIGGER budget_limits_identity_immutable BEFORE UPDATE ON budget_limits
WHEN NEW.id IS NOT OLD.id OR NEW.agent_id IS NOT OLD.agent_id
 OR NEW.created_at IS NOT OLD.created_at
BEGIN SELECT RAISE(ABORT,'budget limit identity is immutable'); END;

CREATE TRIGGER budget_limits_no_delete BEFORE DELETE ON budget_limits
BEGIN SELECT RAISE(ABORT,'budget limits are disabled, not deleted'); END;
