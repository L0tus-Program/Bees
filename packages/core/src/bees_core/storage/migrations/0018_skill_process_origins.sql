-- Origem da conversão humana. Não concede autorização nem copia o resultado privado.
CREATE UNIQUE INDEX idx_skills_id_agent ON skills(id,agent_id);
CREATE TABLE skill_process_origins (
    skill_id TEXT NOT NULL,
    version INTEGER NOT NULL CHECK(version > 0),
    agent_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    message_id TEXT NOT NULL REFERENCES messages(id) ON DELETE RESTRICT,
    result_sha256 TEXT NOT NULL CHECK(length(result_sha256)=64),
    review_json TEXT NOT NULL CHECK(json_valid(review_json)),
    created_at TEXT NOT NULL,
    actor TEXT NOT NULL CHECK(actor='user'),
    PRIMARY KEY(skill_id,version),
    FOREIGN KEY(skill_id,version) REFERENCES skill_revisions(skill_id,version),
    FOREIGN KEY(skill_id,agent_id) REFERENCES skills(id,agent_id),
    FOREIGN KEY(task_id,agent_id) REFERENCES tasks(id,agent_id),
    FOREIGN KEY(run_id,task_id) REFERENCES runs(id,task_id)
) STRICT;
CREATE TRIGGER skill_process_origin_insert BEFORE INSERT ON skill_process_origins
WHEN NOT EXISTS (
    SELECT 1 FROM tasks t JOIN runs r ON r.task_id=t.id
    JOIN messages m ON m.conversation_id=t.conversation_id
        AND m.id=json_extract(r.checkpoint_json,'$.result_message_id')
    WHERE t.id=NEW.task_id AND t.agent_id=NEW.agent_id AND r.id=NEW.run_id
        AND r.status='completed' AND m.id=NEW.message_id AND m.role='assistant'
)
BEGIN SELECT RAISE(ABORT,'skill process origin must reference a completed result'); END;
CREATE TRIGGER skill_process_origin_no_update BEFORE UPDATE ON skill_process_origins
BEGIN SELECT RAISE(ABORT,'skill process origin is immutable'); END;
CREATE TRIGGER skill_process_origin_no_delete BEFORE DELETE ON skill_process_origins
BEGIN SELECT RAISE(ABORT,'skill process origin is append-only'); END;
