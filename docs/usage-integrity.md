# Integridade do consumo e recuperação de legado

BEES-022.5 (#128) protege o vínculo do consumo com sua origem. Cada entrada de
tarefa aponta para uma chamada do journal; a chamada aponta para uma execução e
a execução para uma tarefa. Abelha e conversa da entrada precisam coincidir com
a tarefa canônica, inclusive quando a tarefa não tem conversa. Uma entrada de
chat não aponta para chamada de tarefa e, quando informa conversa, ela pertence
à mesma abelha.

`UsageEntries` verifica esses vínculos antes de inserir, liquidar e materializar
registros. Uma inconsistência produz `IntegrityError`, sem retornar o registro
incoerente nem criar um evento. A migração **0016_usage_integrity.sql** acrescenta
os mesmos controles para INSERT e UPDATE por SQLite direto. Gatilhos também
impedem transferir uma chamada com consumo para outra execução, trocar a conversa
da tarefa com consumo ou transferir a conversa utilizada para outra abelha.
`runs.task_id` já é imutável desde a migração 0001.

Os controles da migração 0010 continuam em vigor: identidade, origem, vínculos,
modelo/provedor, reserva, método de estimativa e data original são imutáveis;
liquidações são finais e linhas não são apagadas. O Store faz CAS de revisão,
e os escritores usam transações `BEGIN IMMEDIATE`. Dois liquidadores concorrentes
não produzem dois consumos ou dois eventos de liquidação. O helper `settle` é
idempotente: a primeira observação final prevalece.

## Atualização segura

A atualização exige API, worker e outros escritores parados. Não executar numa
instalação ativa. O inicializador cria e verifica um backup SQLite antes de
aplicar migrações pendentes, conforme [persistência](persistence.md).

A migração 0016 verifica todas as linhas antes de instalar os gatilhos. Um vínculo
legado incoerente aborta com a causa SQLite
`usage_integrity_legacy_invalid: preserve evidence; operator review required`.
O erro externo é `MigrationError`; o detalhe fica na causa encadeada. A transação
é revertida: entradas, reservas, custos observados, ledger, versões/checksums
anteriores e backup permanecem preservados. Nenhuma linha é corrigida, removida,
reatribuída ou liquidada automaticamente. O worker recusa um esquema pendente;
não use uma versão antiga do serviço para contornar a recusa.

Mesmo ao diagnosticar uma base anterior, a guarda de orçamento soma as entradas
pelo dono gravado sem filtrar as inconsistentes. Uma reserva incoerente continua
consumindo todo o valor reservado; custo informado preserva sua contagem.
Recusar a materialização ou liquidação não libera saldo. As consultas públicas
de atividade já fecham o pertencimento de tarefas para não mostrar o consumo de
uma tarefa de outra abelha; esses controles não substituem a recusa do upgrade.

## Diagnóstico e decisão humana

Trabalhe numa cópia privada do backup, com os serviços parados. Esta consulta
somente lê identificadores e vínculos; a base e os resultados continuam privados:

```sql
SELECT u.id, u.agent_id, u.conversation_id, u.model_call_id,
       t.id AS task_id, t.agent_id AS task_agent_id,
       t.conversation_id AS task_conversation_id
FROM usage_entries u
LEFT JOIN model_calls c ON c.id = u.model_call_id
LEFT JOIN runs r ON r.id = c.run_id
LEFT JOIN tasks t ON t.id = r.task_id
WHERE (u.conversation_id IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM conversations v
    WHERE v.id = u.conversation_id AND v.agent_id = u.agent_id
)) OR (u.source = 'task' AND (
    t.id IS NULL OR t.agent_id IS NOT u.agent_id
    OR t.conversation_id IS NOT u.conversation_id
));
```

O operador precisa determinar a origem da inconsistência e preservar a evidência
original, o backup e os registros do fornecedor. Esta entrega não inclui um
comando de reparo, reatribuição de custo ou dispensa de reserva. Uma correção de
dados exige uma decisão humana e procedimento próprio auditável; nunca desative
gatilhos/FKs na instalação para fazer a atualização passar. Restaurar uma cópia
conhecida segue o procedimento de persistência e não prova que uma geração paga
não ocorreu.

Nenhuma etapa de diagnóstico, migração ou liquidação chama o provedor ou repete
uma geração para tentar reconciliar vínculos. Os testes usam SQLite real, bases
e backups temporários e concorrência de escritores; não dependem de modelo pago.
