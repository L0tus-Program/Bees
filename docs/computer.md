# Computador da abelha

Cada abelha pode ter um computador Linux próprio com desktop, Chromium (sessões persistentes), terminal, LibreOffice e pandoc. A arquitetura está na [ADR 0004](adr/0004-computer-runtime.md); o ciclo de ferramentas, em [agente](agent.md).

## Primeiro uso (Docker, sem terminal)

1. Inicie o Bees normalmente (**Iniciar Bees.vbs** ou `docker compose up -d`). O serviço `computer` sobe junto e aguarda autorização.
2. Na aba **Computador** da abelha, clique **Parear runtime**. O convite é entregue ao runtime por um volume privado; em segundos o status fica **conectado**.
3. Clique **Criar computador**. O runtime cria a conta e o desktop; o status muda para **Pronto**.
4. Em **Ferramentas** (ou no chat, **Ativar ferramentas recomendadas**), instale e conceda o pacote **Computador próprio da abelha** (`bees.computer`).

Fora do Docker, gere o convite pela mesma tela e rode `bees-executor computer --api http://127.0.0.1:8000 --invite <convite> --mode single` (desenvolvimento; sem isolamento de conta).

## Ferramentas

| Função | O que faz | Política padrão |
| --- | --- | --- |
| `computer_list_files`, `computer_read_file` | Lista e lê arquivos do workspace (texto, PDF, DOCX, XLSX) | Permitir |
| `computer_write_file` | Cria/sobrescreve/acrescenta arquivo | Permitir |
| `computer_delete_file` | Remove arquivo/pasta vazia | Perguntar |
| `computer_shell` | Executa bash com prazo (até 600 s) e saída limitada | Perguntar |
| `computer_browser_open/snapshot/scroll/back/screenshot` | Navega e observa (elementos numerados) | Permitir |
| `computer_browser_click/type/press` | Age na página | Perguntar |
| `computer_convert_document` | Converte (MD/HTML/TXT → DOCX/ODT/PDF; CSV → XLSX…) | Permitir |
| `computer_publish_file` | Publica arquivo do workspace como entrega (PDF, DOCX, XLSX, PNG…) | Permitir |

Caminhos relativos partem de `~/workspace`; o executor resolve o caminho real e recusa qualquer alvo fora dele. Campos de senha não são preenchidos pela abelha.

## Ver a tela e assumir o controle

**Ver tela** abre a visualização ao vivo (somente leitura). **Assumir controle** libera teclado/mouse para você e suspende as ferramentas de navegador da abelha — use para fazer login em sites. Ao terminar, **Devolver controle à abelha**. A sessão do navegador permanece no perfil do computador.

## Isolamento e rede

- Uma conta Linux por computador (home `0700`), processo agente e desktop com esse UID; o supervisor não executa ferramentas.
- Filtro de rede no container: as contas das abelhas não alcançam redes privadas, link-local/metadados, CGNAT, a API do Bees nem as portas VNC; a internet pública continua disponível.
- Sem portas publicadas, socket Docker ou pastas do host. Remover o computador apaga conta e arquivos.
- É um container (kernel compartilhado). A VM continua sendo a fronteira mais forte prevista em [ambientes](environments.md).

## Operação

- Volumes: `bees-computers` (homes), `bees-computer-state` (credencial e registro), `bees-computer-link` (convite).
- Revogar o runtime (API `POST /api/v1/executors/{id}/revoke`) encerra a conexão, expira comandos pendentes e marca os computadores como falhos.
- Comandos sem envio até o prazo expiram sem efeito; enviados sem recibo ficam com resultado desconhecido e exigem reconhecimento humano.
