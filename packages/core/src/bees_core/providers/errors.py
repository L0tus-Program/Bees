"""Falhas públicas estáveis, sem URLs, chaves ou bodies de fornecedores."""

from typing import Literal

ErrorCode = Literal[
    "invalid_config",
    "invalid_request",
    "unsupported_capability",
    "secret_unavailable",
    "authentication_failed",
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
    "invalid_secret_reference",
    "invalid_secret",
    "invalid_history",
    "conversation_scope",
    "conversation_inactive",
    "agent_inactive",
    "state_conflict",
]

MESSAGES: dict[str, str] = {
    "invalid_config": "Configuração do provedor inválida.",
    "invalid_request": "Histórico ou ferramentas inválidos para o contrato de conversa.",
    "unsupported_capability": "O backend não atende às capacidades solicitadas.",
    "secret_unavailable": "A referência de credencial não está disponível.",
    "authentication_failed": "O provedor recusou a credencial.",
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
    "invalid_secret_reference": "Referência de credencial inválida ou não suportada.",
    "invalid_secret": "Credencial inválida para o transporte configurado.",
    "invalid_history": "Histórico incompatível com o contrato de conversa.",
    "conversation_scope": "Conversa não pertence ao agente solicitado.",
    "conversation_inactive": "A conversa está inativa.",
    "agent_inactive": "O agente está inativo.",
    "state_conflict": "O estado mudou durante a operação; releia antes de continuar.",
}


class ProviderError(RuntimeError):
    def __init__(self, code: ErrorCode, message: str | None = None, *, retryable: bool = False):
        # Não aceitar mensagem externa como texto público, mesmo quando fornecida por engano.
        self.code = code
        self.message = MESSAGES[code]
        self.retryable = retryable
        super().__init__(self.message)
