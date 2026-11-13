# bees-executor

Executor remoto do Bees: roda o computador próprio da abelha (container `computer`) e o conector da máquina pessoal. Conecta-se à API do Bees por WebSocket com credencial própria obtida de um convite de uso único.

Não publicado em índices públicos. Instale a partir do checkout (`pip install ./apps/executor`) ou da wheel do CI com `SHA256SUMS`. Veja `docs/connector.md` e `docs/computer.md`.

Requer Python 3.12+ e `websockets>=15,<16`. Pareamento, comandos e visualização
usam conexão direta à API, sem proxies descobertos do ambiente/sistema e sem
seguir redirecionamentos. A API deve ser alcançável diretamente pelo executor.
