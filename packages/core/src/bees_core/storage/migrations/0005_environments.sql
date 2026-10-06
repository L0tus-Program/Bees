-- Pedidos e journal do host; nenhuma VM é considerada pronta neste checkpoint.
CREATE TABLE environments (
    id TEXT PRIMARY KEY NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    revision INTEGER NOT NULL DEFAULT 1 CHECK(revision>0),
    metadata_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(metadata_json) AND json_type(metadata_json)='object'),
    agent_id TEXT NOT NULL REFERENCES agents(id) ON DELETE RESTRICT,
    name TEXT NOT NULL CHECK(length(name) BETWEEN 1 AND 100),
    template_id TEXT NOT NULL CHECK(template_id='linux-desktop-v1'),
    cpu_count INTEGER NOT NULL CHECK(cpu_count BETWEEN 1 AND 4),
    memory_mib INTEGER NOT NULL CHECK(memory_mib BETWEEN 2048 AND 8192),
    disk_gib INTEGER NOT NULL CHECK(disk_gib BETWEEN 20 AND 100),
    status TEXT NOT NULL CHECK(status IN ('awaiting_host','provisioning','outcome_unknown','cancelled')),
    reason_code TEXT NOT NULL,
    client_request_id TEXT NOT NULL,
    request_hash TEXT NOT NULL CHECK(length(request_hash)=64 AND request_hash NOT GLOB '*[^0-9a-f]*'),
    UNIQUE(agent_id,client_request_id)
) STRICT;
CREATE INDEX idx_environments_agent ON environments(agent_id,created_at,id);
CREATE TABLE host_jobs (
    id TEXT PRIMARY KEY NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    revision INTEGER NOT NULL DEFAULT 1 CHECK(revision>0),
    metadata_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(metadata_json) AND json_type(metadata_json)='object'),
    environment_id TEXT NOT NULL UNIQUE REFERENCES environments(id) ON DELETE RESTRICT,
    operation TEXT NOT NULL CHECK(operation='create'),
    status TEXT NOT NULL CHECK(status IN ('awaiting_host','dispatch_started','outcome_unknown','cancelled')),
    correlation_id TEXT NOT NULL UNIQUE,
    owner_id TEXT,
    dispatched_at TEXT,
    recovery_evidence TEXT,
    CHECK((status IN ('dispatch_started','outcome_unknown') AND owner_id IS NOT NULL AND dispatched_at IS NOT NULL)
       OR (status IN ('awaiting_host','cancelled') AND owner_id IS NULL AND dispatched_at IS NULL)),
    CHECK((status='outcome_unknown')=(recovery_evidence IS NOT NULL))
) STRICT;
CREATE TRIGGER environments_identity_immutable BEFORE UPDATE ON environments
WHEN NEW.agent_id IS NOT OLD.agent_id OR NEW.client_request_id IS NOT OLD.client_request_id
 OR NEW.template_id IS NOT OLD.template_id OR NEW.cpu_count IS NOT OLD.cpu_count
 OR NEW.memory_mib IS NOT OLD.memory_mib OR NEW.disk_gib IS NOT OLD.disk_gib
 OR NEW.name IS NOT OLD.name OR NEW.request_hash IS NOT OLD.request_hash
BEGIN SELECT RAISE(ABORT,'environment request is immutable'); END;
CREATE TRIGGER host_jobs_identity_immutable BEFORE UPDATE ON host_jobs
WHEN NEW.environment_id IS NOT OLD.environment_id OR NEW.operation IS NOT OLD.operation
 OR NEW.correlation_id IS NOT OLD.correlation_id
BEGIN SELECT RAISE(ABORT,'host job identity is immutable'); END;
