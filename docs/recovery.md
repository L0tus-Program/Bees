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

`GET /api/v1/agents/{agent_id}/tasks/{task_id}/recovery/actions` aceita `limit` entre
1 e 100 (padrão 50) e `after` com o UUID do registro anterior. A referência do cursor
precisa existir na mesma tarefa/abelha; estrangeira ou inexistente retorna 404,
sem página vazia fictícia. A ordenação é por criação/ID. Cada página usa uma
transação query_only própria, com instante/revisão e escopo
`all_canonical_task_actions`, sempre `executor_evidence: not_inspected`.

As páginas não formam uma fotografia única entre pedidos. Mudanças de estado ou
novos registros podem exigir atualizar a primeira página; contagem e página têm
instantes próprios. A interface mostra só a página confirmada, descarta a anterior
durante a consulta, preserva o cursor solicitado para repetir um GET que falhou e
ignora resposta de tarefa/revisão antiga. Erro ou cursor inválido não significam
que o histórico está vazio nem que algum efeito não ocorreu. Esta listagem cobre
registros de ações/autorizações; os agregados de chamadas de modelo continuam no
diagnóstico, sem abrir suas solicitações ou respostas privadas.

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

Esses cenários foram executados nos checkpoints das PRs #135, #136, #139, #142 e
#146. São provas do recorte nativo com provider controlado, não de qualidade de LLM
real, VM, aplicativos ou máquina pessoal. RecoveryService composto, inspeção externa
com identidade/recurso exatos, diagnóstico que componha evidência externa e matriz física de falhas
continuam nos aceites de #82/#83/#84. Um lease vencido ou PID ausente, isoladamente,
nunca substitui evidência suficiente do executor antigo.

## Executores, canais, MCP e webhooks

| Superfície | Estado após queda | O que acontece na retomada |
| --- | --- | --- |
| Comando de executor na fila (`queued`) | Ação `dispatch_started` | O comando expira (`worker_restart`) na mesma transação que declara o efeito desconhecido: nunca sai depois. |
| Comando enviado (`sent`) | Pode ter executado | Vira `unknown` (`worker_restart`). Recibo que chegar depois é guardado como evidência (`late_confirmed`/`late_failed`) sem mudar o estado; reconciliação é humana. |
| Journal do executor | `started` sem recibo | O executor marca `unknown` ao reabrir e não repete o comando. |
| Saída de canal | Registrada como `sending` antes do envio | Queda ou timeout depois do envio vira `unknown`: a mensagem não é reenviada (pode faltar, nunca duplicar). Falha de conexão antes do envio libera nova tentativa. |
| `mcp_call` | Resposta perdida após o envio | `mcp_outcome_unknown`, sem repetição automática. |
| Webhook | Reenvio do serviço externo | Com `Idempotency-Key`, o mesmo evento devolve o recibo anterior sem nova execução; sem chave, vale o intervalo mínimo. |
| Computador `removing`/`failed` | — | Remoção humana conclui; criação nova exige Bees em execução. |
