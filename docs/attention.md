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

## API humana

Rotas exigem sessão; PUT também exige Host/Origin/CSRF e JSON fechado:

| Rota | Contrato |
| --- | --- |
| `GET /api/v1/agents/{agent_id}/attention?limit=50&before={seq}` | `{items, next_before, unread_count}`; de 1 a 100 itens, sequência decrescente. |
| `GET /api/v1/agents/{agent_id}/attention/preferences` | `{agent_id, items:[{kind,enabled,revision}]}`. |
| `PUT /api/v1/agents/{agent_id}/attention/{item_id}/read` | Corpo `{client_request_id,expected_revision,read}`; retorna item atual. |
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
canais externos, push do navegador, notificações do sistema, horários silenciosos ou
avisos de rotinas/conexões. Esses recortes continuam pendentes em BEES-022.3.

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
