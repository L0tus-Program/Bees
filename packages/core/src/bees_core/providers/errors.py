"""Falhas públicas estáveis, sem URLs, chaves ou bodies de fornecedores."""

from typing import Literal

ErrorCode = Literal[
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
]

MESSAGES: dict[str, str] = {
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
        super().__init__(self.message)
