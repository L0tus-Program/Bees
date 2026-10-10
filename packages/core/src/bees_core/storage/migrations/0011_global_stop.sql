-- Parada global durável: uma linha de estado e comandos humanos idempotentes. Aditiva.
-- A geração aumenta a cada parada; fluxos iniciados antes dela não seguem nem após retomar.
CREATE TABLE safety_control (
    id INTEGER PRIMARY KEY CHECK(id = 1),
    status TEXT NOT NULL CHECK(status IN ('running','stopped')),
    generation INTEGER NOT NULL CHECK(generation >= 0),
    revision INTEGER NOT NULL CHECK(revision > 0),
    changed_at TEXT NOT NULL,
    reason TEXT CHECK(reason IS NULL OR length(reason) BETWEEN 1 AND 500)
) STRICT;

INSERT INTO safety_control(id,status,generation,revision,changed_at)
VALUES(1,'running',0,1,'1970-01-01T00:00:00+00:00');

CREATE TABLE safety_commands (
    id TEXT PRIMARY KEY NOT NULL,
    client_request_id TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL CHECK(kind IN ('stop','resume')),
    expected_revision INTEGER NOT NULL CHECK(expected_revision > 0),
    resulting_revision INTEGER NOT NULL CHECK(resulting_revision = expected_revision + 1),
    actor TEXT NOT NULL CHECK(length(actor) BETWEEN 1 AND 64),
    reason TEXT CHECK(reason IS NULL OR length(reason) BETWEEN 1 AND 500),
    created_at TEXT NOT NULL
) STRICT;

CREATE TRIGGER safety_control_transition BEFORE UPDATE ON safety_control
WHEN NEW.revision <> OLD.revision + 1 OR NEW.status = OLD.status
 OR (NEW.status = 'stopped' AND NEW.generation <> OLD.generation + 1)
 OR (NEW.status = 'running' AND NEW.generation <> OLD.generation)
BEGIN SELECT RAISE(ABORT,'safety control transition is invalid'); END;

CREATE TRIGGER safety_control_no_delete BEFORE DELETE ON safety_control
BEGIN SELECT RAISE(ABORT,'safety control cannot be deleted'); END;

CREATE TRIGGER safety_commands_immutable BEFORE UPDATE ON safety_commands
BEGIN SELECT RAISE(ABORT,'safety commands are immutable'); END;

CREATE TRIGGER safety_commands_no_delete BEFORE DELETE ON safety_commands
BEGIN SELECT RAISE(ABORT,'safety commands are append-only'); END;

-- Contagem do que já foi enviado, consultada pela interface enquanto parado.
CREATE INDEX idx_calls_in_flight ON model_calls(status) WHERE status = 'dispatch_started';
CREATE INDEX idx_usage_reserved ON usage_entries(source) WHERE status = 'reserved';

-- Geração da parada vigente quando o claim foi criado; NULL é anterior a esta migração
-- (geração 0). Executor com geração antiga não continua nem depois de uma retomada.
ALTER TABLE provisioning_claims ADD COLUMN safety_generation INTEGER
 CHECK(safety_generation IS NULL OR safety_generation >= 0);

CREATE TRIGGER provisioning_claim_safety_immutable BEFORE UPDATE ON provisioning_claims
WHEN NEW.safety_generation IS NOT OLD.safety_generation
BEGIN SELECT RAISE(ABORT,'provisioning claim is immutable'); END;
