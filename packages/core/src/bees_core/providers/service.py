"""Configuração e conversa persistidas, sem execução de ferramentas ou fallback."""

from datetime import timedelta
from typing import Any
from uuid import UUID

import httpx
from pydantic import ValidationError

from bees_core.models import Agent, Conversation, Message, utc_now
from bees_core.providers.base import create_adapter
from bees_core.providers.contracts import (
    CapabilityRequirements,
    ChatMessage,
    ChatRequest,
    ChatResponse,
    Diagnostic,
    ProviderConfig,
    SecretResolver,
    ToolDefinition,
    validate_capabilities,
    validate_request,
)
from bees_core.providers.errors import ProviderError
from bees_core.providers.secrets import EnvSecretResolver
from bees_core.storage.database import Database
from bees_core.storage.store import NotFoundError, RevisionConflict, StateStore, UnitOfWork


class ProviderService:
    """As transações terminam antes de qualquer requisição de rede.

    Entrada validada fica durável mesmo em falha do provedor. Resposta de chamada
    antiga não é anexada se agente, conversa ou histórico mudaram durante a rede.
    A chamada externa já realizada não é desfeita por esse conflito.
    """

    def __init__(
        self,
        database: Database,
        resolver: SecretResolver | None = None,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.store = StateStore(database)
        self.resolver = EnvSecretResolver() if resolver is None else resolver
        self.transport = transport

    @staticmethod
    def _validated_config(value: Any) -> ProviderConfig:
        try:
            data = value.model_dump(mode="python") if isinstance(value, ProviderConfig) else value
            return ProviderConfig.model_validate(data)
        except ValueError, TypeError:
            raise ProviderError("invalid_config") from None

    @staticmethod
    def _agent(uow: UnitOfWork, agent_id: UUID | str) -> Agent:
        agent = uow.agents.get(agent_id)
        if agent is None:
            raise NotFoundError("Agente não encontrado.")
        return agent

    @staticmethod
    def _conversation(uow: UnitOfWork, agent: Agent, conversation_id: UUID | str) -> Conversation:
        conversation = uow.conversations.get(conversation_id)
        if conversation is None:
            raise NotFoundError("Conversa não encontrada.")
        if conversation.agent_id != agent.id:
            raise ProviderError(
                "conversation_scope", "Conversa não pertence ao agente selecionado."
            )
        if conversation.status != "active":
            raise ProviderError(
                "conversation_inactive", "Conversa arquivada não aceita novas chamadas."
            )
        return conversation

    @staticmethod
    def _records(uow: UnitOfWork, conversation_id: UUID) -> list[Message]:
        records: list[Message] = []
        offset = 0
        while True:
            page = uow.messages.list(conversation_id=conversation_id, limit=1000, offset=offset)
            records.extend(page)
            if len(page) < 1000:
                return records
            offset += len(page)

    @staticmethod
    def _normalized(record: Message) -> ChatMessage:
        try:
            raw = record.metadata.get("provider_message")
            normalized = (
                ChatMessage.model_validate(raw)
                if raw is not None
                else ChatMessage(role=record.role, content=record.content)
            )
            if normalized.role != record.role or (normalized.content or "") != record.content:
                raise ValueError("Conteúdo normalizado diverge do registro canônico.")
            return normalized
        except ValueError, TypeError:
            raise ProviderError(
                "invalid_history", "Histórico possui mensagem normalizada inválida ou incompleta."
            ) from None

    @staticmethod
    def _signature(records: list[Message]) -> tuple[str, ...]:
        return tuple(record.model_dump_json() for record in records)

    @staticmethod
    def _requirements(
        history: list[ChatMessage], requirements: CapabilityRequirements | None
    ) -> CapabilityRequirements:
        return CapabilityRequirements(
            text=True,
            tool_calls=bool(requirements and requirements.tool_calls)
            or any(message.tool_calls or message.role == "tool" for message in history),
        )

    def set_config(
        self,
        agent_id: UUID | str,
        config: ProviderConfig,
        *,
        expected_revision: int,
        requirements: CapabilityRequirements | None = None,
    ) -> Agent:
        config = self._validated_config(config)
        with self.store.transaction(source="provider_configuration") as uow:
            agent = self._agent(uow, agent_id)
            if agent.revision != expected_revision:
                raise RevisionConflict("Configuração mudou; releia o agente antes de editar.")
            history: list[ChatMessage] = []
            offset = 0
            while True:
                conversations = uow.conversations.list(agent_id=agent.id, limit=1000, offset=offset)
                for conversation in conversations:
                    history.extend(
                        self._normalized(record) for record in self._records(uow, conversation.id)
                    )
                if len(conversations) < 1000:
                    break
                offset += len(conversations)
            validate_capabilities(config, requirements=self._requirements(history, requirements))
            updated = agent.model_copy(update={"provider_config": config.model_dump(mode="json")})
            return uow.agents.update(updated, expected_revision=expected_revision)

    def load_history(self, agent_id: UUID | str, conversation_id: UUID | str) -> list[ChatMessage]:
        with self.store.transaction(write=False) as uow:
            agent = self._agent(uow, agent_id)
            conversation = self._conversation(uow, agent, conversation_id)
            return [self._normalized(record) for record in self._records(uow, conversation.id)]

    async def diagnose(self, agent_id: UUID | str) -> Diagnostic:
        with self.store.transaction(write=False) as uow:
            agent = self._agent(uow, agent_id)
            config = self._validated_config(agent.provider_config)
        adapter = create_adapter(config.kind, self.resolver, transport=self.transport)
        diagnostic = await adapter.diagnose(config)
        with self.store.transaction(write=False) as uow:
            if self._agent(uow, agent_id).revision != agent.revision:
                raise ProviderError("state_conflict", "Configuração mudou durante o diagnóstico.")
        return diagnostic

    @staticmethod
    def _input(message: ChatMessage | str) -> ChatMessage:
        try:
            normalized = (
                ChatMessage(role="user", content=message)
                if isinstance(message, str)
                else ChatMessage.model_validate(message.model_dump(mode="python"))
            )
            if normalized.role not in ("user", "tool"):
                raise ValueError("Entrada exige papel user ou tool.")
            if normalized.role == "user" and not normalized.content.strip():
                raise ValueError("Entrada de usuário vazia.")
            return normalized
        except ValueError, TypeError, AttributeError:
            raise ProviderError("invalid_request") from None

    @classmethod
    def _inputs(cls, message: ChatMessage | str | list[ChatMessage]) -> list[ChatMessage]:
        if not isinstance(message, list):
            return [cls._input(message)]
        if not 1 <= len(message) <= 64:
            raise ProviderError("invalid_request")
        normalized = [cls._input(item) for item in message]
        if any(item.role != "tool" for item in normalized):
            raise ProviderError("invalid_request")
        return normalized

    @staticmethod
    def _message(
        conversation_id: UUID,
        normalized: ChatMessage,
        records: list[Message],
        *,
        source: str,
        metadata: dict | None = None,
    ) -> Message:
        stamp = utc_now()
        if records and stamp <= records[-1].created_at:
            stamp = records[-1].created_at + timedelta(microseconds=1)
        return Message(
            conversation_id=conversation_id,
            role=normalized.role,
            content=normalized.content or "",
            created_at=stamp,
            updated_at=stamp,
            source=source,
            metadata=(metadata or {}) | {"provider_message": normalized.model_dump(mode="json")},
        )

    async def chat(
        self,
        agent_id: UUID | str,
        conversation_id: UUID | str,
        message: ChatMessage | str | list[ChatMessage],
        *,
        tools: list[ToolDefinition] | None = None,
        requirements: CapabilityRequirements | None = None,
    ) -> ChatResponse:
        normalized_inputs = self._inputs(message)
        with self.store.transaction(source="provider_chat") as uow:
            agent = self._agent(uow, agent_id)
            if agent.status != "active":
                raise ProviderError(
                    "agent_inactive", "Agente pausado ou arquivado não executa chamadas."
                )
            config = self._validated_config(agent.provider_config)
            conversation = self._conversation(uow, agent, conversation_id)
            records = self._records(uow, conversation.id)
            history = [self._normalized(record) for record in records]
            messages = history + normalized_inputs
            if agent.instructions:
                messages.insert(0, ChatMessage(role="system", content=agent.instructions))
            try:
                request = ChatRequest(messages=messages, tools=tools or [])
            except ValidationError:
                raise ProviderError(
                    "invalid_request", "Solicitação de conversa inválida."
                ) from None
            config, request = validate_request(config, request, requirements=requirements)
            # Resolver antes de persistir: credencial ausente não cria chamada pendente.
            if config.secret_ref is not None:
                self.resolver.resolve(config.secret_ref)
            for normalized_input in normalized_inputs:
                input_record = self._message(
                    conversation.id, normalized_input, records, source="user"
                )
                uow.messages.create(input_record)
                records.append(input_record)
            conversation = uow.conversations.update(
                conversation, expected_revision=conversation.revision
            )
            signature = self._signature(records)

        adapter = create_adapter(config.kind, self.resolver, transport=self.transport)
        response = await adapter.complete(config, request)
        try:
            response = ChatResponse.model_validate(response.model_dump(mode="python"))
            if response.message.role != "assistant":
                raise ValueError("Provedor retornou papel incompatível.")
        except ValueError, TypeError, AttributeError:
            raise ProviderError("invalid_response", "Resposta normalizada inválida.") from None

        with self.store.transaction(source="provider_chat") as uow:
            current_agent = self._agent(uow, agent.id)
            current_conversation = self._conversation(uow, current_agent, conversation.id)
            current_records = self._records(uow, conversation.id)
            if (
                current_agent.revision != agent.revision
                or current_agent.status != "active"
                or current_conversation.revision != conversation.revision
                or self._signature(current_records) != signature
            ):
                raise ProviderError(
                    "state_conflict", "Agente ou conversa mudou; resposta antiga não foi gravada."
                )
            output_record = self._message(
                conversation.id,
                response.message,
                current_records,
                source=f"provider:{config.kind}",
                metadata={
                    "provider_kind": config.kind,
                    "model": config.model,
                    "provider_response": {
                        "finish_reason": response.finish_reason,
                        "usage": response.usage.model_dump(mode="json"),
                    },
                },
            )
            uow.messages.create(output_record)
            uow.conversations.update(
                current_conversation, expected_revision=current_conversation.revision
            )
        return response
