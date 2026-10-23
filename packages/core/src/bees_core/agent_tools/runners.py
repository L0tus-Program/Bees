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


async def run_web(spec: ToolSpec, arguments, context: RunContext) -> Outcome:
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
