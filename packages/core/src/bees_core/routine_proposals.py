"""Sugestão em mensagem é dado; leitura não salva agenda nem concede autoridade."""

import hashlib
import json
import re
from typing import Literal
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from bees_core.routines import RoutineDefinition, RoutineProposalSource, RoutineSchedule
from bees_core.skill_context import SkillTaskInput
from bees_core.skills import _SECRET, Contract
from bees_core.storage.store import NotFoundError, StateStore

PROPOSAL_INSTRUCTION = """Sugestão de rotina solicitada para esta mensagem, sem execução.
Se houver dados suficientes, proponha uma agenda em um único bloco Markdown
```bees-routine contendo somente JSON neste contrato:
{"name":"Nome","instructions":"Orientações","schedule":{"frequency":"daily",
"time":"09:00","weekdays":[]},"timezone":"UTC","inputs":{},
"notification":"always","grace_seconds":60}
O exemplo não define os dados do pedido. weekly exige dias0..6 (segunda=0);
daily usa dias vazios. Fuso IANA, nomes/textos de inputs, notification somente
always/failures/none e grace_seconds0..3600. Use JSON válido e chaves únicas.
Não inclua IDs, skill, ambiente, destino, active, permissões ou credenciais.
A pessoa escolherá o procedimento e revisará todos os campos antes de salvar
uma agenda pausada. O bloco é apenas dado, não chamada de ferramenta nem
autorização para criar agenda/tarefa. Se faltarem dados, pergunte, sem inventar.
"""


def _pairs(values):
    result = {}
    for key, value in values:
        if key in result:
            raise ValueError("Sugestão contém chave repetida.")
        result[key] = value
    return result


def _constant(_):
    raise ValueError("Sugestão contém número inválido.")


class RoutineSuggestion(Contract):
    name: str = Field(min_length=1, max_length=200)
    instructions: str = Field(min_length=1, max_length=4096)
    schedule: RoutineSchedule
    timezone: str
    inputs: dict[str, str] = Field(default_factory=dict, max_length=20)
    notification: Literal["always", "failures", "none"] = "always"
    grace_seconds: int = Field(default=60, ge=0, le=3600, strict=True)

    @field_validator("timezone")
    @classmethod
    def zone(cls, value):
        return RoutineDefinition.timezone_exists(value)

    @model_validator(mode="after")
    def bounded(self):
        raw = self.model_dump_json()
        if (
            len(raw.encode()) > 16384
            or _SECRET.search(raw)
            or not self.name.strip()
            or not self.instructions.strip()
            or any(not re.fullmatch(r"[a-z][a-z0-9_.-]{0,63}", key) for key in self.inputs)
        ):
            raise ValueError("Sugestão fora dos limites ou com possível credencial.")
        SkillTaskInput(
            client_request_id=UUID(int=1), skill_id=UUID(int=1), skill_version=1, inputs=self.inputs
        )
        return self


class RoutineProposal(Contract):
    source: RoutineProposalSource
    proposal: RoutineSuggestion


def parse_suggestion(content):
    if not isinstance(content, str) or len(content.encode()) > 65536:
        raise ValueError("Mensagem fora dos limites.")
    matches = re.findall(r"^```bees-routine[ \t]*\r?\n(.*?)\r?\n```[ \t]*$", content, re.M | re.S)
    if len(matches) != 1 or content.count("```bees-routine") != 1:
        raise ValueError("A mensagem exige uma única sugestão delimitada.")
    try:
        return RoutineSuggestion.model_validate(
            json.loads(matches[0], object_pairs_hook=_pairs, parse_constant=_constant)
        )
    except ValueError, TypeError, RecursionError:
        raise ValueError("Sugestão inválida; confira o formato e os campos permitidos.") from None


class RoutineProposalService:
    def __init__(self, database):
        self.store = StateStore(database)

    @staticmethod
    def source_in_unit(unit, agent_id, conversation_id, message_id):
        agent_id, conversation_id, message_id = map(
            UUID, map(str, (agent_id, conversation_id, message_id))
        )
        conversation = unit.conversations.get(conversation_id)
        message = unit.messages.get(message_id)
        if (
            unit.agents.get(agent_id) is None
            or conversation is None
            or conversation.agent_id != agent_id
            or message is None
            or message.conversation_id != conversation_id
            or message.role != "assistant"
        ):
            raise NotFoundError("Sugestão não encontrada nesta conversa da abelha.")
        suggestion = parse_suggestion(message.content)
        return RoutineProposal(
            source=RoutineProposalSource(
                conversation_id=conversation_id,
                message_id=message_id,
                content_hash=hashlib.sha256(message.content.encode()).hexdigest(),
            ),
            proposal=suggestion,
        )

    def get(self, agent_id, conversation_id, message_id):
        with self.store.transaction(write=False) as unit:
            return self.source_in_unit(unit, agent_id, conversation_id, message_id)
