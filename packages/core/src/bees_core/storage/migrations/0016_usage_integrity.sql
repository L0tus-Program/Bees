-- BEES-022.5: consumo conserva a identidade chamada -> execução -> tarefa.
-- O preflight não reescreve evidência: qualquer incoerência aborta o upgrade.
CREATE TABLE usage_integrity_preflight (invalid INTEGER NOT NULL) STRICT;
CREATE TRIGGER usage_integrity_preflight_abort BEFORE INSERT ON usage_integrity_preflight
BEGIN
    SELECT RAISE(ABORT,'usage_integrity_legacy_invalid: preserve evidence; operator review required');
END;
INSERT INTO usage_integrity_preflight(invalid)
SELECT 1 FROM usage_entries u
WHERE (u.conversation_id IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM conversations v WHERE v.id=u.conversation_id AND v.agent_id=u.agent_id
)) OR (u.source='task' AND NOT EXISTS (
    SELECT 1 FROM model_calls c JOIN runs r ON r.id=c.run_id JOIN tasks t ON t.id=r.task_id
    WHERE c.id=u.model_call_id AND t.agent_id=u.agent_id
      AND t.conversation_id IS u.conversation_id
));
DROP TRIGGER usage_integrity_preflight_abort;
DROP TABLE usage_integrity_preflight;

CREATE TRIGGER usage_entries_scope_insert BEFORE INSERT ON usage_entries
BEGIN
    SELECT CASE WHEN NEW.conversation_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM conversations v
        WHERE v.id=NEW.conversation_id AND v.agent_id=NEW.agent_id
    ) THEN RAISE(ABORT,'usage_conversation_scope') END;
    SELECT CASE WHEN NEW.source='task' AND NOT EXISTS (
        SELECT 1 FROM model_calls c JOIN runs r ON r.id=c.run_id JOIN tasks t ON t.id=r.task_id
        WHERE c.id=NEW.model_call_id AND t.agent_id=NEW.agent_id
          AND t.conversation_id IS NEW.conversation_id
    ) THEN RAISE(ABORT,'usage_task_scope') END;
END;

-- Verifica também liquidação por SQL direto, inclusive diante de legado alterado
-- fora do serviço. Identidade/reserva/finalidade seguem protegidas pela 0010.
CREATE TRIGGER usage_entries_scope_update BEFORE UPDATE ON usage_entries
BEGIN
    SELECT CASE WHEN NEW.conversation_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM conversations v
        WHERE v.id=NEW.conversation_id AND v.agent_id=NEW.agent_id
    ) THEN RAISE(ABORT,'usage_conversation_scope') END;
    SELECT CASE WHEN NEW.source='task' AND NOT EXISTS (
        SELECT 1 FROM model_calls c JOIN runs r ON r.id=c.run_id JOIN tasks t ON t.id=r.task_id
        WHERE c.id=NEW.model_call_id AND t.agent_id=NEW.agent_id
          AND t.conversation_id IS NEW.conversation_id
    ) THEN RAISE(ABORT,'usage_task_scope') END;
END;

-- Impede que uma alteração nos pais reatribua uma reserva já observada.
CREATE TRIGGER usage_model_call_run_immutable BEFORE UPDATE OF run_id ON model_calls
WHEN NEW.run_id IS NOT OLD.run_id
 AND EXISTS (SELECT 1 FROM usage_entries u WHERE u.model_call_id=OLD.id)
BEGIN SELECT RAISE(ABORT,'usage model call run is immutable'); END;

CREATE TRIGGER usage_task_conversation_immutable BEFORE UPDATE OF conversation_id ON tasks
WHEN NEW.conversation_id IS NOT OLD.conversation_id AND EXISTS (
    SELECT 1 FROM usage_entries u JOIN model_calls c ON c.id=u.model_call_id
    JOIN runs r ON r.id=c.run_id WHERE r.task_id=OLD.id
)
BEGIN SELECT RAISE(ABORT,'usage task conversation is immutable'); END;

CREATE TRIGGER usage_conversation_agent_immutable BEFORE UPDATE OF agent_id ON conversations
WHEN NEW.agent_id IS NOT OLD.agent_id
 AND EXISTS (SELECT 1 FROM usage_entries u WHERE u.conversation_id=OLD.id)
BEGIN SELECT RAISE(ABORT,'usage conversation agent is immutable'); END;
