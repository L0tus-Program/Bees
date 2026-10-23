-- Canais externos (Telegram, Discord) ligados a uma conversa da abelha. O token do bot
-- fica no cofre (secret_ref); o pareamento guarda somente hash do código.
CREATE TABLE channel_links (
    id TEXT PRIMARY KEY NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    revision INTEGER NOT NULL DEFAULT 1 CHECK(revision>0),
    metadata_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(metadata_json) AND json_type(metadata_json)='object'),
    agent_id TEXT NOT NULL REFERENCES agents(id) ON DELETE RESTRICT,
    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE RESTRICT,
    kind TEXT NOT NULL CHECK(kind IN ('telegram','discord')),
    name TEXT NOT NULL CHECK(length(name) BETWEEN 1 AND 100),
    status TEXT NOT NULL CHECK(status IN ('pending','active','paused','revoked','failed')),
    mode TEXT NOT NULL CHECK(mode IN ('chat','agent','research')),
    secret_ref TEXT NOT NULL CHECK(length(secret_ref) BETWEEN 1 AND 128),
    target TEXT CHECK(target IS NULL OR length(target) BETWEEN 1 AND 64),
    pairing_hash TEXT CHECK(pairing_hash IS NULL OR length(pairing_hash)=64),
    pairing_expires_at TEXT,
    chat_id TEXT CHECK(chat_id IS NULL OR length(chat_id) BETWEEN 1 AND 64),
    peer_name TEXT CHECK(peer_name IS NULL OR length(peer_name) <= 200),
    inbound_cursor TEXT NOT NULL DEFAULT '0' CHECK(length(inbound_cursor) <= 64),
    outbound_after TEXT,
    reason_code TEXT CHECK(reason_code IS NULL OR length(reason_code) <= 64),
    CHECK((status='active') <= (chat_id IS NOT NULL))
) STRICT;
CREATE INDEX idx_channel_links_status ON channel_links(status,kind);
CREATE INDEX idx_channel_links_agent ON channel_links(agent_id,created_at);
CREATE TRIGGER channel_links_identity_immutable BEFORE UPDATE ON channel_links
WHEN NEW.agent_id IS NOT OLD.agent_id OR NEW.conversation_id IS NOT OLD.conversation_id
  OR NEW.kind IS NOT OLD.kind
BEGIN SELECT RAISE(ABORT,'channel identity is immutable'); END;

-- Entradas e saídas já tratadas: deduplicação durável por identificador externo.
CREATE TABLE channel_events (
    link_id TEXT NOT NULL REFERENCES channel_links(id) ON DELETE RESTRICT,
    direction TEXT NOT NULL CHECK(direction IN ('in','out')),
    external_id TEXT NOT NULL CHECK(length(external_id) BETWEEN 1 AND 200),
    created_at TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('received','processed','ignored','failed','sent')),
    message_id TEXT,
    PRIMARY KEY(link_id,direction,external_id)
) STRICT;
