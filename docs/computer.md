# Computador da abelha

Cada abelha pode ter um computador Linux próprio com desktop, Chromium (sessões persistentes), terminal, LibreOffice e pandoc. A arquitetura está na [ADR 0004](adr/0004-computer-runtime.md); o ciclo de ferramentas, em [agente](agent.md).

## Primeiro uso (Docker, sem terminal)

1. Inicie o Bees normalmente (**Iniciar Bees.vbs** ou `docker compose up -d`). O serviço `computer` sobe junto e aguarda autorização.
2. Na aba **Computador** da abelha, clique **Parear runtime**. O convite é entregue ao runtime por um volume privado; em segundos o status fica **conectado**.
3. Clique **Criar computador**. O runtime cria a conta e o desktop; o status muda para **Pronto**.
4. Em **Ferramentas** (ou no chat, **Ativar ferramentas recomendadas**), instale e conceda o pacote **Computador próprio da abelha** (`bees.computer`).

Fora do Docker, gere o convite pela mesma tela e rode `bees-executor computer --api http://127.0.0.1:8000 --invite=<convite> --mode single` (desenvolvimento; sem isolamento de conta).

O runtime mantém uma trava local por diretório `--state` durante espera, pareamento
e conexão. `executor_state_in_use` recusa uma segunda CLI antes de abrir o journal
ou preparar computadores; encerre a instância anterior para reiniciar.
O arquivo `executor.lock` permanece após saída/crash e não deve ser apagado:
o sistema libera a trava automaticamente. `executor_state_unavailable` indica
falha de acesso à trava. Use filesystem local; estados em diretórios diferentes
continuam independentes. A mesma regra vale para o conector pessoal.

A troca do convite recusa redirecionamentos HTTP, inclusive na mesma origem. Configure
`--api` com a origem final do plano de controle; uma resposta 3xx não autoriza consultar
outro destino nem salvar a credencial devolvida por ele. Isso vale também para o
conector pessoal.

Ao reiniciar, o runtime confere a URL salva com a credencial antes da conexão.
Outro `--api` (ou vínculo ausente/inválido) encerra com `executor_api_mismatch`,
preservando o estado. Restaure a URL original para reconectar à mesma identidade;
uma barra final é equivalente. Use um `--state` separado para outra instalação,
sem apagar ou editar o estado anterior. Essa regra também vale para o conector pessoal.

O tipo salvo também deve corresponder ao modo `computer`. Se aparecer
`executor_kind_mismatch`, o runtime encerra antes de enviar a credencial,
conservando o estado e o convite. Reinicie no modo original; para um pareamento
diferente, use um diretório de estado separado. Tipo ausente/inválido também é
bloqueado, sem reparo automático nem exclusão do estado privado.

`executor_credential_pending` impede iniciar com um temporário de credencial
não reconciliado, mesmo se a identidade anterior for válida. Nenhum computador
é preparado nem convite consumido nesse estado. Preserve os arquivos privados
e siga a [reconciliação de pareamento](recovery.md#falha-ao-salvar-identidade-após-pareamento)
antes de reiniciar.

O WebSocket autenticado de comandos e o canal de visualização também recusam
redirecionamentos, inclusive na mesma origem. Uma resposta 3xx preserva a credencial;
o canal de comandos tenta novamente somente a API configurada, sem novo pareamento.
Corrija o endpoint/proxy para servir a conexão diretamente.

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
- Recursos: o serviço `computer` tem teto de CPU, memória e processos para todos os computadores (`BEES_COMPUTER_CPUS`, padrão 2; `BEES_COMPUTER_MEMORY`, padrão `4g`; `BEES_COMPUTER_PIDS`, padrão 16384). Cada conta recebe ainda, via `prlimit`, limite próprio de processos (`BEES_COMPUTER_MAX_PROCESSES`, padrão 8192; no Linux cada thread conta, e o Chromium sozinho abre centenas), tamanho máximo de arquivo (`BEES_COMPUTER_MAX_FILE_MB`, padrão 4096) e core desligado; valores inválidos voltam ao padrão. Não há cota de disco por conta: o volume `bees-computers` é compartilhado.
- É um container (kernel compartilhado). A VM continua sendo a fronteira mais forte prevista em [ambientes](environments.md).

## Operação

- Volumes: `bees-computers` (homes), `bees-computer-state` (credencial e registro), `bees-computer-link` (convite).
- Revogar o runtime (API `POST /api/v1/executors/{id}/revoke`) encerra a conexão, expira comandos pendentes e marca os computadores como falhos.
- Comandos sem envio até o prazo expiram sem efeito; enviados sem recibo ficam com resultado desconhecido e exigem reconhecimento humano.

## Recuperação do perfil no Linux

Antes de reabrir o Chromium, o executor libera travas antigas do perfil solicitado
e encerra processos órfãos da mesma conta que usam exatamente aquele diretório.
A comparação usa o argumento completo `--user-data-dir=...`, respeitando as
fronteiras de argumentos de `/proc`; perfis com nomes semelhantes permanecem
independentes. Os arquivos do perfil são preservados.

`npm --prefix apps/web run e2e:browser-recovery` verifica esse recorte com dois
perfis Chromium reais e descartáveis, incluindo espaço e acento no caminho:
fecha o perfil exato, conserva o outro navegador e reabre o perfil recuperado.
Exige a `.venv`, as dependências web e o Chromium do Playwright já preparados.
O job Chromium da CI executa o roteiro e publica `browser-recovery.json` junto
aos demais resultados. A prova usa Linux e não equivale ao aceite Windows,
de VM ou de contas/sessões pessoais do mantenedor.
