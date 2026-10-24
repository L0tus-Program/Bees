-- Recibo tardio de comando já desconhecido: registrado uma única vez como evidência,
-- sem mudar o estado nem os dados do comando.
DROP TRIGGER executor_commands_terminal;
CREATE TRIGGER executor_commands_terminal BEFORE UPDATE ON executor_commands
WHEN OLD.status IN ('confirmed','failed','unknown','expired')
  AND NOT (
    OLD.status='unknown' AND NEW.status='unknown' AND OLD.result_json IS NULL
    AND NEW.executor_id IS OLD.executor_id AND NEW.computer_id IS OLD.computer_id
    AND NEW.agent_id IS OLD.agent_id AND NEW.tool_name IS OLD.tool_name
    AND NEW.arguments_json IS OLD.arguments_json AND NEW.sent_at IS OLD.sent_at
    AND NEW.finished_at IS OLD.finished_at AND NEW.error_code LIKE 'late_%'
  )
BEGIN SELECT RAISE(ABORT,'executor command is terminal'); END;
