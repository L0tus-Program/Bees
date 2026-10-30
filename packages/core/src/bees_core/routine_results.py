"""Read-only classification of an actual, canonically bound routine result."""

from bees_core.agent_tools import research

QUIET_MARK = "SEM_NOVIDADES"


def quiet(content: str | None) -> bool:
    return (content or "").strip().strip(".").upper() == QUIET_MARK


def quiet_completion(connection, task, run_id) -> bool:
    if task.routine_id is None or task.control_revision > 0:
        return False
    bound = connection.execute(
        "SELECT json_extract(d.definition_json,'$.execution_mode') "
        "FROM routine_occurrences o JOIN routine_definitions d "
        "ON d.routine_id=o.routine_id AND d.revision=o.routine_revision "
        "JOIN routines r ON r.id=o.routine_id "
        "WHERE o.task_id=? AND o.routine_id=? AND r.agent_id=?",
        (str(task.id), str(task.routine_id), str(task.agent_id)),
    ).fetchone()
    if bound is None or bound[0] != "agent":
        return False
    result = connection.execute(
        "SELECT m.content FROM model_calls c JOIN runs r ON r.id=c.run_id "
        "JOIN messages m ON m.id=c.output_message_id WHERE r.task_id=? AND c.run_id=? "
        "AND c.status='confirmed' AND m.role='assistant' AND m.conversation_id=? "
        "ORDER BY c.ordinal DESC LIMIT 1",
        (str(task.id), str(run_id), str(task.conversation_id)),
    ).fetchone()
    if result is None:
        return False
    if quiet(result[0]):
        return True
    if not task.metadata.get("research"):
        return False
    try:
        observed = research.sources_snapshot(connection, task.id)
        prior, previous = research.previous_fingerprint_snapshot(
            connection, task, research.relevance_key(task)
        )
        return bool(observed) and prior is not None and previous == research.fingerprint(observed)
    except ValueError, TypeError, KeyError, AttributeError:
        # Unusable evidence cannot be a reason to hide a completion notice.
        return False
