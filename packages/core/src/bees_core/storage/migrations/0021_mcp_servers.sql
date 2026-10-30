-- Servidores MCP (Streamable HTTP) configurados por abelha. Credencial opcional fica no
-- cofre; tools_json guarda o último catálogo lido, usado para validar chamadas.
CREATE TABLE mcp_servers (
    id TEXT PRIMARY KEY NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    revision INTEGER NOT NULL DEFAULT 1 CHECK(revision>0),
    metadata_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(metadata_json) AND json_type(metadata_json)='object'),
    agent_id TEXT NOT NULL REFERENCES agents(id) ON DELETE RESTRICT,
    name TEXT NOT NULL CHECK(length(name) BETWEEN 1 AND 40),
    url TEXT NOT NULL CHECK(length(url) BETWEEN 8 AND 2048),
    secret_ref TEXT CHECK(secret_ref IS NULL OR length(secret_ref) BETWEEN 1 AND 128),
    enabled INTEGER NOT NULL CHECK(enabled IN (0,1)),
    status TEXT NOT NULL CHECK(status IN ('ready','failed','removed')),
    tools_json TEXT NOT NULL DEFAULT '[]' CHECK(json_valid(tools_json) AND json_type(tools_json)='array'),
    reason_code TEXT CHECK(reason_code IS NULL OR length(reason_code) <= 64),
    refreshed_at TEXT
) STRICT;
CREATE UNIQUE INDEX idx_mcp_servers_name ON mcp_servers(agent_id,name) WHERE status!='removed';
CREATE TRIGGER mcp_servers_identity_immutable BEFORE UPDATE ON mcp_servers
WHEN NEW.agent_id IS NOT OLD.agent_id OR NEW.name IS NOT OLD.name
BEGIN SELECT RAISE(ABORT,'mcp server identity is immutable'); END;
