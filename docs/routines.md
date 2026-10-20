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

## Sugestões pela conversa

**Pedir uma sugestão de agenda nesta mensagem** começa desmarcado. Ao enviar,
a chamada textual normal recebe a instrução para sugerir dados, com o orçamento,
política e provedor atuais. Não acrescenta ferramenta de agenda, concessão ou
execução; o modelo pode pedir dados ausentes. O opt-in faz parte do UUID/hash do
turno, e mudar a opção não permite repetir uma geração com o mesmo identificador.
O caminho continua disponível para modelos sem chamadas de ferramenta.

Uma sugestão usa um único bloco `bees-routine` com nome, instruções, frequência,
horário, fuso IANA, entradas textuais, aviso e tolerância. O parser rejeita campos
extras, IDs/autoridade, segredos conhecidos, chaves duplicadas, múltiplos blocos,
JSON inválido/números especiais e tamanho excessivo. Exemplo do conteúdo do bloco:

```json
{
  "name": "Resumo diário",
  "instructions": "Conferir as fontes e preparar resumo para revisão.",
  "schedule": {"frequency": "daily", "time": "09:00", "weekdays": []},
  "timezone": "America/Sao_Paulo",
  "inputs": {"tema": "Ciência"},
  "notification": "always",
  "grace_seconds": 60
}
```

**Revisar sugestão de rotina** consulta a mensagem canônica, somente leitura e
sob sessão. Ela precisa ser uma resposta da mesma conversa/abelha. A origem inclui
IDs e SHA-256 UTF-8 do texto completo. Mensagens são append-only; criar a agenda
confere novamente escopo/hash. Esses dados preservam referência, não certificado
de qualidade ou autoridade do modelo. O endpoint não cria rascunho persistido,
rotina, tarefa, grant ou aviso só por ler a sugestão.

O usuário escolhe explicitamente uma skill local e sua revisão. Ambiente vem
dessa skill, destino começa na conversa atual e apenas suas entradas declaradas
são aproveitadas, com aviso para conferir os campos. O editor começa preparatório,
sem recurso fixado. Nome, instruções, horário/fuso e demais campos continuam
editáveis. Só a confirmação salva uma definição humana **pausada**, com
`proposal_source` mínimo na metadata; não é possível criar uma proposta já ativa.
Habilitar execução programada no editor ainda exige ativação separada depois.
O texto original fica disponível inerte, sem interpretação de HTML/script.

Criar usa o UUID/CAS/recuperação das definições. Resposta perdida permanece na
aba; reload não escreve. Reabrir a sugestão e **Consultar e verificar o mesmo
pedido** consulta as agendas antes de repetir explicitamente a intenção original.
O replay devolve a criação original mesmo depois de cancelar a rotina. Consulte o
estado atual em Rotinas; o recibo não afirma que ela continua pausada. Outra
intenção pendente ou registro inválido bloqueia nova criação nessa seção.

## Executar uma vez, sem ativar a agenda

**Executar uma vez** revisa definição, entradas, recurso e limites antes de criar
uma tarefa. É uma intenção humana nova, com UUID e CAS da revisão. Agendas
preparatórias ou pausadas podem ser executadas assim; legados sem definição e
rotinas canceladas são recusados. O pedido não ativa a rotina, muda sua revisão,
avança o cursor ou repõe horários. Uma ocorrência em curso, pendente ou de
resultado desconhecido impede outra execução manual da mesma rotina.

Conversa/tarefa/run, ocorrência e recibo são confirmados em uma transação curta,
sem rede. A ocorrência já fica vinculada à tarefa na fila comum. O comando
humano imutável fixa o recurso e é revalidado antes de cada geração/efeito; texto
de modelo ou metadata sem esse comando não autoriza execução manual. Concessões,
skill habilitada, políticas, orçamento e parada global continuam atuais. O worker
precisa anunciar `routine_manual_version=1`; um worker antigo não recebe o modo.
Não há novo esquema, instalação ou concessão implícita.

A revisão humana da rotina permanece fixa. Edição, cancelamento ou outra mutação
da revisão invalida próximos despachos antigos. Para pausar/cancelar uma tarefa
manual já criada, use seus controles próprios: uma agenda que já estava pausada
continua pausada durante a execução explicitamente solicitada. Resultado
`outcome_unknown` não é apagado por replay ou por novo UUID e impede repetição.

O recibo guarda a resposta original da criação, mesmo se a tarefa já terminou.
Consultar esse recibo não cria tarefas; o histórico mostra o estado atual. Uma
resposta perdida conserva o UUID mínimo no armazenamento da aba, sem entradas.
Após reload não há POST automático: **Consultar recibo do mesmo pedido** verifica
o comando. Se não houver recibo, outro botão permite reenviar explicitamente a
mesma intenção. CAS e idempotência ainda valem se o primeiro pedido estiver em
curso. O replay confirmado funciona com recurso/worker indisponível e após
edição/cancelamento; não autoriza outra execução. Histórico e tarefa identificam
a origem manual.

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
| GET | `/proposals/{conversation_id}/{message_id}` | Sugestão privada e origem canônica, sem escrita |
| POST | vazio | Cria com `client_request_id`, `definition` e `active` opcional |
| GET | `/{routine_id}` | Estado e definição da revisão atual |
| POST | `/{routine_id}/revisions` | Edita com UUID, CAS e definição; resultado pausado |
| PUT | `/{routine_id}/status` | UUID, CAS e `active`, `paused` ou `cancelled` |
| GET | `/{routine_id}/revisions` | Snapshots humanos imutáveis, mais recentes primeiro |
| POST | `/{routine_id}/manual` | Cria uma tarefa manual com UUID e CAS, sem ativar agenda |
| GET | `/{routine_id}/manual/{request_id}` | Recibo humano original, sem execução |
| GET | `/{routine_id}/occurrences` | Histórico temporal, mais recente primeiro |

As consultas paginadas aceitam `limit` de 1 a 100 e `offset` não negativo.
O endpoint manual aceita apenas uma intenção humana explícita; não há endpoint
GET para materializar/despachar ou endpoint de efeito da ferramenta.
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

A execução manual acrescenta testes de recibo offline, alteração de revisão,
concorrência, pausa/cancel/revoke após aprovação, regra exata reutilizada, comando
forjado recusado, crash real dentro do writer e unknown sem retry. A interface
testa os três idiomas, confirmação, reload sem POST, consulta do recibo, reenvio
explícito com o mesmo UUID, armazenamento adulterado e worker indisponível.

Na prova manual com Chromium/API e worker CLI padrão, os três idiomas em390/1280
passaram por revisão sem escrita. Uma única confirmação perdeu realmente a resposta
após commit; reload não fez POST e a consulta do recibo abriu a tarefa original.
Com a aba fechada e reinício durante aprovação, houve duas gerações HTTP controladas,
um único início POSIX, uma ocorrência concluída e um aviso. A definição permaneceu
pausada na revisão1, sem alterar a próxima execução; zero chamadas pagas. Essas
provas não representam LLM real ou execução em VM/apps/máquina pessoal.

A prova de proposta usou uma geração HTTP textual controlada e nenhum efeito
até a confirmação. Chromium pt-BR/en/es390/1280 verificou seleção humana de
procedimento, defaults preparatórios, texto/script inerte e revisão sem POST.
A pessoa alterou nome/fuso; a criação perdeu a resposta depois do commit e foi
recuperada após reload com o mesmo UUID/body, precedida de consulta. O estado
canônico contém uma agenda pausada na revisão1, uma intenção de criação, nenhuma
tarefa/ocorrência e nenhum grant novo. O hash da mensagem original foi conferido.
Não há LLM real ou aceite semântico de qualidade inferido dessa prova.
