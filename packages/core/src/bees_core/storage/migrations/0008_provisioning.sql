-- Autoridade própria de provisionamento. Nenhuma credencial de diagnóstico executa.
CREATE TABLE provisioning_credentials (
 id TEXT PRIMARY KEY NOT NULL, installation_id TEXT NOT NULL,
 host_id TEXT NOT NULL REFERENCES host_links(host_id) ON DELETE RESTRICT,
 host_revision INTEGER NOT NULL CHECK(host_revision>0),
 credential_hash TEXT NOT NULL UNIQUE CHECK(length(credential_hash)=64),
 status TEXT NOT NULL CHECK(status IN ('active','revoked')),
 revision INTEGER NOT NULL DEFAULT 1 CHECK(revision>0),
 issue_request_id TEXT NOT NULL UNIQUE, created_at REAL NOT NULL, revoked_at REAL,
 CHECK((status='revoked')=(revoked_at IS NOT NULL))
) STRICT;
CREATE UNIQUE INDEX provisioning_one_credential ON provisioning_credentials(host_id)
 WHERE status='active';
CREATE TABLE provisioning_plans (
 id TEXT PRIMARY KEY NOT NULL, installation_id TEXT NOT NULL,
 host_id TEXT NOT NULL REFERENCES host_links(host_id) ON DELETE RESTRICT,
 environment_id TEXT NOT NULL REFERENCES environments(id) ON DELETE RESTRICT,
 job_id TEXT NOT NULL REFERENCES host_jobs(id) ON DELETE RESTRICT,
 plan_hash TEXT NOT NULL UNIQUE CHECK(length(plan_hash)=64),
 plan_json TEXT NOT NULL CHECK(json_valid(plan_json) AND json_type(plan_json)='object'),
 client_request_id TEXT NOT NULL UNIQUE, request_hash TEXT NOT NULL CHECK(length(request_hash)=64),
 created_at REAL NOT NULL
) STRICT;
CREATE INDEX provisioning_plans_environment ON provisioning_plans(environment_id,created_at,id);
CREATE TABLE provisioning_authorizations (
 plan_id TEXT PRIMARY KEY NOT NULL REFERENCES provisioning_plans(id) ON DELETE RESTRICT,
 revision INTEGER NOT NULL DEFAULT 1 CHECK(revision>0),
 status TEXT NOT NULL CHECK(status IN ('prepared','authorized','revoked')),
 authorized_at REAL, expires_at REAL,
 consumed_claim_id TEXT REFERENCES provisioning_claims(id) ON DELETE RESTRICT,
 CHECK((authorized_at IS NULL)=(expires_at IS NULL)),
 CHECK(expires_at IS NULL OR expires_at>authorized_at),
 CHECK(status!='authorized' OR expires_at IS NOT NULL)
) STRICT;
CREATE TABLE provisioning_hosts (
 host_id TEXT PRIMARY KEY NOT NULL REFERENCES host_links(host_id) ON DELETE RESTRICT,
 generation INTEGER NOT NULL DEFAULT 0 CHECK(generation>=0)
) STRICT;
CREATE TABLE provisioning_claims (
 id TEXT PRIMARY KEY NOT NULL,
 plan_id TEXT NOT NULL REFERENCES provisioning_plans(id) ON DELETE RESTRICT,
 provisioner_id TEXT NOT NULL REFERENCES provisioning_credentials(id) ON DELETE RESTRICT,
 host_id TEXT NOT NULL REFERENCES host_links(host_id) ON DELETE RESTRICT,
 owner_id TEXT NOT NULL, generation INTEGER NOT NULL CHECK(generation>0),
 authorization_revision INTEGER NOT NULL CHECK(authorization_revision>0),
 revision INTEGER NOT NULL DEFAULT 1 CHECK(revision>0),
 status TEXT NOT NULL CHECK(status IN ('claimed','dispatch_started','confirmed','aborted','outcome_unknown')),
 client_request_id TEXT NOT NULL UNIQUE, request_hash TEXT NOT NULL CHECK(length(request_hash)=64),
 created_at REAL NOT NULL, lease_expires_at REAL NOT NULL CHECK(lease_expires_at>created_at),
 recovery_evidence TEXT, acknowledged_at REAL,
 UNIQUE(host_id,generation),
 CHECK((status='outcome_unknown')=(recovery_evidence IS NOT NULL)),
 CHECK(acknowledged_at IS NULL OR status='outcome_unknown')
) STRICT;
CREATE UNIQUE INDEX provisioning_host_exclusive ON provisioning_claims(host_id)
 WHERE status IN ('claimed','dispatch_started','outcome_unknown');
CREATE UNIQUE INDEX provisioning_plan_exclusive ON provisioning_claims(plan_id)
 WHERE status IN ('claimed','dispatch_started','outcome_unknown','confirmed');
CREATE TABLE provisioning_effects (
 effect_request_id TEXT PRIMARY KEY NOT NULL,
 claim_id TEXT NOT NULL REFERENCES provisioning_claims(id) ON DELETE RESTRICT,
 ordinal INTEGER NOT NULL CHECK(ordinal BETWEEN 1 AND 6),
 operation TEXT NOT NULL CHECK(operation IN ('create_vhd','create_vm','configure_vm','remove_nic','attach_iso','verify')),
 status TEXT NOT NULL CHECK(status IN ('dispatch_started','confirmed','outcome_unknown')),
 request_hash TEXT NOT NULL CHECK(length(request_hash)=64),
 created_at REAL NOT NULL, confirmed_at REAL,
 result_json TEXT CHECK(result_json IS NULL OR (json_valid(result_json) AND json_type(result_json)='object')),
 receipt_request_id TEXT UNIQUE, receipt_hash TEXT,
 UNIQUE(claim_id,ordinal),
 CHECK((status='confirmed')=(confirmed_at IS NOT NULL)),
 CHECK((status='confirmed')=(result_json IS NOT NULL))
) STRICT;
CREATE TABLE provisioning_commands (
 entity_id TEXT NOT NULL, client_request_id TEXT NOT NULL,
 command TEXT NOT NULL, request_hash TEXT NOT NULL CHECK(length(request_hash)=64),
 created_at REAL NOT NULL,
 PRIMARY KEY(entity_id,client_request_id)
) STRICT;
CREATE TABLE provisioning_vm_bindings (
 host_id TEXT NOT NULL REFERENCES host_links(host_id) ON DELETE RESTRICT,
 vm_id TEXT NOT NULL,
 claim_id TEXT NOT NULL UNIQUE REFERENCES provisioning_claims(id) ON DELETE RESTRICT,
 created_at REAL NOT NULL,
 PRIMARY KEY(host_id,vm_id)
) STRICT;
CREATE TRIGGER provisioning_vm_binding_no_update BEFORE UPDATE ON provisioning_vm_bindings
BEGIN SELECT RAISE(ABORT,'physical VM receipt binding is immutable'); END;
CREATE TRIGGER provisioning_vm_binding_no_delete BEFORE DELETE ON provisioning_vm_bindings
BEGIN SELECT RAISE(ABORT,'physical VM receipt binding is durable'); END;
CREATE TRIGGER provisioning_plan_immutable BEFORE UPDATE ON provisioning_plans
BEGIN SELECT RAISE(ABORT,'provisioning plan is immutable'); END;
CREATE TRIGGER provisioning_plan_no_delete BEFORE DELETE ON provisioning_plans
BEGIN SELECT RAISE(ABORT,'provisioning plan is durable'); END;
CREATE TRIGGER provisioning_credential_identity BEFORE UPDATE ON provisioning_credentials
WHEN NEW.id IS NOT OLD.id OR NEW.installation_id IS NOT OLD.installation_id
 OR NEW.host_id IS NOT OLD.host_id OR NEW.host_revision IS NOT OLD.host_revision
 OR NEW.credential_hash IS NOT OLD.credential_hash OR NEW.issue_request_id IS NOT OLD.issue_request_id
 OR NEW.created_at IS NOT OLD.created_at
BEGIN SELECT RAISE(ABORT,'provisioner identity is immutable'); END;
CREATE TRIGGER provisioning_credential_revoked BEFORE UPDATE ON provisioning_credentials
WHEN OLD.status='revoked'
BEGIN SELECT RAISE(ABORT,'revoked provisioner is terminal'); END;
CREATE TRIGGER provisioning_credential_no_delete BEFORE DELETE ON provisioning_credentials
BEGIN SELECT RAISE(ABORT,'provisioner identity is durable'); END;
CREATE TRIGGER provisioning_authorization_consumption BEFORE UPDATE ON provisioning_authorizations
WHEN NEW.plan_id IS NOT OLD.plan_id
 OR (OLD.consumed_claim_id IS NOT NULL AND NEW.consumed_claim_id IS NOT OLD.consumed_claim_id)
 OR (OLD.status='revoked' AND NEW.status IS NOT OLD.status)
 OR NEW.revision<=OLD.revision
 OR (NEW.consumed_claim_id IS NOT NULL AND NOT EXISTS(
   SELECT 1 FROM provisioning_claims WHERE id=NEW.consumed_claim_id AND plan_id=NEW.plan_id))
BEGIN SELECT RAISE(ABORT,'provisioning authorization is fenced'); END;
CREATE TRIGGER provisioning_authorization_no_delete BEFORE DELETE ON provisioning_authorizations
BEGIN SELECT RAISE(ABORT,'provisioning authorization is durable'); END;
CREATE TRIGGER provisioning_host_generation BEFORE UPDATE ON provisioning_hosts
WHEN NEW.host_id IS NOT OLD.host_id OR NEW.generation<=OLD.generation
BEGIN SELECT RAISE(ABORT,'provisioning generation is monotonic'); END;
CREATE TRIGGER provisioning_host_no_delete BEFORE DELETE ON provisioning_hosts
BEGIN SELECT RAISE(ABORT,'provisioning generation is durable'); END;
CREATE TRIGGER provisioning_claim_identity BEFORE UPDATE ON provisioning_claims
WHEN NEW.id IS NOT OLD.id OR NEW.plan_id IS NOT OLD.plan_id OR NEW.provisioner_id IS NOT OLD.provisioner_id
 OR NEW.host_id IS NOT OLD.host_id OR NEW.owner_id IS NOT OLD.owner_id OR NEW.generation IS NOT OLD.generation
 OR NEW.client_request_id IS NOT OLD.client_request_id OR NEW.request_hash IS NOT OLD.request_hash
 OR NEW.authorization_revision IS NOT OLD.authorization_revision
 OR NEW.created_at IS NOT OLD.created_at
BEGIN SELECT RAISE(ABORT,'provisioning claim is immutable'); END;
CREATE TRIGGER provisioning_claim_terminal BEFORE UPDATE ON provisioning_claims
WHEN OLD.status IN ('confirmed','aborted','outcome_unknown') AND NEW.status IS NOT OLD.status
BEGIN SELECT RAISE(ABORT,'provisioning claim is terminal'); END;
CREATE TRIGGER provisioning_claim_no_delete BEFORE DELETE ON provisioning_claims
BEGIN SELECT RAISE(ABORT,'provisioning claim is durable'); END;
CREATE TRIGGER provisioning_effect_identity BEFORE UPDATE ON provisioning_effects
WHEN NEW.effect_request_id IS NOT OLD.effect_request_id OR NEW.claim_id IS NOT OLD.claim_id
 OR NEW.ordinal IS NOT OLD.ordinal OR NEW.operation IS NOT OLD.operation
 OR NEW.request_hash IS NOT OLD.request_hash OR NEW.created_at IS NOT OLD.created_at
BEGIN SELECT RAISE(ABORT,'provisioning effect is immutable'); END;
CREATE TRIGGER provisioning_effect_terminal BEFORE UPDATE ON provisioning_effects
WHEN OLD.status IN ('confirmed','outcome_unknown')
BEGIN SELECT RAISE(ABORT,'provisioning effect is terminal'); END;
CREATE TRIGGER provisioning_effect_no_delete BEFORE DELETE ON provisioning_effects
BEGIN SELECT RAISE(ABORT,'provisioning effect is durable'); END;
CREATE TRIGGER provisioning_commands_no_update BEFORE UPDATE ON provisioning_commands
BEGIN SELECT RAISE(ABORT,'provisioning receipt is append only'); END;
CREATE TRIGGER provisioning_commands_no_delete BEFORE DELETE ON provisioning_commands
BEGIN SELECT RAISE(ABORT,'provisioning receipt is append only'); END;
