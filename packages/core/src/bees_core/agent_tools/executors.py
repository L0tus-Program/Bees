"""Alvos remotos (computador próprio e máquina pessoal).

Implementação completa em ``bees_core.executors``; este módulo mantém a fronteira
importável pelo resolvedor de alvos sem dependências circulares.
"""

from bees_core.agent_tools.catalog import ToolSpec
from bees_core.tools import ToolError


def resolve_executor_target(unit, agent_id, spec: ToolSpec):
    try:
        from bees_core.executors import resolve_target
    except ImportError:
        raise ToolError("environment_unavailable", "Ambiente de execução indisponível.") from None
    return resolve_target(unit, agent_id, spec)


def require_online(unit, agent_id, spec: ToolSpec, target) -> None:
    try:
        from bees_core.executors import require_online as check
    except ImportError:
        raise ToolError("environment_unavailable", "Ambiente de execução indisponível.") from None
    check(unit, agent_id, spec, target)
