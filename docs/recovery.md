# Recuperação operacional

Este roteiro de BEES-024.3 (#84) reúne os caminhos já implementados de tarefas,
aprovações, rotinas e executor local de texto. Use o estado confirmado pelo serviço
para decidir o próximo passo. Uma frase de sucesso do modelo, um processo ausente
ou uma tela sem resposta não comprovam o resultado de um efeito.

## Identificar o que pode ter ocorrido

Abra **Atenção**, **Atividade** e os detalhes da tarefa da abelha correta. Confira
horário/fuso, execução e histórico de ferramentas. O filtro de conversa usa a
conversa de execução da tarefa. Erro de consulta exige atualizar; ausência de
cartão numa página não prova ausência de trabalho ou de resultado desconhecido.

| Registro canônico | Interpretação e próximo passo |
| --- | --- |
| Preparado, aguardando aprovação ou pronto | O registro ainda não confirma despacho. Confira a intenção atual e seus vínculos antes de qualquer decisão. Uma autorização `tool.intent` confirmada significa autorização utilizada; o efeito pertence à ação concreta. |
| `dispatch_started` | O início foi autorizado. A operação pode continuar fora do Bees; parar ou encerrar um processo local não comprova que terminou. Preserve o journal e aguarde evidência. |
| `confirmed` | O resultado dessa ação foi persistido. Consulte-o sem redespatch. Confirmação de modelo não avalia qualidade; confirmação de uma ferramenta não conclui automaticamente o objetivo inteiro. |
| `failed_no_effect` | O contrato registrou falha sem efeito. Não deduza essa classificação de timeout, HTTP genérico ou ausência de saída. A próxima tentativa continua exigindo controle e autorização atuais. |
| `outcome_unknown` | Pode ter ocorrido efeito ou consumo. Não repetir automaticamente. Preserve a ação/chamada e a evidência privada; eventual decisão humana não transforma o resultado em confirmação. |
| Tarefa pausada, cancelada ou concluída | É o estado da tarefa. Examine também as chamadas e ações: cancelar não desfaz efeito e um resultado parcial não comprova entrega final. |

Os detalhes exibem um recorte limitado do histórico. Os contadores e bloqueios de
unknown consultam o conjunto relevante, inclusive registros fora desse recorte.
Não use o tamanho da lista como autorização para continuar.

O histórico de ferramentas apresenta uma explicação por estado em português,
inglês e espanhol. Uma intenção de autorização utilizada aponta para a ação
concreta; somente essa ação registra o resultado do efeito. O aviso de unknown
continua explicando a incerteza depois do reconhecimento humano. Essas explicações
são consultas: não acrescentam botões de execução, concessão ou reconciliação.

## Diagnóstico canônico por tarefa

Nos detalhes, **Diagnóstico da tarefa → Consultar diagnóstico** consulta o inventário completo
por estado de ações e chamadas de modelo, em todas as execuções. O endpoint humano
`GET /api/v1/agents/{agent_id}/tasks/{task_id}/recovery` exige sessão e mantém
isolamento entre abelhas. A resposta fechada contém IDs da tarefa/abelha, revisão e
estado da tarefa, instante da consulta, total de execuções e contagens por estado.
Não inclui nomes de ferramentas, parâmetros, respostas, erros brutos, checkpoints,
metadados, caminhos, owners, referências de credencial ou bindings de executor.

O inventário e as páginas exigem a tabela canônica `tasks` antes de consultar
a identidade/revisão da tarefa. Ausência ou substituição por view retorna conflito
sanitizado (HTTP 409 `state_conflict`), inclusive para IDs ou cursores inexistentes,
pois o cadastro indisponível não permite comprovar o isolamento. Nenhuma view é
lida e nenhum dado é reparado. Com a tabela íntegra, tarefa/abelha incompatíveis
continuam retornando 404. A verificação não detecta substituição por outra tabela
válida nem adulteração de linhas.

A consulta usa uma única transação SQLite `query_only`. Os agregados não dependem
dos limites de 100 ações ou 1.000 execuções do detalhe. Conta também registros de
autorização: um `confirmed` não equivale à contagem de efeitos físicos concluídos.
Unknown reconhecido continua em unknown, separado de unknown sem reconhecimento.
Nenhum registro fora da página é esquecido por isso. A interface informa o instante
e a revisão consultados; uma revisão diferente pede atualizar os detalhes antes de
mostrar o diagnóstico. Atualizar descarta a projeção anterior enquanto consulta;
erro não vira histórico vazio. Trocar tarefa/revisão ou fechar aborta a consulta e
ignora respostas tardias. O painel não consulta automaticamente nem acrescenta
comandos de retomada/reconciliação.

O escopo é `all_canonical_task_history` e a evidência do executor é explicitamente
`not_inspected`. Zero registros não comprova ausência de efeitos fora do core.
A leitura não abre/recupera journals privados, toma lease, revalida autoridade,
libera quarentena, concede ferramenta ou permite redespatch. Inspeção confiável do
executor e decisões com evidência completa continuam nos aceites de #82/#83.

### Localizar registros fora do histórico resumido

**Registros de ações e autorizações → Consultar primeira página** lista 50 registros
por vez, com próxima/anterior e retorno explícito à primeira página. Abre os IDs de
ação/execução, revisão e datas sem consultar parâmetros, resultados, metadados ou
bindings. Tool names são texto inerte; autorizações usadas e unknown reconhecido
conservam a explicação de seu estado. Não são acrescentados botões de decisão.

Os códigos de comando do executor passam por uma lista pública fechada. Texto
livre, inclusive texto curto ou com prefixo `late_`, é omitido, sem truncar nem
alterar o registro privado. Somente `late_confirmed`, `late_failed` e
`late_unknown` em comando `unknown` aparecem como resposta tardia. São marcadores
da fila do servidor, não inspeção autenticada do recurso ou confirmação do efeito;
um código informado pelo executor não é, sozinho, prova de resultado. A projeção
não lê argumentos ou resultados privados nem reconcilia o estado.

`GET /api/v1/agents/{agent_id}/tasks/{task_id}/recovery/actions` aceita `limit` entre
1 e 100 (padrão 50) e `after` com o UUID do registro anterior. A referência do cursor
precisa existir na mesma tarefa/abelha; estrangeira ou inexistente retorna 404,
sem página vazia fictícia. A ordenação é por criação/ID. Cada página usa uma
transação query_only própria, com instante/revisão e escopo
`all_canonical_task_actions`, sempre `executor_evidence: not_inspected`.

As tabelas canônicas de comandos, executores e computadores precisam existir,
inclusive ao consultar uma página vazia ou ações sem comando/computador associado.
Ausência ou substituição de qualquer uma por view retorna HTTP 409 `state_conflict`,
antes de preparar a projeção, sem apresentar vínculos como íntegros, ler a view ou
recriar tabelas. Preserve o estado privado para investigar e restaurar explicitamente;
a consulta não repara dados.
Essa verificação não detecta exclusão de linhas individuais nem substituição por
outra tabela válida.

Antes de publicar o estado da fila, a página confere se o comando pertence à
mesma abelha e ferramenta da ação. Quando há computador, seu cadastro precisa
conservar a mesma abelha e executor do comando. Divergência recusa a página inteira
com HTTP 409 `state_conflict`, sem devolver resultados parciais ou reparar dados.
Preserve o estado privado e investigue os vínculos antes de repetir a consulta;
atualizar a tela não corrige a inconsistência. Cada página verifica somente seus
registros retornados, sem certificar páginas ainda não consultadas. Comando ausente
continua sem projeção de fila. Computador removido e executor revogado não impedem
consulta de histórico com vínculos válidos. Essa conferência canônica não lê
argumentos, resultados ou bindings privados nem comprova identidade/journal externo.

As páginas não formam uma fotografia única entre pedidos. Mudanças de estado ou
novos registros podem exigir atualizar a primeira página; contagem e página têm
instantes próprios. A interface mostra só a página confirmada, descarta a anterior
durante a consulta, preserva o cursor solicitado para repetir um GET que falhou e
ignora resposta de tarefa/revisão antiga. Erro ou cursor inválido não significam
que o histórico está vazio nem que algum efeito não ocorreu. A seção **Registros de chamadas de modelo** permite a mesma navegação para todas
as chamadas, pelo endpoint humano
`GET /api/v1/agents/{agent_id}/tasks/{task_id}/recovery/model-calls`.
Ela usa os mesmos limites e validação de cursor, com escopo
`all_canonical_task_model_calls`. A projeção fechada contém somente ID da chamada,
ID da execução, revisão do registro, estado, datas de criação/atualização e
reconhecimento de unknown. A consulta SQL não lê prompt, resposta, configuração
do provedor, snapshot, hash, erro bruto ou referência de credencial. Não consulta
o provedor e não gera conteúdo. Confirmação significa resposta persistida, sem
avaliar qualidade; reconhecimento de risco conserva unknown. Cada página usa uma
transação query_only e o mesmo descarte por revisão/fechamento. As listas de ações
e de modelos têm cursores e instantes independentes.

### Consultar todas as execuções

**Registros de execuções** permite primeira/próxima/anterior com 50 registros por
página, inclusive além das 1.000 execuções do detalhe resumido. O endpoint humano
`GET /api/v1/agents/{agent_id}/tasks/{task_id}/recovery/runs` aceita os mesmos
limites (1–100, padrão 50) e cursor `after` limitado à tarefa/abelha. Ordena por
criação/ID e usa snapshot query_only próprio, com revisão da tarefa e instante.
O escopo é `all_canonical_task_runs`; o executor continua `not_inspected`.

A projeção lê somente ID, revisão, estado e datas de criação/alteração/início/fim.
Não lê checkpoint, metadados, configuração, provedor/modelo, erro ou ambiente.
Data ausente aparece como não registrada; estado concluído/cancelado não confirma
todos os efeitos externos. IDs permitem relacionar execuções às ações e chamadas.
Cada seção tem cursor/instante próprios. Erro descarta a página, repetição consulta
o mesmo cursor e troca de tarefa/revisão/fechamento descarta respostas tardias.
Esta consulta não retoma, repete ou reconcilia operações.

As três páginas exigem as tabelas canônicas que consultam, antes de ler o cursor
ou os registros: `runs` e `actions` nas ações, `runs` e `model_calls` nas chamadas,
e `runs` nas execuções. Ausência ou substituição por view (mesmo vazia) produz
`409 state_conflict`, sem ler o substituto ou reparar o estado. A verificação usa
o mesmo snapshot `query_only` da página e ocorre depois de validar a tarefa/abelha.
Um cursor não pode ser validado enquanto seu histórico estiver indisponível;
após restaurar explicitamente o original, cursores inexistentes/estrangeiros
continuam retornando 404. Isso não detecta exclusão de linhas ou substituição por
outra tabela válida, nem certifica o journal externo.

## Conter e preservar

1. Confirme **Parar tudo** quando precisar impedir novos despachos. Consulte
   **Operações já enviadas, encerrando** e os registros da tarefa. A parada é durável,
   mas permite a publicação de resultados já iniciados; não equivale a quiescência.
2. Para manutenção ou cópia fria, identifique e pare todos os escritores próprios:
   API, worker, CLIs e componentes da instalação envolvidos. Confirme a identidade
   dos serviços; não encerre um PID só porque aparece num arquivo antigo. No Compose,
   siga [o procedimento de parada e backup](containers.md#dados-cofre-e-recuperação).
3. Preserve um conjunto coerente e privado: dados SQLite e seus arquivos auxiliares,
   blobs, identidade/cofre correspondentes e journals privados dos recursos.
   Preserve os originais para investigação. Copiar apenas o arquivo principal de
   uma base ativa não produz esse conjunto; veja [persistência](persistence.md).
4. Antes de iniciar serviços, compare a versão do schema com a versão do código.
   O worker não migra. Uma migração exige parada e backup; abrir um journal existente
   não autoriza inicializá-lo, repará-lo ou substituir sua identidade.

Não publique esses arquivos em issue, PR ou log comum. Para acompanhar o incidente,
registre apenas identificadores permitidos de tarefa/ação/correlação, códigos
tipados, revisões, horários, plataforma e conclusão das verificações. Não inclua
prompts, entradas privadas, caminhos locais, tokens, referências de credencial ou
erros brutos do fornecedor. Um recibo do executor pode conter texto privado.

## Consultar e decidir

Reabra o estado preservado pelos caminhos suportados e consulte a tarefa antes de
tomar uma decisão. A consulta não concede ferramenta, retoma tarefa ou reconhece
unknown. Se faltam identidade, chave, journal ou vínculos, mantenha o bloqueio e
preserve a evidência; não crie uma raiz substituta para contorná-lo.

No [executor POSIX de texto](executor-transport.md), `inspect` é uma operação
interna privada que consulta o pedido no recurso exato, sem enviar GO. Um receipt
local após resposta perdida não reconcilia automaticamente o core. Abrir o journal
sob o lock exclusivo pode registrar como unknown um início interrompido; portanto,
essa recuperação interna não deve ser descrita como inspeção forense sem escrita.
Não há endpoint humano geral de reconciliação ou adoção de recurso neste checkpoint.

Para uma decisão disponível na interface:

- **Aprovação:** revise novamente a operação e seu alcance. Uma decisão obsoleta,
  expirada ou com conflito pede atualização. Aprovar geração não aprova ferramenta;
  o efeito revalida política, concessão, controle, limites e dono antes do início.
- **Resposta perdida:** consulte o estado/recibo e use a recuperação própria daquele
  formulário. Quando ele permite repetir, conserve UUID, corpo e revisão da intenção
  original. Não troque o UUID para transformar uma confirmação incerta em nova operação.
  Um conflito não comprova que o pedido anterior falhou.
- **Retomada de tarefa:** leia a consequência e, quando exigido, reconheça explicitamente
  o risco de unknown. Isso permite considerar uma continuação sob os controles atuais;
  não muda o resultado anterior nem manda repetir uma ação desconhecida. Recursos podem
  continuar bloqueados pela sua própria quarentena.
- **Rotina manual:** confira primeiro o recibo do pedido. Reenviar o mesmo UUID recupera
  a mesma tarefa/ocorrência; um UUID novo é outra execução e pode ser recusado por
  sobreposição ou unknown. Salvar a rotina pausada não executa nem concede ferramentas.
- **Parada global:** antes de retomar, pause ou cancele tarefas na fila que não devem
  executar. Retomar libera novos claims; tarefas pausadas/unknown conservam a decisão
  própria. Revogar uma regra bloqueia usos futuros, sem desfazer um efeito anterior.

Leitura e silêncio em **Atenção** não resolvem nenhum desses bloqueios. Unknown
continua visível como atenção necessária mesmo após leitura ou reconhecimento.

## Roteiro: queda do worker ou do executor

Reproduzido pela [matriz de falhas](../apps/executor/tests/test_failure_matrix.py), com processos reais mortos por `SIGKILL`/`SIGSTOP`:

1. **Reinicie primeiro o worker** (ou o Compose inteiro). Até o lease vencer (30 s), a tarefa ainda aparece em execução. Depois disso, a ação em voo vira **resultado desconhecido** e a tarefa fica **pausada**, com atenção. O comando do executor fica `expired` (não saiu) ou `unknown` (`worker_restart`, pode ter saído). Nenhum dos dois é reenviado.
2. **Reconecte o executor.** Ele não reexecuta comandos antigos. Um recibo tardio fica guardado como evidência (`late_confirmed`/`late_failed`) e não muda o estado.
3. **Abra a tarefa → Recuperação.** A coluna do comando mostra o que a fila sabe (`expired`, `unknown`, evidência tardia). Confira o efeito no recurso real, por exemplo se o arquivo existe e quantas linhas tem, antes de decidir.
4. **Decida**:
   - efeito ausente: retome a tarefa, que pode propor a ação de novo como um pedido novo;
   - efeito presente: retome e informe na conversa, ou cancele.
   Nos dois casos, a decisão é sua. O Bees não repete sozinho.
5. Uma ação **confirmada** antes da queda não repete após reiniciar tudo; a tarefa continua de onde parou.

## Acompanhar a continuação

Inicie a API compatível e confirme banco pronto; depois inicie o worker e confira
seu heartbeat. Saúde do serviço não comprova conclusão de tarefa. Consulte a mesma
tarefa, execução, ocorrência e registros de efeito após reload/restart. Confira o
resultado e seus artefatos antes do aceite humano; mantenha o histórico anterior.

Uma [restauração lógica em staging](restoration.md) tem outro propósito: transfere
dados para um destino novo e inativo, sem reconectar autoridade, credenciais ou
rotinas ativas. Não é reparo automático da instalação em execução. Perda da âncora
privada/cofre e reset destrutivo não são caminhos de retomada deste roteiro.

## Evidência e portões restantes

| Caminho demonstrado | Evidência reproduzível e limite |
| --- | --- |
| Receipt persistido, replay e interrupção do filho real | [Testes do transporte](../packages/core/tests/test_executor_transport.py): processo POSIX próprio, fencing, journal e contagem do início; sem VM ou sistema pessoal. |
| Aprovação após restart e efeito confirmado sem redespatch | [Testes do ciclo](../packages/core/tests/test_tool_cycle.py): provedor controlado e ferramenta POSIX real; perda de commit da projeção consulta a confirmação já existente. |
| Rotina manual, rollback e recuperação do mesmo pedido | [Testes manuais](../packages/core/tests/test_routine_manual.py): SQLite, reinício, ocorrência única e unknown bloqueando novo pedido. |
| Leitura após resposta perdida/reinício da API | [Atenção por conversa](attention.md): prova Chromium com API real e comparação canônica; leitura não executa tarefa. |
| Worker e conector pessoal mortos com SIGKILL (processos reais) | [Matriz de falhas](../apps/executor/tests/test_failure_matrix.py), job de CI próprio: efeito aplicado sem recibo fica `unknown` com 1 efeito no disco; executor congelado que recebeu o comando e morreu deixa 0 efeitos e `unknown`; efeito confirmado não repete após reiniciar tudo; nenhum comando novo nasce dos reinícios. Modelo simulado por HTTP, Linux; sem VM, navegador ou máquina pessoal real. |
| Rotina agendada confirmada e reinício dos processos | A mesma matriz executa o scheduler real no worker, sem frontend. Depois do restart conserva uma ocorrência, uma tarefa, um comando confirmado e uma linha no arquivo; duas gerações do modelo controlado, sem geração adicional no restart. |
| Rotina manual com efeito sem recibo e replay | Depois de matar worker/conector com efeito no disco, a ocorrência conserva `outcome_unknown`, a tarefa fica pausada e o comando fica `unknown`. Reapresentar o mesmo UUID manual conserva os IDs, uma ocorrência/tarefa/comando e um efeito; apenas uma geração do modelo controlado. |
| Rotina esperando decisão durante o restart | A mesma decisão reaparece. Antes de aprovar há zero comandos/efeitos e uma geração. Depois da aprovação explícita termina com um comando, uma linha no arquivo e duas gerações no total; nenhuma nova tarefa ou ocorrência. |

Para repetir os seis cenários em uma instalação de teste Linux, execute da raiz:

```sh
BEES_FAILURE_MATRIX=1 BEES_FAILURE_MATRIX_OUT=failure-matrix.json \
  .venv/bin/python -m pytest -q apps/executor/tests/test_failure_matrix.py
```

A API usa uvicorn em thread do processo de teste; worker/scheduler e conector são
subprocessos do produto, mortos e reiniciados com seus arquivos privados preservados.
O modelo é um servidor HTTP controlado; os efeitos são arquivos reais em uma pasta
descartável concedida ao conector. O relatório exporta apenas cenário, estados e
contagens, sem caminhos, tokens, prompts ou dados privados.

Os cenários anteriores de transporte e ciclo foram executados nos checkpoints
das PRs #135, #136, #139, #142 e #146. A matriz de tarefas veio da #176 e foi
ampliada para rotinas na [subtarefa #212](https://github.com/L0tus-Program/Bees/issues/212).
São provas do recorte nativo com provider controlado, não de qualidade de LLM
real, VM, aplicativos ou máquina pessoal. RecoveryService composto, inspeção externa
com identidade/recurso exatos, diagnóstico que componha evidência externa e matriz física de falhas
continuam nos aceites de #82/#83/#84. Um lease vencido ou PID ausente, isoladamente,
nunca substitui evidência suficiente do executor antigo.

## Executores, canais, MCP e webhooks

| Superfície | Estado após queda | O que acontece na retomada |
| --- | --- | --- |
| Comando de executor na fila (`queued`) | Ação `dispatch_started` | O comando expira (`worker_restart`) na mesma transação que declara o efeito desconhecido (lease vencido ou retomada humana) e o envio recusa comando de ação já encerrada (`action_closed`) ou cuja ferramenta/pacote foi desligado depois do início (`tool_grant_revoked`): nunca sai depois. |
| Comando enviado (`sent`) | Pode ter executado | Vira `unknown` (`worker_restart`). Recibo que chegar depois é guardado como evidência (`late_confirmed`/`late_failed`) sem mudar o estado; reconciliação é humana. |
| Journal do executor | `started` sem recibo | O executor marca `unknown` ao reabrir e não repete o comando. |
| Saída de canal | Registrada como `sending` antes do envio | Queda ou timeout depois do envio vira `unknown`: a mensagem não é reenviada (pode faltar, nunca duplicar). Falha de conexão antes do envio libera nova tentativa. |
| `mcp_call` | Resposta perdida após o envio | `mcp_outcome_unknown`, sem repetição automática. |
| Webhook | Reenvio do serviço externo | Com `Idempotency-Key`, o mesmo evento devolve o recibo anterior sem nova execução; sem chave, vale o intervalo mínimo. |
| Computador `removing`/`failed` | — | Remoção humana conclui; criação nova exige Bees em execução. |


### Journal dos computadores no runtime container

Após a primeira abertura nesta versão, cada computador mantém em sua home privada
`.bees/journal.required`, criado exclusivamente e sincronizado antes de permitir a
criação de `.bees/journal.sqlite3`. O marcador registra intenção, inclusive se a
primeira criação for interrompida; não é uma prova de journal completo. No Linux,
a entrada do diretório também é sincronizada antes da criação do SQLite.

Se o journal desaparecer e o marcador permanecer, o modo single e o processo agente
multiuser recusam a abertura com `executor_journal_invalid`, sem recriar histórico.
O agente não abre seu socket e o supervisor não anuncia preparação concluída se o
agente encerrar ou não ficar disponível no prazo. Preserve a home, o marcador e os
arquivos de trabalho. Após quiescer os processos, restaure somente o journal
original de backup confiável, com os mesmos vínculos; abrir de novo preserva os
recibos e conserva comandos interrompidos como unknown. Não remova o marcador
para tentar novamente, nem crie um SQLite vazio.

Um journal legado válido recebe o marcador na primeira abertura com esta versão.
Sem marcador anterior não é possível distinguir primeiro uso de perda de um
journal legado; a proteção não detecta essa perda anterior à atualização, perda
conjunta de journal e marcador, rollback para journal válido, ou exclusão durante
uma conexão já aberta. Não substitui backup consistente nem reconciliação humana.
A criação exclusiva do marcador evita duas primeiras criações simultâneas, mas
não substitui a exclusividade de processos do supervisor/estado privado.

### Journal pessoal após revogação

A CLI pessoal também grava `journal.required` no diretório privado do executor,
antes da primeira criação de `journal.sqlite3`, usando a mesma criação exclusiva
e sincronizada dos computadores. O marcador permanece quando uma recusa de
autenticação remove `credential.json`: perder o journal depois da revogação ou
mesmo durante a espera pelo primeiro convite não autoriza criar histórico vazio.
A próxima inicialização bloqueia com `executor_journal_invalid` antes de preparar
recursos, conectar ou consumir um convite. Um journal legado válido recebe o
marcador; credencial salva sem journal já bloqueia mesmo sem marcador.

Após parar os processos, preserve marcador, convites e arquivos compartilhados e
restaure o journal original de backup confiável. A revogação continua valendo:
a restauração do histórico não reativa a credencial recusada; outro pareamento
segue o fluxo normal autorizado. Recibos confirmados continuam em replay e
comandos interrompidos permanecem unknown. Não apague o marcador para reparear.
Os limites de perda anterior à atualização sem marcador/credencial, perda
conjunta de journal e marcador, rollback válido e exclusão durante conexão aberta
continuam os mesmos; o marcador não é backup nem prova de conclusão do efeito.

### Resposta de pareamento interrompida

Depois de obter uma resposta HTTP de sucesso, corpo truncado, timeout ou reset
durante a leitura geram `exchange_invalid_response`. A API pode já ter consumido
o convite: a CLI pessoal ou de computadores aguarda outro, sem repetir a troca,
salvar o corpo parcial ou conectar. Confira o cadastro sem conexão pela interface
e revogue-o antes de emitir outro convite, preservando o estado privado e o
journal. A CLI lembra todos os convites recusados na execução atual: reapresentar
A depois de recusar B não repete A. Não registra convites recusados entre reinícios;
forneça outro convite novo após reiniciar. `--invite` explícito mantém precedência
sobre o arquivo mesmo depois de recusado. Falhas antes da resposta e HTTP 429/5xx
conservam sua política de indisponibilidade; este recorte não resolve toda
ambiguidade anterior aos headers.

### Falha ao salvar identidade após pareamento

`executor_credential_save_failed` encerra a CLI pessoal ou de computadores quando
a API já retornou uma identidade válida, mas a gravação local falhou. Não há
retry da troca, conexão autenticada nem limpeza do convite/journal. Uma credencial
anterior permanece intacta se a substituição falhar; eventual `credential.tmp`
também é preservado e pode conter segredo. Não o publique em diagnósticos nem o
promova automaticamente. Confira espaço/permissões e o cadastro na interface;
se não houver recuperação confiável da identidade recebida, revogue esse cadastro
e escolha um novo convite. Preserve o histórico ao reiniciar. A recusa trata erros
observáveis de filesystem, inclusive falhas ao descarregar o buffer ou sincronizar
o conteúdo do temporário com `fsync` antes da substituição. Não há sincronização
do diretório nem garantia completa de durabilidade contra queda de energia.


`executor_credential_pending` bloqueia o reinício se `credential.tmp` existir
(inclusive vazio, parcial, diretório ou link quebrado) ou não puder ser inspecionado.
A recusa ocorre antes de preparar recursos, abrir o journal, trocar convites ou
conectar, mesmo com `credential.json` válido. O temporário pode ser a única cópia
de uma identidade já emitida; não é adotado, apagado ou sobrescrito pelo executor.
Preserve uma cópia privada de todo o estado, confira o cadastro na interface e
reconcilie explicitamente qual identidade deve permanecer antes de reiniciar.
Se o cadastro pendente for revogado, seu temporário ainda precisa ser separado
do caminho ativo pelo operador, após preservação da evidência; emitir outro
convite sozinho não libera a recusa. Não altere journal, marcador ou arquivos de
trabalho. Esta proteção não detecta perda conjunta de credencial e temporário
nem substitui backup ou garante persistência após perda de energia.
Se o temporário surgir durante a chamada de pareamento, a criação exclusiva
recusa a colisão com `executor_credential_save_failed`, sem truncar o arquivo
ou seguir links. No Windows, usa `CreateFileW` com `CREATE_NEW` e
`FILE_FLAG_OPEN_REPARSE_POINT`, inclusive para links sem destino. A API pode
já ter consumido o convite; preserve a evidência
e reconcilie o cadastro antes de reiniciar. Isso não protege contra adulteração
arbitrária do diretório por outro processo após a abertura do arquivo.
