"""Resolução confiável dos alvos de execução das ferramentas.

O alvo identifica onde o efeito acontece (rede pública a partir do worker,
plano de controle, computador próprio da abelha ou máquina pessoal pareada).
Indisponibilidade nunca troca silenciosamente de alvo.
"""

from uuid import UUID

from bees_core.agent_tools.catalog import ToolSpec
from bees_core.tools import ToolError

WEB = "web"
CONTROL_PLANE = "control_plane"


class TargetResolver:
    """Alvos de computador e pessoal são resolvidos pelos serviços de executores."""

    def resolve(self, unit, agent_id: UUID, spec: ToolSpec):
        from bees_core.agent_tools.service import ToolTarget

        if spec.target == "web" and spec.name.startswith("mcp."):
            # Conectores MCP só aparecem quando a abelha tem servidor pronto.
            if not unit.agents._context.connection.execute(
                "SELECT 1 FROM mcp_servers WHERE agent_id=? AND enabled=1 AND status='ready'",
                (str(agent_id),),
            ).fetchone():
                raise ToolError("environment_unavailable", "Nenhum conector MCP pronto.")
            return ToolTarget(WEB, WEB, f"mcp:{agent_id}", "bees_user")
        if spec.target == "web":
            return ToolTarget(WEB, WEB, "web:public", "anonymous")
        if spec.target == "control_plane":
            return ToolTarget(CONTROL_PLANE, CONTROL_PLANE, f"agent:{agent_id}", "bees_user")
        from bees_core.agent_tools.executors import resolve_executor_target

        return resolve_executor_target(unit, agent_id, spec)

    def check(self, unit, agent_id: UUID, spec: ToolSpec, target) -> None:
        """Alvo do snapshot continua o mesmo que a composição resolveria agora."""
        if target.kind != spec.target:
            raise ToolError("target_changed", "Alvo diferente do contrato da ferramenta.")
        current = self.resolve(unit, agent_id, spec)
        if current != target:
            raise ToolError("target_changed", "Ambiente mudou desde o preparo.")

    def ready(self, unit, agent_id: UUID, spec: ToolSpec, target) -> None:
        """Guarda imediatamente antes do início; executores verificam conexão atual."""
        if spec.target in ("computer", "personal"):
            from bees_core.agent_tools.executors import require_online

            require_online(unit, agent_id, spec, target)
