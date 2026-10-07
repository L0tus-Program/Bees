class GuestError(RuntimeError):
    """Códigos fixos; nunca conservar mensagens externas ou conteúdo de frames."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)
