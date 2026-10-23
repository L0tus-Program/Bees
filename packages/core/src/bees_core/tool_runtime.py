"""Recurso local preparado pelo operador; caminhos nunca vêm do HTTP ou modelo."""

import os
from pathlib import Path
from uuid import UUID, uuid4

from bees_core.agent_tools.cycle import AgentTaskWorker
from bees_core.executor_transport.client import ProcessTextResource
from bees_core.executor_transport.contracts import TransportError
from bees_core.executor_transport.journal import private
from bees_core.models import utc_now
from bees_core.providers.errors import ProviderError
from bees_core.providers.service import ProviderService
from bees_core.resource_tools import MODEL_TASK_KIND
from bees_core.security.hosts import HostService
from bees_core.storage.store import NotFoundError, StateStore
from bees_core.tasks import TaskError
from bees_core.tool_cycle import ToolTaskWorker
from bees_core.tools import CATALOG_MANIFEST, ToolError, ToolService, digest


def resource_path(database, agent_id):
    return Path(database.path).absolute().parent / "text-resources" / str(UUID(str(agent_id)))


def initialize_resource(database, agent_id):
    """Operador local cria somente um journal/processo de texto, sem grants ou convites."""
    agent_id = UUID(str(agent_id))
    if os.name != "posix":
        raise TransportError("executor_platform_unavailable")
    if not Path(database.path).is_file():
        raise TransportError("executor_journal_missing")
    database.require_current_schema()
    store = StateStore(database)
    with store.transaction(source="operator_text_resource") as unit:
        agent = ProviderService._agent(unit, agent_id)
        if (
            agent.status != "active"
            or not unit.agents._context.connection.execute(
                "SELECT EXISTS(SELECT 1 FROM identity_users)"
            ).get
        ):
            raise TaskError("unsupported_capability")
        installation = HostService._installation(unit.agents._context.connection)
        if installation is None:
            installation = str(uuid4())
            unit.agents._context.connection.execute(
                "INSERT INTO host_link_installation VALUES(1,?,?)",
                (installation, utc_now().timestamp()),
            )
    path = resource_path(database, agent_id)
    private(path.parent.parent, directory=True)
    if not path.parent.exists():
        try:
            path.parent.mkdir(mode=0o700)
        except FileExistsError:
            pass
    private(path.parent, directory=True)
    if path.exists() or path.is_symlink():
        resource = ProcessTextResource.open(path)
    else:
        resource = ProcessTextResource.initialize(path, installation, str(agent_id))
    if resource.identity.installation_id != installation or resource.identity.agent_id != str(
        agent_id
    ):
        raise TransportError("executor_binding_mismatch")
    return resource


def available_runtime(database, agent_id):
    """Sondagem somente leitura; não cria diretório, instalação ou permissão."""
    resource = ProcessTextResource.open(resource_path(database, agent_id))
    with StateStore(database).transaction(write=False) as unit:
        agent = ProviderService._agent(unit, agent_id)
        if (
            agent.status != "active"
            or not ProviderService._validated_config(agent.provider_config).capabilities.tool_calls
            or resource.identity.agent_id != str(agent.id)
            or HostService._installation(unit.agents._context.connection)
            != resource.identity.installation_id
        ):
            raise TaskError("unsupported_capability")
        manifest_hash = digest(CATALOG_MANIFEST.model_dump(mode="json"))
        row = unit.agents._context.connection.execute(
            "SELECT p.id FROM plugins p JOIN tool_grants g ON g.plugin_id=p.id "
            "WHERE p.enabled=1 AND g.enabled=1 AND g.agent_id=? "
            "AND g.tool_name='text.normalize' AND p.manifest_hash=? ORDER BY p.id LIMIT 1",
            (str(agent.id), manifest_hash),
        ).get
        if row is None:
            raise TaskError("unsupported_capability")
        plugin = unit.plugins.get(row)
        if ToolService._manifest(plugin) != CATALOG_MANIFEST:
            raise TaskError("unsupported_capability")
        return resource, plugin.id


class ToolQueueWorker(AgentTaskWorker):
    """Fila comum escolhe composição pelo modo canônico; não inicializa recursos."""

    async def _run_claim(self, claim, generation):
        if claim.task.metadata.get("execution_kind") != MODEL_TASK_KIND:
            return await super()._run_claim(claim, generation)
        try:
            resource = ProcessTextResource.open(
                resource_path(self.store.database, claim.task.agent_id)
            )
            plugin_id = UUID(claim.task.metadata["tool_cycle_binding"]["plugin_id"])
            worker = ToolTaskWorker(
                self.store.database,
                resource,
                plugin_id,
                self.providers.resolver,
                transport=self.providers.transport,
                owner_id=self.owner_id,
                poll_seconds=self.poll_seconds,
                lease_seconds=self.lease_seconds,
                artifacts=self.artifacts,
            )
        except KeyError, ValueError, TransportError, ToolError, NotFoundError, ProviderError:
            with self.store.transaction(actor="worker", source="tool_runtime_unavailable") as unit:
                unit.execution.assert_claim(claim, now=utc_now())
                self._settle_local(unit, claim, code="unsupported_capability")
            return True
        return await worker._run_claim(claim, generation)
