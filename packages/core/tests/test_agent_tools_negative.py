"""Negativos do agente: autoridade injetada em argumentos e precedência de bloqueio."""

from uuid import uuid4

from test_agent_tools import Case, tool_calls
from test_tasks_execution import answer

from bees_core.memory import MemoryService
from bees_core.models import Policy


def test_authority_fields_in_arguments_are_rejected_without_effect(tmp_path):
    case = Case(
        tmp_path,
        [
            tool_calls(
                (
                    "i1",
                    "memory_save",
                    {
                        "content": "A",
                        "environment_id": "personal:outro",
                        "identity": "root",
                        "agent_id": str(uuid4()),
                    },
                )
            ),
            answer("Fim."),
        ],
    )
    case.drain()
    [result] = case.results()
    assert result["status"] == "rejected"
    assert MemoryService(case.database).list_for_agent(case.agent.id) == []
    assert case.service.list(case.agent.id) == []


def test_deny_rule_wins_over_allow_tool_and_skips_question(tmp_path):
    case = Case(
        tmp_path,
        [tool_calls(("d1", "memory_save", {"content": "segredo"})), answer("Fim.")],
    )
    with case.store.transaction() as unit:
        unit.policies.create(
            Policy(
                agent_id=case.agent.id,
                name="Nunca salvar memória",
                effect="deny",
                scope={"tool_name": "memory.save", "action": "save"},
            )
        )
        unit.policies.create(
            Policy(
                agent_id=case.agent.id,
                name="Sempre permitir",
                effect="allow",
                scope={"tool_name": "memory.save", "action": "save"},
            )
        )
    case.drain()
    [result] = case.results()
    assert result["status"] == "rejected"
    assert MemoryService(case.database).list_for_agent(case.agent.id) == []
    assert case.service.list(case.agent.id) == []
