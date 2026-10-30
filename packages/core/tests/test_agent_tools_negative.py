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


def test_invalid_arguments_tell_the_model_which_field_and_what_to_do(tmp_path):
    case = Case(
        tmp_path,
        [tool_calls(("i1", "memory_save", {"conteudo": "A"})), answer("Fim.")],
    )
    case.drain()
    [result] = case.results()
    assert result["code"] == "invalid_tool_arguments"
    # Nome do campo e motivo, sem ecoar o valor enviado.
    assert "content" in result["detail"] and "conteudo" in result["detail"]
    assert '"A"' not in result["detail"]
    assert result["hint"]


def test_browser_url_without_scheme_is_completed():
    from bees_core.agent_tools.catalog import BrowserOpenInput

    assert BrowserOpenInput(url="g1.com.br").url == "https://g1.com.br"
    assert BrowserOpenInput(url="http://exemplo.com").url == "http://exemplo.com"


def test_failures_and_unknown_outcomes_carry_a_next_step():
    from types import SimpleNamespace

    from bees_core.agent_tools.cycle import AgentTaskWorker

    failed = SimpleNamespace(
        status="failed_no_effect", id=uuid4(), result={"error": "element_not_found"}
    )
    assert "snapshot" in AgentTaskWorker._effect_payload(failed)["hint"]
    other = SimpleNamespace(status="failed_no_effect", id=uuid4(), result={"error": "raro"})
    assert "hint" not in AgentTaskWorker._effect_payload(other)
    unknown = SimpleNamespace(status="outcome_unknown", id=uuid4(), result=None)
    assert AgentTaskWorker._effect_payload(unknown)["hint"]
