"""WebSocket autenticado limitado ao endpoint escolhido pelo operador."""

from websockets.asyncio.client import connect


class AuthenticatedConnect(connect):
    def __init__(self, uri: str, **kwargs):
        # A credencial pertence à API escolhida. Nem comandos nem visualização
        # podem descobrir um intermediário no ambiente/configuração do sistema.
        kwargs["proxy"] = None
        super().__init__(uri, **kwargs)

    def process_redirect(self, exc: Exception) -> Exception:
        # Preserva o erro HTTP original para a política do chamador. Nunca
        # reapresenta Authorization em outra URL, nem dentro da mesma origem.
        return exc
