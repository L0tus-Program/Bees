# Rotinas e contrato temporal

BEES-021.1 (#69) define a agenda canônica e a persistência de ocorrências.
`RoutineService` calcula instantes e registra decisões, sem despachar efeitos.
O `RoutineScheduler` de BEES-021.2 (#70), no processo do worker padrão, cria tarefas
pelo [ciclo de skill](skills.md#reexecutar-em-tarefa-nova). Cada geração/ferramenta
segue journal, orçamento, claims e políticas comuns. A interface (#71) permite
optar pela execução programada, ativar e abrir a tarefa e suas decisões no histórico.

## Preparar ou executar automaticamente

O padrão continua `execution_mode=prepare`: uma agenda assim não cria tarefas,
mesmo ativa. Definições anteriores sem esse campo permanecem preparatórias após
upgrade. Marque **Permitir tarefas automáticas quando esta agenda for ativada**,
confira entradas, destino e limites e salve. A revisão fica pausada; **Ativar agenda**
exige outra confirmação e só considera o próximo horário. São até quatro gerações,
dezesseis propostas de ferramenta e 120 segundos ativos por ocorrência, com possível
consumo do modelo configurado. Espera por decisão não concede acesso nem repete geração.

O recorte aceita texto sem ferramentas ou `own` com somente `text.normalize`.
Ambos exigem o runtime desse ciclo: recurso POSIX preparado pelo operador, modelo
compatível com ferramentas, instalação/grant atuais e worker ativo. Apps, navegador,
VM e máquina pessoal continuam indisponíveis. Fechar o navegador não interrompe a
agenda; serviço parado aparece indisponível, com recuperação limitada pela tolerância.

O salvamento humano fixa `runtime_binding`: instalação, recurso, extensão, hash do
manifesto e versão `1` do contrato. É referência de compatibilidade, não concessão.
Outro recurso ou versão gera `runtime_changed`, sem escolher outro executor. Para
trocar, revise e escolha **Selecionar recurso atual para esta revisão**, confira e
salve/ative novamente. Uma instalação nova também precisa de grant humano próprio.

## Definição humana e identidade

Uma rotina operacional declara nome, instruções, frequência, horário, fuso IANA,
ID e versão imutável da skill, entradas, ambiente requerido (`none`, `own` ou
`personal`), conversa de destino pertencente à mesma abelha, aviso (`always`,
`failures` ou `none`) e tolerância de atraso em segundos. O ambiente é um
requisito declarativo: não concede ferramentas, acesso, aprovação ou autoridade
para trocar de ambiente/provedor. A composição confiável do executor resolve e
valida a identidade real do ambiente antes do despacho.

As entradas precisam satisfazer os nomes obrigatórios e permitidos pela revisão
fixada da skill. O ambiente declarado precisa coincidir com essa revisão. Criar
uma rotina não habilita uma skill: a revisão selecionada precisa estar habilitada
para ativar a rotina e continua sendo verificada na materialização. Uma edição ou
desabilitação da skill provoca `skill_unavailable`, sem selecionar outra versão.
O consumidor também deve revalidar a skill e a rotina na sua transação de claim;
`pending` representa elegibilidade temporal, nunca autorização para um efeito.

O scheduler revalida a criação atômica da tarefa, e o executor repete as guardas
antes de cada geração e efeito. Definição, instruções, entradas, revisão, horário e
ocorrência ficam vinculados. Editar, pausar ou cancelar invalida próximos despachos
antigos; um efeito já iniciado conserva o journal. Grants e políticas são atuais.
Uma regra salva pela decisão própria pode ser reutilizada no mesmo recurso/revisão/
entradas; outra rotina, revisão ou nova regra `ask`/`deny` não herda essa autorização.

A rotina recebe UUID e revisão humana crescente. Cada mutação exige
`client_request_id` UUID não nulo; edições e mudanças de estado também exigem
`expected_revision` (CAS). Repetir exatamente uma intenção devolve sua resposta
original; reaproveitar o UUID para outro conteúdo, alvo ou abelha retorna conflito.
As revisões e respostas são imutáveis e persistidas na mesma transação da mutação.
A origem é `human`, definida pelo serviço sob sessão, e não pode ser concedida
por texto de modelo, arquivo ou campo recebido do cliente.

A criação fica pausada por padrão. `active: true` é uma intenção humana explícita
e exige skill habilitada. Toda edição pausa a rotina; é necessário retomar
explicitamente a revisão resultante. Pause, cancelamento e edição cancelam
ocorrências `pending` com motivo `routine_changed`. Ocorrências já iniciadas
conservam seu estado/evidência. O cancelamento da rotina é terminal; outra agenda
exige nova criação humana. Ao ativar/retomar, a agenda começa estritamente depois
do limite atual; horários transcorridos durante a pausa não são repostos.

## Frequência, fuso e DST

A v1 aceita somente:

- `daily`: uma vez por dia no horário local `HH:MM`, sem `weekdays`.
- `weekly`: no horário local nos dias declarados por `weekdays`, usando
  segunda-feira=0 até domingo=6, sem repetições.

Horários/instantes persistidos são UTC com timezone. O cálculo usa `zoneinfo`
e o fuso IANA da revisão. O resultado de `next_run(definition, after)` é sempre
estritamente posterior a `after`, inclusive quando `after` é exatamente o horário
agendado. O horizonte de busca é limitado a 15 dias para essas frequências,
independentemente da duração de uma indisponibilidade.

No avanço DST, um horário local inexistente é pulado: `02:30` em Nova York em
08/03/2026 não executa; a próxima ocorrência é 09/03 às `06:30 UTC`. No recuo DST,
somente a primeira passagem (`fold=0`) é considerada: `01:30` em 01/11/2026 ocorre
às `05:30 UTC`; a repetição às `06:30 UTC` não cria outra ocorrência.

O cursor durável é separado da revisão humana e não muda o CAS da configuração.
Cada avaliação avança o cursor atomicamente junto às ocorrências. Um relógio
recuado não retrocede esse cursor nem recria um horário já avaliado. Uma edição
ou retomada usa o maior valor entre relógio e limite já avaliado.

## Atraso, indisponibilidade e sobreposição

A política inicial é `misfire_policy=skip`, `overlap_policy=skip`. A tolerância
`grace_seconds` é 60 por padrão, entre 0 e 3600. `catch_up` e `queue` são recusados
nas novas definições: não há fila ou recuperação ilimitada escondida.

Uma avaliação arbitrariamente atrasada grava no máximo dois registros:

- Um `skipped`/`misfire` cobre o intervalo do primeiro horário pendente até o último
  horário perdido, usando `scheduled_at` e `window_end`. A janela é evidência
  explícita dos horários ignorados, sem expandi-los em milhares de linhas/tarefas.
- Se o horário mais recente ainda estiver dentro da tolerância, ele pode gerar
  uma única ocorrência `pending`/`scheduled`, separada da janela ignorada.

Se já houver `pending`, `running` ou `outcome_unknown` da mesma rotina, inclusive
revisões anteriores, o novo horário fica `skipped`/`overlap`. Um resultado
desconhecido não é repetido automaticamente. O serviço temporal não afirma que
um efeito externo ocorreu, terminou ou pode ser repetido.

A identidade temporal é única em `(routine_id, routine_revision, scheduled_at)`.
Mesmo conteúdo ou conteúdo divergente não podem ocupar essa identidade duas
vezes. `window_end` só difere de `scheduled_at` para uma janela `skipped`.
A rotina/revisão/instante/janela/criação da ocorrência são imutáveis. A ocorrência
tem sua própria revisão, incrementada nas transições; o esquema aceita somente
`pending -> running|cancelled|skipped` e
`running -> completed|failed|outcome_unknown`. Registros não são apagados.
O vínculo eventual com uma tarefa não pode ser substituído depois de preenchido.
O scheduler deve validar esse vínculo e o escopo da abelha ao criá-lo.

## Worker, vínculo e recuperação

O worker avalia lotes de até 32 agendas a cada três segundos, mesmo durante uma
geração em andamento. Não há timer na API ou broker obrigatório. Criação de
conversa/tarefa/run e vínculo `pending -> running` usam um writer SQLite, CAS da
ocorrência e UUID determinístico. Não existe rede/efeito externo nessa aquisição;
ela não precisa de outra lease separada. Após commit, o claim/lease/fencing comum
coordena despachos. Duas instâncias e crashes pré/pós-commit não duplicam tarefas.

Parada global impede materializar/despachar trabalho novo. Recurso/ferramenta
indisponível ou versão incompatível pula a ocorrência com motivo, sem instalação
ou troca implícita. O histórico projeta a situação da tarefa vinculada, incluindo
espera por aprovação e pausa. **Abrir tarefa e decisões** permite consultar resultado
e controles. A ocorrência permanece `running` durante espera ou revisão; cancelar
a tarefa vinculada libera a sobreposição depois da reconciliação.

Conclusão/falha/desconhecido são reconciliados com o journal. Cancelamento de tarefa
já criada usa `failed`/`task_cancelled` no contrato persistido. `outcome_unknown` fica
preservado e impede outra ocorrência automática; retomar a tarefa não apaga essa
evidência. A reconciliação humana desse resultado continua separada do scheduler;
não apagar registros ou repetir efeitos para corrigir o histórico. Ativar a agenda
em outra revisão também não retoma a tarefa antiga.

Fatos de aprovação, erro e desconhecido usam a atenção durável das tarefas.
`notification` controla resumos opcionais na conversa ao terminar ou pular uma
ocorrência: `always`, `failures` ou `none`, sem entradas ou resultado bruto. Aviso e
conclusão são atômicos; janelas puladas têm recuperação após crash com identidade
determinística, sem duplicação. Não há email, webhook ou outro envio externo.

## API e persistência

O prefixo é `/api/v1/agents/{agent_id}/routines`, sob sessão humana e as proteções
Host/Origin/CSRF existentes. Os endpoints são:

| Método | Caminho relativo | Resultado |
| --- | --- | --- |
| GET | vazio | Lista paginada de rotinas, sem avaliação ou efeitos |
| GET | `/runtime` | Capacidade atual do worker/modelo/recurso, sem efeitos |
| POST | vazio | Cria com `client_request_id`, `definition` e `active` opcional |
| GET | `/{routine_id}` | Estado e definição da revisão atual |
| POST | `/{routine_id}/revisions` | Edita com UUID, CAS e definição; resultado pausado |
| PUT | `/{routine_id}/status` | UUID, CAS e `active`, `paused` ou `cancelled` |
| GET | `/{routine_id}/revisions` | Snapshots humanos imutáveis, mais recentes primeiro |
| GET | `/{routine_id}/occurrences` | Histórico temporal, mais recente primeiro |

As consultas paginadas aceitam `limit` de 1 a 100 e `offset` não negativo.
Não há endpoint HTTP para materializar, despachar ou forçar uma ocorrência.
`RoutineService.evaluate(..., expected_revision=..., now=...)` é uma operação
interna transacional; consumidores precisam usar a revisão canônica atual.

`execution_mode` e `runtime_binding` são declarações opcionais; o binding é resolvido
e validado canonicamente no salvamento humano. Comandos preparatórios antigos
conservam o hash de serialização anterior. Não há nova migração neste checkpoint;
preserve código compatível com os contratos gravados e não use uma versão antiga
contra definições novas. Ocorrências projetam `metadata.task_status`, sem entradas
ou conteúdo de outra tarefa.

A migração `0015_routine_occurrences.sql` adiciona definições, cursores, comandos
e ocorrências sem reescrever metadados antigos. Siga a manutenção com serviço
quiescido e backup de [persistência](persistence.md). O inicializador cria e
verifica o backup antes de migrar uma base existente e aplica as alterações em
uma transação exclusiva. Histórico e metadados legados continuam presentes.

Uma rotina antiga sem definição humana aparece com `origin=legacy` e
`definition=null`. Mesmo quando seu metadado antigo diz `active`, ela não é
avaliada. Não recebe cursor nem ativação por migração. Uma revisão humana
completa pode convertê-la para a v1, inicialmente pausada; não é possível ativar
os campos genéricos legados diretamente.

## Evidência e limites

`packages/core/tests/test_routines.py` cobre DST inexistente/ambíguo, frequência
semanal, UTC, relógio recuado, indisponibilidade de vinte anos, limite de duas
linhas, sobreposição, revisão/pausa na fronteira, cancelamento terminal,
concorrência, falha com rollback, unicidade, imutabilidade, escopo da abelha e
migração com backup preservando legados. `apps/api/tests/test_routines_api.py`
verifica sessão, Host/Origin/CSRF, UUID/CAS, contratos fechados, skill fixada e
consultas sem criação de tarefas.

`test_routine_scheduler.py` acrescenta dois dispatchers concorrentes, crashes reais
pré/pós-vínculo, restart, revisão fixa, legado preparatório, troca explícita de
recurso/versão, nova regra ask, deny/revoke/pausa antes do efeito, parada global,
indisponibilidade longa, avisos e desconhecido sem retry. A API testa sessão e
sondagem somente leitura, inclusive heartbeat incompatível.

Na prova cloud, o worker CLI padrão criou a tarefa no horário UTC com a aba fechada.
A agenda preparatória ativa não criou ocorrência. Reiniciar o worker durante a
espera conservou a aprovação própria; depois dela houve um início POSIX real,
duas gerações HTTP controladas, uma ocorrência concluída e um resumo na conversa.
Chromium pt-BR/en/es390/1280 também comprovou resposta perdida após commit/reload
com o mesmo UUID e sem POST automático. Isso não afirma seleção por LLM real,
execução de apps/VM ou aceite humano da qualidade da entrega.
