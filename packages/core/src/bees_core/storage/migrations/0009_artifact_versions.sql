-- Linhagem de versões e imutabilidade dos artefatos publicados. Aditiva: linhas
-- anteriores ficam sem série/anterior e preservam seus metadados.
ALTER TABLE artifacts ADD COLUMN series_id TEXT
    CHECK(series_id IS NULL OR length(series_id)=36);
ALTER TABLE artifacts ADD COLUMN previous_id TEXT REFERENCES artifacts(id) ON DELETE RESTRICT
    CHECK(previous_id IS NULL OR (series_id IS NOT NULL AND previous_id <> id));

CREATE UNIQUE INDEX idx_artifacts_series_version ON artifacts(series_id,version)
    WHERE series_id IS NOT NULL;
CREATE UNIQUE INDEX idx_artifacts_storage_key ON artifacts(storage_key)
    WHERE storage_key IS NOT NULL;
CREATE INDEX idx_artifacts_previous ON artifacts(previous_id)
    WHERE previous_id IS NOT NULL;

CREATE TRIGGER artifacts_lineage_immutable BEFORE UPDATE ON artifacts
WHEN NEW.task_id IS NOT OLD.task_id OR NEW.run_id IS NOT OLD.run_id
 OR NEW.version IS NOT OLD.version OR NEW.series_id IS NOT OLD.series_id
 OR NEW.previous_id IS NOT OLD.previous_id
 OR (OLD.storage_key IS NOT NULL AND NEW.storage_key IS NOT OLD.storage_key)
BEGIN SELECT RAISE(ABORT,'artifact lineage is immutable'); END;

CREATE TRIGGER artifacts_ready_immutable BEFORE UPDATE ON artifacts
WHEN OLD.status='ready' AND (NEW.status NOT IN ('ready','deleted')
 OR NEW.sha256 IS NOT OLD.sha256 OR NEW.size_bytes IS NOT OLD.size_bytes
 OR NEW.media_type IS NOT OLD.media_type)
BEGIN SELECT RAISE(ABORT,'ready artifact content is immutable'); END;

CREATE TRIGGER artifacts_terminal_immutable BEFORE UPDATE ON artifacts
WHEN OLD.status IN ('failed','deleted')
BEGIN SELECT RAISE(ABORT,'terminal artifact is immutable'); END;

CREATE TRIGGER artifacts_no_delete BEFORE DELETE ON artifacts
BEGIN SELECT RAISE(ABORT,'artifact rows are append-only'); END;
