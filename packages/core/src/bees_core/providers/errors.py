"""Falhas públicas estáveis, sem URLs, chaves ou bodies de fornecedores."""

import re
import unicodedata
from collections.abc import Iterable
from typing import Literal

RESPONSE_ISSUES = frozenset(
    {
        "invalid_json",
        "content_type",
        "content_encoding",
        "choices",
        "message",
        "tool_calls",
        "tool_call",
        "tool_arguments_json",
        "tool_arguments_schema",
        "tool_not_offered",
        "duplicate_tool_call",
        "finish_reason",
        "content",
        "usage",
        "error_envelope",
    }
)


def public_provider_message(value: object, private: Iterable[str] = ()) -> str | None:
    """Trecho diagnóstico, sem credenciais, URLs ou conteúdo refletido do pedido."""
    if not isinstance(value, str) or not value.strip() or len(value) > 8192:
        return None
    for secret in sorted(set(private), key=len, reverse=True):
        if secret:
            value = value.replace(secret, "[redacted]")
    value = re.sub(r"(?i)\b(?:https?://|data:)[^\s<>\"']+", "[redacted]", value)
    value = re.sub(r"(?i)\bbearer\s+[^\s,;\"']+", "Bearer [redacted]", value)
    value = re.sub(r"\b(?:sk-|sk_|ghp_|github_pat_)[A-Za-z0-9_-]+", "[redacted]", value)
    value = re.sub(
        r"(?i)\b(api[_ -]?key|authorization|password|secret|token)\s*[:=]\s*[^\s,;]+",
        r"\1=[redacted]",
        value,
    )
    value = "".join(" " if unicodedata.category(char).startswith("C") else char for char in value)
    value = " ".join(value.split())
    return value[:1000] + ("…" if len(value) > 1000 else "")


ErrorCode = Literal[
    "image_vision_required",
    "image_ocr_unavailable",
    "image_ocr_empty",
    "image_ocr_failed",
    "invalid_config",
    "invalid_request",
    "unsupported_capability",
    "secret_unavailable",
    "authentication_failed",
    "access_denied",
    "rate_limited",
    "model_unavailable",
    "local_model_required",
    "timeout",
    "connection_failed",
    "response_too_large",
    "request_too_large",
    "invalid_response",
    "provider_unavailable",
    "redirect_refused",
    "provider_rejected",
    "tool_schema_rejected",
    "model_tools_unsupported",
    "unsupported_model_parameter",
    "context_window_exceeded",
    "invalid_secret_reference",
    "invalid_secret",
    "invalid_history",
    "conversation_scope",
    "conversation_inactive",
    "agent_inactive",
    "state_conflict",
    "chat_outcome_unknown",
    "policy_denied",
    "policy_approval_required",
    "budget_exhausted",
    "global_stop",
    "task_limit_reached",
    "tool_result_too_large",
    "skill_unavailable",
    "routine_unavailable",
]

MESSAGES: dict[str, str] = {
    "image_vision_required": (
        "Este modelo não está configurado com visão. Habilite visão para um modelo "
        "compatível ou envie novamente para usar OCR local."
    ),
    "image_ocr_unavailable": (
        "O modelo é textual e o OCR local não está disponível. Instale Tesseract com o "
        "idioma inglês no servidor Bees ou configure um modelo com visão."
    ),
    "image_ocr_empty": (
        "O OCR local não encontrou texto na imagem. Use um modelo com visão para analisar"
        " seu conteúdo visual."
    ),
    "image_ocr_failed": (
        "O OCR local não conseguiu ler esta imagem. Tente outra imagem ou um modelo com visão."
    ),
    "routine_unavailable": "A rotina ou sua ocorrência mudou; revise a tarefa antes de continuar.",
    "skill_unavailable": (
        "A revisão ou as entradas da skill não estão mais disponíveis para executar."
    ),
    "task_limit_reached": "A tarefa atingiu seu limite de chamadas, operações ou tempo.",
    "tool_result_too_large": (
        "O resultado da ferramenta não cabe no próximo contexto; nenhuma nova geração foi iniciada."
    ),
    "invalid_config": "Configuração do provedor inválida.",
    "invalid_request": "Histórico ou ferramentas inválidos para o contrato de conversa.",
    "unsupported_capability": "O backend não atende às capacidades solicitadas.",
    "secret_unavailable": "A referência de credencial não está disponível.",
    "authentication_failed": "O provedor recusou a credencial.",
    "access_denied": "O provedor não autorizou acesso ao recurso solicitado.",
    "rate_limited": "O provedor informou limite de uso.",
    "model_unavailable": "O modelo configurado não está disponível.",
    "local_model_required": "Este adaptador exige modelo instalado e executado localmente.",
    "timeout": "O provedor excedeu o prazo configurado.",
    "connection_failed": "Não foi possível conectar ao provedor configurado.",
    "response_too_large": "A resposta excedeu o limite de bytes configurado.",
    "request_too_large": "A requisição excedeu o limite de bytes configurado.",
    "invalid_response": "O provedor retornou resposta incompatível com o contrato.",
    "provider_unavailable": "O provedor está indisponível.",
    "redirect_refused": "Redirecionamento recusado; revise o endpoint explícito.",
    "provider_rejected": "O provedor recusou a requisição.",
    "tool_schema_rejected": (
        "O provedor recusou o formato de uma ferramenta. Revise a compatibilidade do modelo."
    ),
    "model_tools_unsupported": "Este modelo ou endpoint recusou o uso de ferramentas.",
    "unsupported_model_parameter": "O modelo não aceita uma das opções enviadas pelo Bees.",
    "context_window_exceeded": (
        "O histórico e as ferramentas excedem a janela de contexto do modelo."
    ),
    "invalid_secret_reference": "Referência de credencial inválida ou não suportada.",
    "invalid_secret": "Credencial inválida para o transporte configurado.",
    "invalid_history": "Histórico incompatível com o contrato de conversa.",
    "conversation_scope": "Conversa não pertence ao agente solicitado.",
    "conversation_inactive": "A conversa está inativa.",
    "agent_inactive": "O agente está inativo.",
    "state_conflict": "O estado mudou durante a operação; releia antes de continuar.",
    "chat_outcome_unknown": (
        "Este envio não tem resposta confirmada. Consulte o histórico antes de fazer "
        "outro pedido: o provedor pode ter consumido créditos. O Bees não o repete."
    ),
    "policy_denied": "Uma regra do Bees bloqueia esta geração. Revise as regras de autonomia.",
    "policy_approval_required": (
        "Esta geração exige decisão. Revise a regra de autonomia antes de continuar."
    ),
    "budget_exhausted": (
        "O limite de consumo desta abelha não comporta outra geração agora. Revise o limite "
        "ou aguarde a janela; nenhuma geração foi solicitada ao provedor."
    ),
    "global_stop": (
        "O Bees está parado. Nenhuma nova geração foi solicitada ao provedor; retome a "
        "execução antes de enviar novamente."
    ),
}


class ProviderError(RuntimeError):
    def __init__(
        self,
        code: ErrorCode,
        message: str | None = None,
        *,
        retryable: bool = False,
        upstream_status: int | None = None,
        undelivered: bool = False,
        parameter: str | None = None,
        provider_message: str | None = None,
        response_issue: str | None = None,
    ):
        # Não aceitar mensagem externa como texto público, mesmo quando fornecida por engano.
        self.code = code
        self.message = MESSAGES[code]
        self.retryable = retryable
        # Conexão não estabelecida: nenhum byte do pedido chegou ao provedor.
        self.undelivered = undelivered
        if upstream_status is not None and (
            type(upstream_status) is not int or not 400 <= upstream_status <= 599
        ):
            raise ValueError("Status do provedor inválido.")
        self.upstream_status = upstream_status
        # Apenas nomes de campos conhecidos; nunca valores ou mensagens do fornecedor.
        allowed = {
            "tools",
            "tools.function.parameters",
            "tools.function.strict",
            "messages",
            "model",
            "store",
            "stream",
        }
        if parameter is not None and parameter not in allowed:
            raise ValueError("Parâmetro público inválido.")
        self.parameter = parameter
        self.provider_message = public_provider_message(provider_message)
        if response_issue is not None and (
            not isinstance(response_issue, str) or response_issue not in RESPONSE_ISSUES
        ):
            raise ValueError("Motivo de resposta inválido.")
        self.response_issue = response_issue
        super().__init__(self.message)
