# Artefatos

BEES-018.1/018.2 reutilizam o `ArtifactStore` em `packages/core/src/bees_core/artifacts.py`: blobs verificados, versões imutáveis e download autenticado. A conversa permite armazenar arquivos, consultar texto inerte e criar uma revisão humana. Incluir um arquivo no próximo pedido ao modelo é uma escolha separada do upload. O primeiro recorte de BEES-018.3 gera relatório Markdown e tabela CSV a partir da resposta de uma tarefa textual (ver [Entregáveis de tarefas](#entregáveis-de-tarefas--checkpoint-bees-0183)); produção e abertura no computador próprio continuam pendentes.

## Contrato

- **Formatos aceitos:** `text/plain`, `text/markdown`, `text/csv` e `application/json`. Todos em UTF-8 estrito, sem NUL. JSON sem chaves duplicadas nem `NaN`; CSV analisado em modo estrito. O limite padrão é 25 MiB por arquivo. Outros tipos são recusados antes de gravar qualquer coisa.
- **Nome:** só para exibição, de 1 a 200 caracteres, sem separadores de caminho, controles, caracteres de formatação invisíveis ou espaços nas pontas. O caminho físico deriva apenas do UUID do artefato (`artifacts/blobs/<uuid>`). O banco guarda a chave `blob/v1/<uuid>`, e uma chave diferente da esperada é tratada como violação de integridade, nunca como caminho.
- **Estados:**
  - `draft`: publicação ainda não confirmada; nunca é servido. O hash, o tamanho e o tipo declarados ficam imutáveis desde o rascunho.
  - `ready`: blob publicado e relido com o mesmo SHA-256 e tamanho.
  - `failed`: terminal, com `metadata.error_code`, evidência preservada. Ocorre só em dois casos: conteúdo estável divergente no destino (`artifact_integrity_failed`) ou abandono explícito (`artifact_abandoned`).
  - `deleted`: reservado; não há exclusão neste recorte.
- **Versões:** cada versão é um registro imutável com `series_id`, `version` e `previous_id`. Uma revisão cria nova versão a partir de uma versão `ready` e preserva a anterior. O banco exige coerência dessa linhagem: v1 inicia a própria série; a anterior precisa estar `ready`, ser da mesma tarefa e da versão imediatamente anterior.
  - `(series_id, version)` é único entre registros não `failed`. Entre duas versões concorrentes a partir da mesma base, uma é criada e a outra recebe `artifact_version_conflict`.
  - Um rascunho interrompido ocupa a posição e outro conteúdo recebe `artifact_version_pending`. Publicações internas podem derivar o id de anterior, conteúdo, tipo, nome e execução; uploads humanos usam o UUID4 confirmado. Repetir explicitamente a mesma intenção readota o rascunho e conclui a publicação.
  - `abandon()` encerra um rascunho como `failed`, sem tocar em arquivos, e libera a posição.
  - Registros anteriores à migração 0009 não têm série; a primeira nova versão inicia a série pelo id do legado.
- **Vínculo:** todo artefato pertence à abelha e a uma conversa ou tarefa canônica. Tarefa e execução são opcionais para anexos da conversa principal; execução exige tarefa. Quando conversa e tarefa estão presentes, a conversa precisa ser a dessa tarefa. Versões preservam a abelha, conversa, tarefa e série. IDs de outro contexto resultam em "não encontrado", sem bytes.
- **Origem:** `user_upload` e `user_revision` identificam intenção humana; `system_generated`, publicação pelo código confiável do Bees; `legacy_unknown`, origem não demonstrada dos registros migrados. A origem é imutável e definida pelo servidor. Ela não comprova geração por modelo, VM nem isolamento de execução.

## Publicação

Banco e arquivos não fecham numa transação única ([ADR 0001](adr/0001-foundation.md)). A sequência é:

1. Validar formato, nome e tamanho e calcular SHA-256.
2. Criar a linha `draft` já com hash, tamanho e chave. Isso vincula o id ao conteúdo antes de qualquer arquivo.
3. Gravar o staging próprio (`staging/<uuid>.<aleatório>.part`, criação exclusiva, `0600` no POSIX, `fsync`). Publicar sem sobrescrever e sincronizar diretórios no POSIX:
   - **Windows:** `rename`, com até cinco tentativas curtas quando antivírus ou indexador seguram o arquivo recém-escrito.
   - **POSIX:** `link` seguido da remoção imediata do staging. Um link de staging do mesmo artefato deixado por publicador interrompido nessa janela aponta para o mesmo conteúdo e é liberado na repetição. Link a mais nunca torna o artefato terminal.
4. Reler o blob publicado: sem link/reparse, um único hard link, mesmo arquivo aberto, tamanho e hash esperados.
5. Atualizar `draft → ready` com controle de revisão, exigindo que conteúdo e linhagem da linha sejam exatamente os verificados.

**Repetição com o mesmo id e conteúdo:**
- termina uma publicação interrompida;
- devolve o registro já pronto, sem novo blob;
- com conteúdo ou vínculos diferentes, é recusada (`artifact_conflict`).

**Se o destino já existir com outro conteúdo:** o rascunho vira `failed` e o arquivo existente é preservado.

**Falha do ambiente** (disco cheio, permissão, arquivo em uso): o staging parcial próprio é removido e o rascunho continua `draft`. Retorna `artifact_storage_unavailable`, que é retentável com o mesmo id.

**Processo interrompido:** a linha fica `draft`, para reconciliação e repetição explícita ou abandono.

`reconcile()` é somente leitura. Relata:
- rascunhos pendentes (sem blob), interrompidos (blob íntegro) e em conflito (blob divergente);
- prontos sem integridade e falhas com blob;
- blobs órfãos e arquivos de staging.

Lê os diretórios antes do banco, numa única transação de leitura, para não marcar como órfão um blob publicado durante a varredura. Arquivos de staging listados podem pertencer a publicadores ativos. Não apaga, não move, não reaproveita staging alheio e não conclui publicações.

## API

Rotas autenticadas por sessão. Mutações exigem a origem e o token CSRF da sessão atual.

- `GET /api/v1/agents/{agent_id}/tasks/{task_id}/artifacts?offset=0&limit=100`
  - Pagina metadados seguros: id, abelha, conversa, tarefa, execução, origem, nome, tipo, tamanho, hash, versão, série, anterior, estado, revisão e datas.
  - Nunca expõe chave de armazenamento, caminho ou `metadata`.
- `GET /api/v1/agents/{agent_id}/artifacts/{artifact_id}/download`
  - Relê e confere o blob antes de enviar qualquer byte.
  - Responde sempre como anexo (`Content-Disposition: attachment` com `filename*` UTF-8), `nosniff`, `Cache-Control: no-store` e uma `Content-Security-Policy: sandbox` adicional à política global.
  - Rascunho, blob ausente ou alterado retornam `409` com código (`artifact_not_ready`, `artifact_blob_missing`, `artifact_integrity_failed`), sem caminho nem conteúdo. Armazenamento indisponível retorna `503` (`artifact_storage_unavailable`).

- `GET /api/v1/agents/{agent_id}/conversations/{conversation_id}/artifacts?offset=0&limit=100`
  - Lista os arquivos desse contexto com o mesmo contrato fechado de paginação.
- `GET /api/v1/agents/{agent_id}/conversations/{conversation_id}/artifacts/{artifact_id}`
  - Consulta a versão exata, inclusive o estado de uma publicação com resposta perdida.
- `GET .../artifacts/{artifact_id}/preview?limit=65536`
  - Confere o blob antes de devolver `artifact`, `text`, `truncated` e `total_bytes`.
    A prévia é UTF-8 inerte; o limite é de bytes. Prévia truncada não pode alimentar
    uma revisão no editor nem ser enviada silenciosamente ao modelo.
    Legado de formato não suportado ou sem UTF-8 válido recebe
    `422 artifact_preview_unavailable`, sem omitir bytes inválidos silenciosamente.
- `PUT .../artifacts/{client_request_id}/content`
  - UUID4 humano estável identifica a intenção e o novo artefato. Corpo binário
    `application/octet-stream`, `Content-Length` exato e máximo de 25 MiB.
    `X-Bees-Artifact` contém JSON fechado em base64url; o header codificado tem limite de 2 KiB:
    `name`, `media_type`, `size_bytes`, `sha256`; revisão inclui juntos
    `previous_id` e `expected_revision`.
  - A exceção binária vale apenas nessa rota e método. Sessão, CSRF, contexto e
    metadados são conferidos antes de consumir o corpo volumoso. Demais mutações
    conservam JSON e limite de 64 KiB. Corpo/headers incoerentes são recusados.
    Há dois uploads simultâneos por processo e prazo de dez segundos para ler o corpo.
    Saturação retorna `429 artifact_upload_busy`; desconexão e excesso são recusados
    sem reservar um rascunho. A sessão é conferida novamente antes de publicar.
  - A intenção humana e o rascunho são registrados juntos. Replay fecha conteúdo,
    contexto e operação pelo hash do pedido; intenção divergente é conflito.
    Uma revisão nova confere CAS antes de reservar a posição da série. Replay
    de intenção existente consulta seu estado, sem reaplicar uma edição antiga.

## Arquivo no próximo pedido ao modelo

Upload armazena na instalação Bees aberta e não chama um provedor. O usuário escolhe
separadamente versões prontas para o próximo envio, vendo o destino/modelo atual.
O contrato de chat recebe até quatro `attachments` com `artifact_id`, `revision` e
`sha256`, e `attachment_consent.expected_agent_revision`. Com anexos, o UUID4 do
turno é obrigatório. Mudança da configuração invalida a confirmação antes da rede.

O backend confere contexto, estado, versão, hash e bytes de cada arquivo. O limite
agregado é 64 KiB de conteúdo completo: excesso é recusado, sem cortar o arquivo.
O conteúdo entra após o recorte do histórico como dado não confiável, sem conceder
permissões ou editar políticas. A inclusão vale para esse turno; armazenar ou
consultar arquivos não os inclui automaticamente em pedidos futuros.

Referências seguras e a confirmação humana ficam no registro append-only da
mensagem. Texto do pedido, versões e confirmação fazem parte da identidade do
turno: replay com seleção diferente é recusado. Resultado desconhecido conserva
a entrada e exige reconciliação; não há repetição automática de geração.

## Interface — checkpoint BEES-018.2

A consulta de uma tarefa mostra cartões dos artefatos já registrados, logo após sua resposta. Cada cartão corresponde a uma versão exata, inclusive versões anteriores, com nome, formato, tamanho, estado e data. A seção recolhível **Vínculos e integridade** mostra SHA-256, tarefa, execução quando presente, id, série e anterior quando registrados. Não infere origem nem ambiente a partir desses campos.

Arquivos de saída vinculados à conversa canônica da tarefa também oferecem prévia
e revisão humana. A nova versão conserva a tarefa/conversa e tem origem
`user_revision`, sem atribuir ao run anterior uma edição posterior. A revisão
não muda a resposta textual da tarefa nem cria outra execução.

A lista autenticada começa em carregamento explícito, distingue lista vazia de erro e oferece atualização manual e paginação de 100 registros. Falha ou resposta incoerente conserva os cartões anteriores com aviso de desatualização e bloqueia downloads até uma nova consulta válida. Trocar de tarefa/abelha ou fechar a consulta cancela transportes e descarta respostas tardias. Dados de vínculos estrangeiros, campos extras, duplicatas, paginação incoerente ou metadados regressivos são recusados antes de entrar no estado da interface.

**Baixar versão** solicita somente o id daquele cartão, com sessão de mesma origem, sem cache, redirecionamento nem repetição automática. A interface confere anexo, nome, formato, tamanho e SHA-256 antes de entregar um blob inerte ao navegador. A leitura limita bytes ao tamanho declarado; arquivos acima de 25 MiB ficam fora do download pela interface neste checkpoint. Falha, expiração de sessão, arquivo ausente/alterado ou cancelamento não exibem sucesso. A confirmação diz que o download foi enviado ao navegador; não afirma que o usuário salvou o arquivo.

Na conversa, a seleção nativa informa tipo, tamanho e destino antes da confirmação
de armazenamento. A prévia mostra texto escapado, sem interpretar HTML, Markdown,
macros ou fórmulas de CSV. A revisão usa um novo arquivo ou o editor quando a prévia
está completa. Conflito preserva o candidato e exige atualização e nova decisão;
não há rebase automático sobre uma versão concorrente.

Interromper o transporte não desfaz uma publicação. Resposta perdida conserva UUID
e snapshot para consultar a versão exata; não anuncia cancelamento ou sucesso
antes do estado canônico. Seleção e bytes locais ficam em RAM. Trocar contexto
aborta transportes e descarta respostas tardias, conservando registros do servidor.

Rascunhos, falhas e registros removidos não oferecem download. Textos estão
disponíveis em português, inglês e espanhol; controles e detalhes usam elementos
nativos de teclado e cartões adaptam nome/hash longos a telas estreitas.

## Entregáveis de tarefas — checkpoint BEES-018.3

Ao delegar uma tarefa, o usuário pode marcar **Relatório editável (.md)** e/ou
**Tabela (.csv)**. Sem marcação, o pedido, o hash de idempotência e o prompt são
os mesmos de antes. A escolha é campo fechado `deliverables` de `TaskInput`
(`report`, `table`, sem repetição), guardado nos metadados da tarefa e parte da
identidade da submissão: repetir o UUID com outra escolha é conflito. O modelo
não escolhe nem amplia formatos.

**Contrato com o modelo.** O worker acrescenta ao pedido seções delimitadas por
linhas exatas `<<<BEES:REPORT>>>`, `<<<BEES:TABLE>>>`, `<<<BEES:MISSING>>>` e
`<<<BEES:END>>>`. A resposta continua salva inteira como mensagem da tarefa.
O relatório deve citar entradas/fontes do pedido; a seção de dados faltantes
lista o que não pôde ser preenchido, sem inventar valores.

**Validação independente** (`packages/core/src/bees_core/deliverables.py`, pura,
sem rede ou arquivos):

- Seções ausentes, vazias, repetidas, sem fim ou com fim inesperado recebem
  código explícito. Um bloco cercado inteiro (```` ```csv ````) é desembrulhado;
  cercas internas do relatório são preservadas.
- Relatório: UTF-8 estrito, sem NUL, até 25 MiB.
- Tabela: leitura `csv` estrita; cabeçalho com nomes únicos e não vazios (até 200
  colunas), todas as linhas com o mesmo número de colunas e até 100.000 linhas de
  dados. O arquivo publicado é a reescrita RFC 4180 (CRLF, aspas quando
  necessário) dos **mesmos valores** lidos; nada é corrigido em silêncio.
  Células que começam como fórmula (`=`, `+`, `-`, `@`) são preservadas e
  contadas; a interface avisa antes do uso em planilha.

**Publicação.** A tarefa não conclui ao receber a resposta. Com a chamada
confirmada, a execução fica em `publishing` sob a mesma lease e o worker publica
cada formato válido pelo `ArtifactStore` com origem `system_generated`, vínculo
de abelha/conversa/tarefa/execução e id `uuid5(chamada, formato)`. Metadados
privados registram chamada, mensagem de origem, parcialidade e dados faltantes.
O resumo público da execução traz estado, código, id, SHA-256, tamanho e versão
de cada arquivo, além de linhas/colunas da tabela.

| Situação | Resultado |
| --- | --- |
| Todos publicados | `completed`; resposta pronta. Dados faltantes declarados marcam a entrega como **parcial**. |
| Algum ausente/inválido | Os válidos são publicados; tarefa `paused` com `deliverable_invalid` e atenção. Retomar ou redirecionar gera nova chamada, com aviso de custo. |
| Integridade recusada | `deliverable_publication_failed`; nenhum arquivo é sobrescrito. |
| Armazenamento indisponível | `artifact_storage_unavailable`; publicação continua **pendente**. Retomar publica a mesma resposta sem nova chamada, mesmo com o limite de chamadas atingido. |
| Processo interrompido durante a gravação | A recuperação de leases pausa com `artifact_publication_interrupted`; não há repetição automática. Retomar repete a mesma intenção com os mesmos ids, readotando rascunhos sem cartão duplicado. |
| Pausa, cancelamento, redirecionamento ou parada global | Pausa conserva a publicação pendente; cancelamento a encerra; redirecionamento a marca `superseded` e a nova orientação gera outra resposta; parada global devolve à fila sem gravar. |

A interface mostra **Arquivos da entrega** no detalhe da tarefa, com estado de
cada formato, motivo de recusa, forma da tabela, aviso de fórmulas e lista de
dados faltantes (texto escapado). Downloads continuam nos cartões verificados.

**Limites deste recorte.** Os arquivos são gerados a partir da resposta do modelo,
no armazenamento do Bees. Não há produção, abertura ou edição no guest/app do
computador próprio (BEES-012), nem DOCX/ODT/XLSX. O critério de leitor independente
foi comprovado por leitura direta do blob e `csv` do Python nos testes; a abertura
num editor/planilha real e o aceite com modelo real continuam pendentes.

Testes da interface (`apps/web/src/features/artifacts`) cobrem contratos negativos, sessão expirada, download íntegro e adulterado, tamanho/nome/formato divergentes, estados dos cartões, três idiomas, versões separadas, paginação, falha de atualização e cancelamento/respostas tardias.

## Operação e limites

Os blobs ficam em `BEES_DATA_DIR/artifacts`, no mesmo volume `bees-data` da distribuição Docker. Backup da base SQLite não inclui blobs nem o cofre. Copie a pasta de dados inteira com API e worker parados, como descrito em [persistência](persistence.md) e [containers](containers.md).

Não há exclusão, retenção, cota por abelha ou exportação de artefatos. Essas políticas pertencem a BEES-023/024.

O schema13 migra vínculos legados a partir das tarefas e preserva UUIDs, revisões,
bytes e linhagem. A migração reconstrói a tabela numa transação sem desligar FKs;
`artifact_commands` guarda intenções humanas append-only. Execute a atualização
com serviço quiescido e backup. Este checkpoint valida somente bases próprias de
QA; não atualiza a instalação pessoal automaticamente.

No Windows, a privacidade depende das ACLs da pasta de dados; a biblioteca não altera ACLs. A verificação de links cobre os ancestrais da raiz e os arquivos publicados, mas não é isolamento contra outro processo da mesma conta ou de administrador.

A raiz recusa qualquer ancestral symlink ou junction. Uma pasta de dados atrás de link (por exemplo, redirecionada por sincronização de nuvem) mantém o banco funcionando, mas deixa os artefatos indisponíveis (`artifact_storage_unavailable`). Use um caminho local direto. Gravações pelo repositório ou SQL direto podem criar `ready` sem blob; somente o `ArtifactStore` dá essa prova, e o download sempre relê e confere.

## Validação

BEES-018.3 acrescenta `packages/core/tests/test_task_deliverables.py`: extração
com pt/en/es, aspas, cercas, dados vazios, tabela de 5.000 linhas, limites,
fórmulas e erros de estrutura; publicação pelo worker com leitura direta do blob;
formato inválido, seção ausente, dados faltantes, armazenamento indisponível,
crash entre gravações com retomada e readoção dos mesmos ids, pausa, redirecionamento
e parada global. `apps/api/tests/test_tasks.py` cobre contrato, projeção fechada e
download; `apps/web/src/features/tasks/deliverables.test.tsx`, escolha no formulário,
contrato recusado e apresentação em três idiomas.

Testes com SQLite e arquivos reais em diretórios próprios (`packages/core/tests/test_artifacts.py` e `apps/api/tests/test_artifacts_api.py`) cobrem:
- publicação e leitura independente dos bytes no disco após reinício;
- interrupção antes e depois do blob, com repetição idêntica e conflitante;
- link de staging remanescente da janela POSIX, sem estado terminal;
- retentativa de rename no Windows e falha de disco retentável;
- versão interrompida readotada, pendente e abandonada sem travar a série;
- blob estranho no destino, disco cheio, blob alterado, ausente, com hard link ou com chave reescrita;
- raiz com symlink (pulado no Windows sem privilégio);
- acesso por outra abelha;
- versões concorrentes;
- gatilhos SQL de imutabilidade (inclusive de rascunho), coerência de linhagem e append-only;
- CSV com campo acima de 128 KiB;
- validação de formatos e nomes;
- reconciliação sem mutação;
- upgrade 8→9 preservando registro legado;
- headers e respostas da API.

BEES-018.2 acrescenta `test_artifact_uploads.py`, `test_artifact_upload.py` e
`test_artifact_chat.py`: migração 12→13 com FKs ativas e rollback, vínculo humano
sem tarefa, intenção append-only/CAS/UUID, concorrência e publicação interrompida;
autenticação antes do corpo, limites e HTTP loopback real; arquivo completo fora
da janela do histórico, mudança de destino, replay/unknown e ausência de reenvio
no turno seguinte. Testes interativos de React cobrem confirmação, erro,
conflito, reconciliação, troca de contexto e conteúdo escapado. O ambiente DOM é
dependência somente de desenvolvimento; não representa uma fronteira de isolamento.

A GUI de QA usa identidade/dados descartáveis, API e arquivos reais e provedor HTTP
controlado local. Não demonstra geração paga ou qualidade de um modelo real.
