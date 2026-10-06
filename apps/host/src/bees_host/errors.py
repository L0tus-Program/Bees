class HostError(RuntimeError):
    """Código público fixo: nunca propaga corpo HTTP, segredos ou saída de processo."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code
