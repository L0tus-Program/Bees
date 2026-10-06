-- O vínculo com o executor é persistido antes do efeito, não contém argumentos.
ALTER TABLE actions ADD COLUMN execution_binding_json TEXT
    CHECK(execution_binding_json IS NULL OR
          (json_valid(execution_binding_json) AND json_type(execution_binding_json)='object'));
ALTER TABLE actions ADD COLUMN unknown_acknowledged_at TEXT
    CHECK(unknown_acknowledged_at IS NULL OR
          status IN ('outcome_unknown','confirmed','failed_no_effect'));

CREATE INDEX actions_unresolved_run ON actions(run_id,status,unknown_acknowledged_at);

CREATE TRIGGER actions_execution_binding_immutable
BEFORE UPDATE ON actions
WHEN NEW.execution_binding_json IS NOT OLD.execution_binding_json
BEGIN SELECT RAISE(ABORT, 'action execution binding immutable'); END;

CREATE TRIGGER actions_acknowledgement_immutable
BEFORE UPDATE ON actions
WHEN OLD.unknown_acknowledged_at IS NOT NULL
 AND NEW.unknown_acknowledged_at IS NOT OLD.unknown_acknowledged_at
BEGIN SELECT RAISE(ABORT, 'action acknowledgement immutable'); END;
