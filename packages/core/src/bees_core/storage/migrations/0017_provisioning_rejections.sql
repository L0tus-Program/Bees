-- Recusas definitivas de comandos que não aceitaram intent; UUID nunca se reativa.
CREATE TABLE provisioning_rejections (
    provisioner_id TEXT NOT NULL REFERENCES provisioning_credentials(id),
    client_request_id TEXT NOT NULL PRIMARY KEY,
    command TEXT NOT NULL CHECK (command IN ('claim', 'begin_dispatch')),
    request_hash TEXT NOT NULL CHECK (length(request_hash) = 64),
    code TEXT NOT NULL,
    created_at REAL NOT NULL CHECK (created_at >= 0)
) STRICT;
CREATE TRIGGER provisioning_rejections_immutable_update
BEFORE UPDATE ON provisioning_rejections
BEGIN SELECT RAISE(ABORT, 'provisioning rejection is immutable'); END;
CREATE TRIGGER provisioning_rejections_immutable_delete
BEFORE DELETE ON provisioning_rejections
BEGIN SELECT RAISE(ABORT, 'provisioning rejection is immutable'); END;
