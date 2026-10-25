# Atenção para tarefas textuais

BEES-022.3 acrescenta avisos locais por abelha sobre conclusão, falha recuperável,
decisão pendente e resultado desconhecido. O painel **Atenção** mostra o título da
tarefa, o tipo do aviso, a data, leitura e silêncio; **Abrir tarefa** permite consultar
o contexto e os controles já existentes. Objetivos, respostas, snapshots, credenciais
e parâmetros privados não entram no contrato de atenção.

Aviso lido não autoriza geração, aprova uma decisão, retoma tarefa nem reconhece
resultado desconhecido. Essas operações continuam nas interfaces próprias de
[tarefas](tasks.md), [aprovações](approvals.md) e [autonomia](policies.md).

## Fatos, histórico e transações

A migração `0012_attention.sql` acrescenta três tabelas:

| Tabela | Papel |
| --- | --- |
| `attention_items` | Projeção durável do fato, com UUID, sequência, abelha/tarefa, aprovação opcional, evento canônico de origem e estado de leitura com revisão. |
| `attention_preferences` | Preferência humana por abelha e tipo; ausência significa habilitado, revisão virtual zero. |
| `attention_commands` | UUID, destino e digest da intenção humana, para idempotência e investigação; append-only. |

O writer central de `domain_events` chama `project_event` dentro do mesmo savepoint
e da mesma transação do registro original. Não há trigger de geração de avisos nem
fila secundária: se a projeção falhar, o registro e o evento também são revertidos.
O hook não publica eventos recursivos nem executa código de plugins.

Tipos persistidos:

- `task_completed`: tarefa concluída, associada ao run atual quando existente.
- `task_failed`: tarefa terminal falha ou run pausado/falho com erro e atenção
  necessária, como resposta truncada, contexto alterado ou limite atingido.
- `approval_required`: aprovação criada e vinculada à tarefa textual.
- `outcome_unknown`: journal de modelo ou ação com resultado desconhecido.

Espera por aprovação usa o aviso próprio. Negação humana resolve esse aviso e
preserva `approval_denied` na execução/auditoria, sem gerar outra falha que peça
atenção. Parada global e unknown não geram outra falha genérica. A identidade
`(kind, entity_type, entity_id)` evita duplicar o mesmo
fato em replay/atualização de metadados. Conclusões/falhas usam o run canônico;
registros de tarefa sem run usam a identidade da tarefa. Uma retomada que cria outro
run pode produzir outra falha. Action textual e ModelCall da mesma geração unknown
compartilham a identidade do ModelCall, com o evento original preservado na projeção.

FKs e gatilhos verificam vínculos abelha/tarefa/run/aprovação, tornam a identidade e
o evento de origem imutáveis e impedem apagar avisos ou comandos. Atualizações humanas
exigem CAS; concorrência obsoleta falha com conflito. Reutilizar UUID com outra
abelha, destino ou intenção também falha. Replay válido retorna o **estado atual**,
preservando alterações humanas posteriores; não reaplica o estado antigo.

Leitura e preferências registram `actor=user`, origem, UUID de correlação, revisão e
valores anterior/posterior em `domain_events`, sem conteúdo livre. A entidade de
auditoria da preferência usa UUID determinístico por abelha/tipo.

## Decisões, silêncio e contagem

`requires_attention` indica um fato aplicável. Decisão pendente só mantém esse campo
quando a tarefa está aguardando aprovação e `ApprovalService.validity` confirma
snapshot, controle, run atual, regras e TTL. Decisão resolvida, expirada, cancelada
ou obsoleta conserva o item histórico com `requires_attention=false`. A verificação
usa o último run canônico, inclusive com mais de mil runs históricos; GET não altera
a aprovação nem cria aviso. Unknown conserva `requires_attention=true`, mesmo após
leitura, silêncio ou reconhecimento humano na tarefa; esses atos não comprovam o
resultado do efeito.

Todos os tipos começam habilitados. Desabilitar um tipo marca seus itens como
`muted`, sem apagar fatos, decisões ou falhas do painel. Reabilitar volta a contar
itens ainda não lidos. `unread_count` conta apenas itens não lidos, não silenciados e
aplicáveis. Leitura é independente de `requires_attention`.

O título é consultado da própria tarefa, sem cópia no journal. `task_title`, `muted`
e `requires_attention` podem mudar em GET mantendo a revisão do aviso; essa revisão
acompanha apenas leitura humana. A interface deve aceitar projeções atuais na mesma
revisão e recusar regressões.

## Visualização por conversa

O seletor **Avisos por conversa** oferece as conversas próprias por título e data,
com carregamento explícito de mais opções. Cada tarefa tem uma conversa de execução;
esse vínculo canônico define o filtro. Texto do modelo, conversa de origem de uma
delegação ou metadata livre não substituem esse vínculo. **Todas as conversas da
abelha** conserva a visualização anterior. Reabrir o painel começa nesse escopo.

A lista, a paginação e a contagem de não lidos usam a mesma conversa. Um cursor de
outra conversa é recusado no modo filtrado. Consultar uma conversa inexistente ou
de outra abelha retorna 404; a leitura de um aviso também verifica esse vínculo
antes do replay. A resposta filtrada identifica `conversation_id`; sem filtro,
o contrato de resposta anterior é preservado.

Trocar o filtro abre uma nova consulta e descarta respostas atrasadas do escopo
anterior. Falha de consulta permanece erro, sem converter uma lista antiga em
resultado da nova conversa. Enquanto uma alteração humana estiver em curso ou
sem confirmação, ou os detalhes da tarefa estiverem abertos, o seletor fica
bloqueado para conservar o contexto. Recuperar uma leitura usa o mesmo UUID e a
mesma conversa. Preferências de silêncio continuam valendo para a abelha inteira;
a interface informa esse alcance mesmo na visualização filtrada.

O catálogo de conversas só retorna ID, título limitado e data de criação. Ele usa
páginas limitadas por posição, com deduplicação na interface caso uma conversa nova
desloque a página seguinte. A paginação dos avisos continua por sequência exclusiva.
Nenhuma consulta cria fatos, tarefas, mensagens, concessões ou chamadas de modelo.

## API humana

Rotas exigem sessão; PUT também exige Host/Origin/CSRF e JSON fechado:

| Rota | Contrato |
| --- | --- |
| `GET /api/v1/agents/{agent_id}/attention?limit=50&before={seq}` | `{items, next_before, unread_count}`; de 1 a 100 itens, sequência decrescente. |
| `GET /api/v1/agents/{agent_id}/attention?conversation_id={id}&before={seq}` | Mesmos campos e `conversation_id`; lista e contagem limitadas à conversa própria. |
| `GET /api/v1/agents/{agent_id}/attention/conversations?limit=50&offset=0` | `{agent_id,items:[{id,title,created_at}],next_offset}`; catálogo próprio limitado, sem mensagens ou metadata. |
| `GET /api/v1/agents/{agent_id}/attention/preferences` | `{agent_id, items:[{kind,enabled,revision}]}`. |
| `PUT /api/v1/agents/{agent_id}/attention/{item_id}/read` | Corpo `{client_request_id,expected_revision,read}`; retorna item atual. |
| `PUT /api/v1/agents/{agent_id}/attention/{item_id}/read?conversation_id={id}` | Mesmo comando; exige aviso da conversa selecionada antes de consultar o replay. |
| `PUT /api/v1/agents/{agent_id}/attention/preferences/{kind}` | Corpo `{client_request_id,expected_revision,enabled}`; retorna preferência atual. Revisão zero cria a preferência. |

Cada item contém somente `id`, `agent_id`, `task_id`, `task_title`, `approval_id`,
`kind`, `created_at`, `revision`, `read`, `requires_attention` e `muted`. IDs de
outros agentes retornam 404 antes do replay. Paginação usa cursor exclusivo por
sequência, mantendo páginas antigas estáveis diante de novos fatos.

## Operação, consultas e limites

Índice `(agent_id,seq DESC)` atende paginação com busca pelo cursor; índice parcial
dos não lidos atende contagem. Preferências usam chave composta `(agent_id,kind)`.
Conclusões/falhas/unknown são contados no SQLite; somente aprovações pendentes em
tarefas aguardando decisão passam pela validação canônica em leitura. Custo de
validação cresce com decisões pendentes, não com todos os avisos históricos.

Os vínculos de evento, tipo, abelha, tarefa e data permitem métricas derivadas de
conclusões, falhas e decisões, sem depender exclusivamente do estado atual da tarefa.
O estado de leitura é pessoal ao usuário desta instalação individual. Não há tenants,
canais externos, push do navegador, notificações do sistema ou horários silenciosos.
O [scheduler](routines.md) reutiliza os fatos de atenção da tarefa vinculada e pode
gravar um resumo idempotente na conversa, conforme a definição humana da rotina.
Isso não acrescenta preferências globais de avisos de rotinas/conexões; esses
recortes continuam pendentes em BEES-022.3.

A execução manual confirmada usa a mesma tarefa vinculada e a mesma regra de
resumo por ocorrência. `always` permite o resumo terminal; `failures` omite o
resumo de conclusão; `none` omite esses resumos. A escolha não apaga histórico,
aprovações pendentes ou resultado desconhecido do painel **Atenção**. Reconciliar
ou consultar a mesma ocorrência não publica outro resumo. Ocorrências diferentes
continuam sendo fatos distintos: ainda não há classificação semântica de “sem
mudança relevante” entre resultados.

Uma sugestão de rotina na conversa é texto para revisão. Abrir o rascunho e salvar
uma agenda pausada não conclui uma tarefa nem produz aviso de execução. O vínculo
com a mensagem original serve à rastreabilidade; não é uma aprovação de ferramenta.

Não há backfill: migração 12 preserva eventos e tarefas legados e começa a projetar
novos fatos. Não inventa alertas antigos nem depende de GET para inicialização.
Atualizar exige API/worker/escritores parados, backup e decisão operacional; o worker
recusa schema pendente e nunca migra. A instalação padrão do usuário não foi alterada
por este checkpoint. Consulte [persistência](persistence.md).

Avisos, comandos e auditoria são canônicos e não entram na limpeza de cache. Não há
TTL/expurgo automático nesta etapa. IDs, títulos e timestamps continuam dados do
usuário: backups/exportações exigem o mesmo acesso privado do banco. Uma política
futura de retenção deve preservar evidências unknown e referências de eventos antes
de arquivar; não apagar por silêncio.

Validação usa SQLite real e bancos descartáveis: worker com modelo falso, duas
falhas em runs distintos, unknown sem repetição, decisão/expiração/revisão de política,
rollback de projeção, CAS concorrente, replay/isolamento, GET sem escrita, constraints,
paginação e upgrade 11→12 com backup/integridade/FKs. Nenhuma geração paga, mensagem
externa ou migração do banco pessoal é necessária para esses testes.

Os testes do filtro acrescentam contagem por conversa, cursor fora do escopo,
inserção entre páginas, reabertura, catálogo próprio limitado, metadata de origem
forjada e replay de leitura em conversa errada. API/ASGI autenticada verifica
compatibilidade sem filtro, sessão, CSRF e contratos fechados. React/DOM verifica
os três idiomas, resposta atrasada, erro sem falso vazio, leitura incerta com o
mesmo pedido, bloqueio ao abrir detalhes e catálogo com páginas deslocadas.
Uma instalação privada com a interface compilada passou por 12 verificações em
Chromium: português, inglês e espanhol em 390/1280 px, paginação real de 50+2 avisos,
catálogo de 50+9 conversas, conversa vazia e isolamento. Uma leitura confirmada no
servidor teve a resposta descartada; após reiniciar a API, repetir o mesmo UUID,
corpo e conversa recuperou um único comando. HTTP recusou a conversa de outra
abelha antes do replay. Erro de consulta não exibiu cartões antigos ou falso vazio.
Reload preservou os fatos e unknown continuou pedindo atenção depois de lido.

A comparação canônica conservou 12 tabelas de negócio, com somente duas alterações
humanas de leitura e seus eventos. A preparação usou uma falha de transporte
controlada para produzir unknown; a navegação não iniciou chamadas de modelo,
tarefas ou efeitos externos. Essa prova não representa aceite com modelo ou VM reais.

## Conexões que precisam de você

A aba **Atenção** mostra, acima dos avisos, as conexões que exigem ação humana, calculadas do estado atual (sem duplicatas nem itens a marcar como lidos): canal com token recusado ou bot sem acesso, conector MCP com falha, computador com falha ou runtime desconectado, comandos aguardando executor desconectado/revogado e gatilho por webhook recusado. Cada item diz o que fazer e onde; nenhum segredo, argumento ou mensagem é exibido (`GET /api/v1/agents/{agente}/connections/health`).

Desativar o aviso **Decisão necessária** também silencia o aviso correspondente nos canais Telegram/Discord.
