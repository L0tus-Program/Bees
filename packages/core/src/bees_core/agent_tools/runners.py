"""Execução fora da transação para alvos web e plano de controle.

Retorna ``Outcome`` fechado. Falhas observáveis antes de qualquer efeito são
``failed``; leituras falhas também (não há efeito a reconciliar). Somente o
gateway de executores remotos produz ``unknown``.
"""

from dataclasses import dataclass, field
from uuid import UUID, uuid5

from bees_core.agent_tools.catalog import ToolSpec
from bees_core.agent_tools.web import WebClient, WebError

MEDIA = {
    "markdown": ("text/markdown", ".md"),
    "text": ("text/plain", ".txt"),
    "csv": ("text/csv", ".csv"),
    "json": ("application/json", ".json"),
}


@dataclass(frozen=True)
class Outcome:
    status: str  # confirmed | failed | unknown
    result: dict = field(default_factory=dict)
    code: str | None = None
    detail: str | None = None


@dataclass(frozen=True)
class RunContext:
    agent_id: UUID
    task_id: UUID
    run_id: UUID
    action_id: UUID
    database: object
    artifacts: object | None = None
    web: WebClient | None = None
    secrets: object | None = None
    mcp_transport: object | None = None
    provider_transport: object | None = None


async def run_mcp(spec: ToolSpec, arguments, context: RunContext) -> Outcome:
    from bees_core.mcp import McpError, McpService

    service = McpService(context.database, context.secrets, transport=context.mcp_transport)
    try:
        if spec.name == "mcp.list_tools":
            servers = [
                {
                    "server": row["name"],
                    "tools": [
                        {
                            "name": tool["name"],
                            "description": tool["description"][:400],
                            "arguments_schema": tool["input_schema"],
                            "read_only": tool["read_only"],
                        }
                        for tool in row["tools"]
                    ],
                }
                for row in service.list(context.agent_id, enabled_only=True)
                if arguments.server in (None, row["name"])
            ]
            return Outcome("confirmed", {"servers": servers})
        return Outcome(
            "confirmed",
            await service.call(
                context.agent_id, arguments.server, arguments.tool, arguments.arguments
            ),
        )
    except McpError as error:
        if error.code == "mcp_outcome_unknown":
            return Outcome("unknown", code=error.code)
        return Outcome("failed", code=error.code, detail=error.detail)


async def run_web(spec: ToolSpec, arguments, context: RunContext) -> Outcome:
    if spec.name.startswith("mcp."):
        return await run_mcp(spec, arguments, context)
    client = context.web or WebClient()
    try:
        if spec.name == "web.search":
            return Outcome("confirmed", await client.search(arguments.query, arguments.max_results))
        return Outcome("confirmed", await client.fetch(arguments.url, arguments.max_chars))
    except WebError as error:
        return Outcome("failed", code=f"web_{error.code}", detail=error.detail)


def run_control(spec: ToolSpec, arguments, context: RunContext) -> Outcome:
    from bees_core.memory import MemoryService

    memories = MemoryService(context.database)
    if spec.name == "memory.search":
        found = memories.select_context(
            context.agent_id, arguments.query, max_chars=6000, max_items=20
        )
        return Outcome(
            "confirmed",
            {
                "memories": [
                    {"id": str(entry.id), "content": entry.content[:1000]}
                    for entry in found.entries
                ],
                "omitted": found.omitted,
            },
        )
    if spec.name == "memory.save":
        memory = memories.create(
            {
                "scope": "agent",
                "agent_id": context.agent_id,
                "content": arguments.content,
                "kind": "preference",
                "source_ref": f"task:{context.task_id}",
            }
        )
        return Outcome("confirmed", {"memory_id": str(memory.id), "saved": True})
    if spec.name == "deliverable.publish":
        from bees_core.artifacts import ArtifactError

        if context.artifacts is None:
            return Outcome("failed", code="artifact_storage_unavailable")
        media_type, suffix = MEDIA[arguments.kind]
        name = arguments.name if arguments.name.endswith(suffix) else arguments.name + suffix
        try:
            artifact = context.artifacts.create(
                context.agent_id,
                context.task_id,
                name=name,
                media_type=media_type,
                content=arguments.content.encode("utf-8"),
                run_id=context.run_id,
                artifact_id=uuid5(context.action_id, "bees.agent-tool.artifact/v1"),
                metadata={"agent_tool_action_id": str(context.action_id)},
            )
        except ArtifactError as error:
            return Outcome("failed", code=error.code)
        return Outcome(
            "confirmed",
            {
                "artifact_id": str(artifact.id),
                "name": artifact.name,
                "media_type": artifact.media_type,
                "size_bytes": artifact.size_bytes,
                "sha256": artifact.sha256,
            },
        )
    return Outcome("failed", code="tool_unavailable")


TEAM_PROMPT = (
    "Pergunta de outra abelha do mesmo usuário ({origin}). Responda de forma objetiva, "
    "dentro do seu propósito; se não souber, diga.\n\n{question}"
)


def _team_target(unit, origin_id, name: str):
    agents = [
        agent
        for agent in unit.agents.list(limit=1000)
        if agent.id != origin_id and agent.status == "active"
    ]
    wanted = " ".join(name.split()).casefold()
    matches = [agent for agent in agents if " ".join(agent.name.split()).casefold() == wanted]
    return (matches[0] if len(matches) == 1 else None), [agent.name for agent in agents]


async def run_team(spec: ToolSpec, arguments, context: RunContext) -> Outcome:
    from bees_core.agent_tools.cycle import AgentTaskInput, AgentTaskService
    from bees_core.models import Conversation
    from bees_core.providers.errors import ProviderError
    from bees_core.providers.service import ProviderService
    from bees_core.storage.store import StateStore

    store = StateStore(context.database)
    with store.transaction(write=False) as unit:
        origin = unit.agents.get(context.agent_id)
        if spec.name == "bees.list":
            bees = [
                {"name": agent.name, "purpose": agent.purpose[:300]}
                for agent in unit.agents.list(limit=1000)
                if agent.id != context.agent_id and agent.status == "active"
            ]
            return Outcome("confirmed", {"bees": bees})
        target, names = _team_target(unit, context.agent_id, arguments.bee)
        task = unit.tasks.get(context.task_id)
    if target is None:
        return Outcome("failed", code="bee_not_found", detail="Abelhas: " + ", ".join(names)[:500])
    if spec.name == "bees.ask":
        with store.transaction(source="team_consult") as unit:
            consult = next(
                (
                    item
                    for item in unit.conversations.list(agent_id=target.id, limit=1000)
                    if item.metadata.get("consult", {}).get("from_agent_id") == str(origin.id)
                ),
                None,
            )
            if consult is None:
                consult = unit.conversations.create(
                    Conversation(
                        agent_id=target.id,
                        title=f"Consultas de {origin.name}"[:200],
                        metadata={"consult": {"from_agent_id": str(origin.id)}},
                    )
                )
        try:
            response = await ProviderService(
                context.database, context.secrets, transport=context.provider_transport
            ).chat(
                target.id,
                consult.id,
                TEAM_PROMPT.format(origin=origin.name, question=arguments.question),
            )
        except ProviderError as error:
            return Outcome("failed", code=error.code)
        return Outcome(
            "confirmed", {"bee": target.name, "answer": (response.message.content or "")[:12000]}
        )
    origin_chat = (task.metadata.get("agent_chat") or {}).get("conversation_id") if task else None
    with store.transaction(write=False) as unit:
        main = next(
            (
                item
                for item in unit.conversations.list(agent_id=target.id, limit=1000)
                if not item.metadata.get("execution_kind") and not item.metadata.get("consult")
            ),
            None,
        )
    try:
        created = AgentTaskService(context.database).create(
            target.id,
            AgentTaskInput(
                client_request_id=uuid5(context.action_id, "bees.team-delegate/v1"),
                title=f"Pedido de {origin.name}"[:80],
                objective=arguments.task,
                expected_result=arguments.expected_result,
            ),
            metadata={
                **({"agent_chat": {"conversation_id": str(main.id)}} if main else {}),
                "delegated_from": {
                    "agent_id": str(origin.id),
                    "agent_name": origin.name,
                    "task_id": str(context.task_id),
                    "conversation_id": origin_chat,
                },
            },
        )
    except ProviderError as error:
        return Outcome("failed", code=error.code)
    except Exception as error:  # TaskError e validações do destino
        return Outcome("failed", code=getattr(error, "code", "delegation_failed"))
    return Outcome(
        "confirmed",
        {
            "bee": target.name,
            "task_id": str(created.id),
            "status": created.status,
            "note": "O resultado será publicado na conversa quando a outra abelha concluir.",
        },
    )
