# Histórico de entregas — BEES-025.2

A aba **Entregas** da abelha consulta resultados de tarefas e versões de arquivos
preservados nesta instalação. A busca não depende da janela recente de mensagens
nem de intervalo de dias. Resultados incluem mensagens de assistente explicitamente
vinculadas ao `result_message_id` de uma execução concluída; arquivos incluem todas
as versões e estados, inclusive publicação incompleta e remoção lógica.

O formulário aceita nome/título literal, ID de entrega, tarefa, conversa ou execução,
além dos filtros exatos de tarefa e conversa. A busca textual usa `casefold` Unicode;
`%` e `_` são caracteres literais, não curingas. Ela pesquisa títulos de tarefas e
nomes de arquivos, sem indexar semanticamente conteúdo ou examinar todas as
mensagens de chat.

## Paginação e vínculos

`GET /api/v1/agents/{agent_id}/deliveries` exige sessão. Parâmetros: `q` (até 200
caracteres), `conversation_id`, `task_id`, `limit` (1–100; interface: 50) e `cursor`.
O retorno informa `scope`, `items`, `limit`, `until` e `next_cursor`; `null` indica
fim do histórico. A interface informa o limite por página e o total já carregado.

A ordenação é decrescente por data e identidade (`kind:id`). O cursor leva a última
posição e a data superior da primeira página, vinculado ao conjunto de filtros e à
abelha. Assim uma entrega recém-publicada não desloca páginas existentes; não há
offset máximo nem corte silencioso de resultados antigos. Alterar filtros exige
nova consulta. Essa data é uma fronteira de inclusão por data de criação, **não**
um snapshot imutável do banco: o estado atual de um arquivo pode mudar para
`deleted` enquanto o usuário navega. As consultas usam transações SQLite de leitura,
parâmetros SQL e os índices existentes de escopo de abelha, tarefa, mensagem e
artefato, sem migração adicional.

`GET /api/v1/agents/{agent_id}/deliveries/results/{message_id}?run_id={run_id}`
retorna texto completo por vínculo exato entre mensagem, conversa, tarefa e
execução concluída. A versão do resultado é a revisão da mensagem preservada, não
a revisão atual da tarefa. O servidor não usa a última mensagem visível como
substituta. Um ID estrangeiro ou vínculo de execução incorreto retorna 404.

`GET /api/v1/agents/{agent_id}/deliveries/artifacts/{artifact_id}` retorna a versão
exata e seu estado atual, sem caminho de arquivo, chave de armazenamento ou
metadados internos. A interface reaproveita a prévia de texto e o download com
verificação de integridade existentes. Uma versão removida continua identificável,
sem botão de download; remoção entre leitura e download é erro explícito. Um
arquivo em preparação/falha e resultados com dados faltantes são sinalizados.

Abrir consulta, selecionar resultado, preparar referência, abrir biblioteca de skills
ou consultar decisões da tarefa não chama modelo, não cria tarefa, não altera
mensagem append-only, não publica revisão e não muda revisões canônicas. Conteúdo
é exibido como texto simples, com escape do React. O usuário conserva o texto
local do formulário até enviar uma busca; não existe editor de conteúdo neste
painel que possa sobrescrever um rascunho.

## Referências e atalhos

O campo de referência é somente leitura e pode ser selecionado/copiado pelo teclado:

```text
bees:delivery/result/{message_id}?agent={agent_id}&task={task_id}&run={run_id}&conversation={conversation_id}&version=1
```

Arquivos usam `bees:delivery/artifact/{artifact_id}` e sua versão preservada. A
referência é um identificador textual para a pessoa incluir em uma resposta,
**não** um comando nem um deep link que inicia execução. Copiar não envia mensagem;
falha da API de clipboard mostra orientação e seleciona o campo para cópia manual.
Os vínculos não concedem recursos, permissões ou compartilhamento entre agentes.

Os botões de atalho abrem a biblioteca de skills existente e a tarefa original,
incluindo as decisões já suportadas nela. São controles nativos acessíveis por
teclado. Não há conversão automática de entrega em skill. O atalho **Rotinas**
explica explicitamente que criar uma rotina **a partir desta entrega** não está
disponível e que nenhuma rotina foi criada; isso não afirma ausência da API de
rotinas ou de outras telas em desenvolvimento. Reações, colaboração avançada,
busca semântica obrigatória e compartilhamento de contexto entre agentes estão
fora do recorte mínimo desta história.

## Validação

- Testes core em SQLite real percorrem 302 entregas de mais de um ano, com empates
  de data, sem omissões/duplicações; também cobrem Unicode, busca literal, IDs,
  consulta vazia, escopo estrangeiro, cursor inválido/vinculado, novas entregas
  durante paginação, resultados parciais, remoção e vínculos de conversa incorretos.
- Snapshots de tarefas, execuções, mensagens, artefatos e eventos antes/depois
  comprovam que as consultas e a referência exata não mudam estado canônico.
- Testes HTTP com sessão real cobrem autenticação obrigatória, 404 entre abelhas,
  paginação/cursor, metadados públicos, erro de consulta e arquivo removido.
- Testes web cobrem contrato de resposta, versão substituída, cursor/snapshot,
  cancelamento mesmo com transporte que ignora o sinal, respostas tardias, troca
  de consulta/abelha, foco de detalhe e retorno, foco após última página, atalho
  honesto, referência por tarefa/execução/versão, erro de detalhe, clipboard
  indisponível e resultado parcial/arquivo removido. pt-BR/en/es usam os mesmos
  controles nativos e têm teste de rótulos e navegação.

A folha responsiva dispõe formulário e vínculos em uma coluna em telas estreitas,
permite quebra de UUIDs e mantém conteúdo longo rolável. Os testes DOM verificam
controles e foco; não substituem revisão visual em navegadores e dispositivos
reais. Nenhum modelo pago ou ação externa é necessário para este aceite de leitura.

## Revisão com navegador real em 17/10/2026

Chromium e a API em processo separado usaram uma instalação temporária própria,
sem modelo conectado: pt-BR, inglês e espanhol em 390 e 1280 px. Foram conferidos
teclado, foco ao abrir o resultado, busca, vínculos e o download de um arquivo de
fixture com bytes conhecidos. As 30 verificações de overflow do fluxo combinado
entregas/rotinas passaram, sem erro JavaScript ou requisição externa. As 24 capturas
ficaram no diretório privado de revisão, sem conteúdo de usuário. O banco manteve
somente tarefa/execução da fixture, com zero chamadas de modelo e zero ações.
Isso demonstra o painel com API e navegador reais; não é uma pesquisa ou geração
por modelo nem uma prova dos ambientes físicos.
