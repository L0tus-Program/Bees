-- Pareamento de diagnóstico. Não concede execução, arquivos ou acesso a VMs.
CREATE TABLE host_link_installation (
 id INTEGER PRIMARY KEY CHECK(id=1), installation_id TEXT NOT NULL UNIQUE, created_at REAL NOT NULL
) STRICT;
CREATE TABLE host_link_invites (
 id TEXT PRIMARY KEY NOT NULL, client_request_id TEXT NOT NULL UNIQUE,
 token_hash TEXT NOT NULL UNIQUE CHECK(length(token_hash)=64),
 created_at REAL NOT NULL, expires_at REAL NOT NULL CHECK(expires_at>created_at),
 consumed_at REAL, revoked_at REAL
) STRICT;
CREATE TABLE host_links (
 host_id TEXT PRIMARY KEY NOT NULL,
 invite_id TEXT NOT NULL UNIQUE REFERENCES host_link_invites(id) ON DELETE RESTRICT,
 credential_hash TEXT NOT NULL UNIQUE CHECK(length(credential_hash)=64),
 fingerprint TEXT NOT NULL CHECK(length(fingerprint)=19),
 pair_request_id TEXT NOT NULL UNIQUE, pair_request_hash TEXT NOT NULL CHECK(length(pair_request_hash)=64),
 status TEXT NOT NULL CHECK(status IN ('pending','active','revoked')),
 revision INTEGER NOT NULL DEFAULT 1 CHECK(revision>0),
 report_revision INTEGER NOT NULL DEFAULT 0 CHECK(report_revision>=0),
 created_at REAL NOT NULL, pairing_expires_at REAL NOT NULL CHECK(pairing_expires_at>created_at),
 paired_at REAL, revoked_at REAL,
 last_seen REAL, last_report_sequence INTEGER NOT NULL DEFAULT 0 CHECK(last_report_sequence>=0),
 last_report_id TEXT, last_report_hash TEXT,
 diagnostic_json TEXT CHECK(diagnostic_json IS NULL OR (json_valid(diagnostic_json) AND json_type(diagnostic_json)='object')),
 CHECK((status='active')=(paired_at IS NOT NULL AND revoked_at IS NULL)),
 CHECK((status='revoked')=(revoked_at IS NOT NULL))
) STRICT;
CREATE UNIQUE INDEX idx_host_links_active ON host_links(status) WHERE status='active';
CREATE INDEX idx_host_links_created ON host_links(created_at,host_id);
CREATE TABLE host_link_commands (
 host_id TEXT NOT NULL REFERENCES host_links(host_id) ON DELETE RESTRICT,
 client_request_id TEXT NOT NULL,
 command TEXT NOT NULL CHECK(command IN ('confirm','revoke')),
 request_hash TEXT NOT NULL CHECK(length(request_hash)=64),
 created_at REAL NOT NULL,
 PRIMARY KEY(host_id,client_request_id)
) STRICT;
CREATE TABLE host_link_report_receipts (
 host_id TEXT NOT NULL REFERENCES host_links(host_id) ON DELETE RESTRICT,
 client_request_id TEXT NOT NULL,
 request_hash TEXT NOT NULL CHECK(length(request_hash)=64),
 sequence INTEGER NOT NULL CHECK(sequence>0),
 created_at REAL NOT NULL,
 PRIMARY KEY(host_id,client_request_id),
 UNIQUE(host_id,sequence)
) STRICT;
CREATE TRIGGER host_links_identity_immutable BEFORE UPDATE ON host_links
WHEN NEW.host_id IS NOT OLD.host_id OR NEW.invite_id IS NOT OLD.invite_id
 OR NEW.credential_hash IS NOT OLD.credential_hash OR NEW.fingerprint IS NOT OLD.fingerprint
 OR NEW.pair_request_id IS NOT OLD.pair_request_id OR NEW.pair_request_hash IS NOT OLD.pair_request_hash
 OR NEW.created_at IS NOT OLD.created_at OR NEW.pairing_expires_at IS NOT OLD.pairing_expires_at
BEGIN SELECT RAISE(ABORT,'host link identity is immutable'); END;
CREATE TRIGGER host_links_revoked_terminal BEFORE UPDATE ON host_links WHEN OLD.status='revoked'
BEGIN SELECT RAISE(ABORT,'revoked host link is terminal'); END;
CREATE TRIGGER host_link_commands_append_only_update BEFORE UPDATE ON host_link_commands
BEGIN SELECT RAISE(ABORT,'host command is append only'); END;
CREATE TRIGGER host_link_commands_append_only_delete BEFORE DELETE ON host_link_commands
BEGIN SELECT RAISE(ABORT,'host command is append only'); END;
