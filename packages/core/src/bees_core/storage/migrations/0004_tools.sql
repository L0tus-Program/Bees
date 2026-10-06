-- Manifestos declarativos imutáveis; nenhuma referência executa código instalado.
CREATE TABLE plugins (
    id TEXT PRIMARY KEY NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    revision INTEGER NOT NULL DEFAULT 1 CHECK(revision>0),
    metadata_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(metadata_json) AND json_type(metadata_json)='object'),
    manifest_json TEXT NOT NULL CHECK(json_valid(manifest_json) AND json_type(manifest_json)='object'),
    manifest_hash TEXT NOT NULL CHECK(length(manifest_hash)=64 AND manifest_hash NOT GLOB '*[^0-9a-f]*'),
    enabled INTEGER NOT NULL DEFAULT 0 CHECK(enabled IN (0,1))
) STRICT;
CREATE TABLE tool_grants (
    id TEXT PRIMARY KEY NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    revision INTEGER NOT NULL DEFAULT 1 CHECK(revision>0),
    metadata_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(metadata_json) AND json_type(metadata_json)='object'),
    agent_id TEXT NOT NULL REFERENCES agents(id) ON DELETE RESTRICT,
    plugin_id TEXT NOT NULL REFERENCES plugins(id) ON DELETE RESTRICT,
    tool_name TEXT NOT NULL CHECK(length(tool_name)>0),
    enabled INTEGER NOT NULL DEFAULT 0 CHECK(enabled IN (0,1)),
    UNIQUE(agent_id,plugin_id,tool_name)
) STRICT;
CREATE INDEX idx_tool_grants_agent ON tool_grants(agent_id,created_at,id);
CREATE TRIGGER plugins_manifest_immutable BEFORE UPDATE OF manifest_json,manifest_hash ON plugins
WHEN NEW.manifest_json IS NOT OLD.manifest_json OR NEW.manifest_hash IS NOT OLD.manifest_hash
BEGIN SELECT RAISE(ABORT,'plugin manifest is immutable'); END;
CREATE TRIGGER tool_grants_identity_immutable BEFORE UPDATE OF agent_id,plugin_id,tool_name ON tool_grants
WHEN NEW.agent_id IS NOT OLD.agent_id OR NEW.plugin_id IS NOT OLD.plugin_id OR NEW.tool_name IS NOT OLD.tool_name
BEGIN SELECT RAISE(ABORT,'tool grant identity is immutable'); END;
