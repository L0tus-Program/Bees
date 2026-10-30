# Parada global

Primeiro checkpoint de BEES-022.2 em `packages/core/src/bees_core/safety.py`. **Parar tudo** impede que o Bees inicie qualquer nova geração, tarefa, ação de ferramenta ou etapa de provisionamento, em todas as abelhas. A parada é durável: sobrevive a reinícios da API e do worker e só termina com uma retomada humana.

A parada **não cancela no provedor** o que já foi enviado. Uma chamada em andamento termina normalmente; seu resultado e seu consumo continuam registrados. Ela também não apaga dados, não muda regras de autonomia, limites ou concessões e não desfaz efeitos já confirmados.

O ponto de corte é o commit que autoriza o efeito (reserva de consumo e `dispatch_started` no chat e no worker, `dispatch_started` da ação, `begin` do provisionamento). Uma parada confirmada depois desse commit não impede o envio já autorizado; ele aparece nas contagens em andamento.

## Interface

O painel principal mostra o estado no topo da página, acima das abelhas:
- **Execução ativa**, com o botão **Parar tudo**;
- **Bees parado**, com a data, o motivo opcional e o botão **Retomar**.

Cada decisão abre uma confirmação que descreve o efeito. A interface só mostra sucesso depois do estado canônico devolvido pelo serviço. Se a resposta se perder (rede, timeout ou erro 5xx), **Tentar novamente** reenvia o mesmo pedido: o serviço o reconhece pelo identificador e não aplica a parada duas vezes. Nesse caso o motivo fica fixo, porque um replay só é aceito com conteúdo idêntico. Cancelar depois de um envio sem confirmação relê o estado.

Enquanto o Bees está parado, a lista **Operações já enviadas, encerrando** mostra o que ainda não terminou. A contagem é atualizada a cada 5 segundos por até 5 minutos, e **Atualizar estado** consulta de novo a qualquer momento. Ela não comprova que o efeito remoto acabou:

| Contagem | Origem |
| --- | --- |
| Chamadas de tarefas ao modelo | Journal do worker em `dispatch_started` |
| Ações de ferramentas | Ações em `dispatch_started` |
| Respostas do chat aguardando | Reservas de consumo do chat ainda sem liquidação |
| Etapas de provisionamento | Claims de provisionamento em `dispatch_started` |

Uma reserva de chat cujo processo terminou antes de liquidar continua contada como aguardando (veja [consumo e limites](budgets.md)). Ações e claims em `dispatch_started` sem dono seguem suas próprias regras de recuperação; a retomada não os repete.

Um chat enviado com o Bees parado recebe o erro `global_stop` e nada vai ao provedor.

## Estado e comandos

`safety_control` (migração 0011) tem uma única linha com `status` (`running` ou `stopped`), `generation`, `revision`, `changed_at` e `reason`. A instalação começa em execução, inclusive após a migração de uma base existente.

| Comando | Efeito |
| --- | --- |
| `stop` | `running` → `stopped`; a geração aumenta em 1 |
| `resume` | `stopped` → `running`; a geração não muda |

- Todo comando exige a revisão lida (CAS). Revisão diferente ou comando que não altera o estado retorna 409.
- Cada comando tem um `client_request_id` UUID. Repetir o mesmo pedido idêntico devolve o estado **atual** sem reaplicar; o mesmo UUID com outro conteúdo é recusado. Se outra sessão mudou o estado depois do comando original, o replay devolve esse estado mais novo: a interface só mostra sucesso quando o estado devolvido é o pedido e a revisão avançou, e caso contrário relê e avisa do conflito.
- `safety_commands` registra o histórico: tipo, revisão esperada e resultante, ator, motivo e data. Gatilhos SQL garantem transições válidas, impedem apagar o estado e tornam os comandos imutáveis. Cada mudança também gera um evento `safety_control` (`stopped` ou `running`) com revisões, geração e status anterior, sem o texto do motivo. Ainda não há rota de listagem desse histórico.
- `provisioning_claims.safety_generation` guarda a geração do claim e é imutável; claims anteriores à migração ficam sem valor e contam como geração 0.

API, com sessão humana, Origin e CSRF como nas demais rotas:
- `GET /api/v1/safety`: estado e contagens em andamento;
- `POST /api/v1/safety/commands`: `{client_request_id, kind, expected_revision, reason?}`. O motivo tem até 500 caracteres.

Estado e contagens são lidos na mesma transação, com snapshot global
(`event_seq`, `observed_at`). A UI compara snapshot, revisão e geração antes de
aceitar uma resposta. Uma consulta atrasada não desfaz uma parada já confirmada;
novas contagens podem chegar com a mesma revisão do controle.

Conteúdo de conversas, sites, documentos ou modelos não chega a esse canal. Não existe parada ou retomada automática.

## Onde a parada é verificada

A verificação ocorre antes de cada efeito novo e dentro da mesma transação que o autorizaria:

- **Chat**: antes de gravar a mensagem, junto da política e do limite de consumo, e de novo na autorização imediatamente antes da rede. A segunda verificação também compara a geração: parar e retomar durante a preparação de um chat ainda recusa esse envio. Nesse caso a mensagem já gravada fica sem resposta, como numa revogação de política no mesmo intervalo.
- **Worker**: não reivindica tarefas enquanto parado. Uma tarefa reivindicada antes da parada volta para a fila sem iniciar a chamada (`calls_started` inalterado, chamada preparada descartada com `global_stop`), tanto antes quanto depois do preflight do provedor. Quando a recusa ocorre depois do preflight, o tempo gasto nele conta no limite de tempo ativo da tarefa, como nas demais falhas antes do despacho. A autorização final compara a geração; uma tarefa preparada antes de uma parada nunca segue com a geração antiga, mesmo após a retomada.
- **Ferramentas**: antes de `dispatch_started`. O builtin interno verifica o estado. No [recurso POSIX](resource-tool-approvals.md), intenção, aprovação e efeito também fixam a geração; parar/retomar invalida a autorização antiga, sem consumir outra vez nem repetir um resultado desconhecido. Efeito já iniciado conserva seu journal.
- **Provisionamento**: o claim registra a geração vigente e não é criado com o Bees parado. `begin` recusa com `provisioning_global_stop` enquanto parado ou quando o claim é de geração anterior, inclusive depois da retomada. Uma etapa cujo `begin` já foi confirmado segue: guarda online, renovação, recibo e registro de `unknown` continuam aceitos, para que o resultado seja publicado. A sequência para na etapa seguinte. O helper distingue a recusa comprovada sem efeito pela resposta fechada e pelo UUID persistido: a aquisição ou a etapa termina `stopped`, preservando as etapas anteriores. Perda de resposta, erro não reconhecido, timeout e replay em cache continuam incertos e bloqueados. A retomada não repete o UUID recusado nem libera um efeito realmente incerto. Consulte [recusas definitivas](provision-rejection.md). Veja [provisionamento](provisioning.md).

## Retomada

Retomar libera novas operações; nada que foi recusado é reenviado por isso:
- tarefas na fila voltam a ser reivindicadas, com políticas, aprovações e limites de consumo reavaliados. Isso inclui as tarefas devolvidas à fila pela parada; para não executá-las, pause ou cancele cada uma antes de retomar;
- tarefas pausadas, canceladas ou com resultado desconhecido continuam aguardando decisão humana;
- aprovações consumidas não voltam a valer; decisões textuais ainda válidas conservam seu prazo. A aprovação de recurso também depende da geração e do snapshot atuais: parar/retomar pode invalidá-la antes do efeito;
- uma mensagem de chat recusada precisa ser enviada de novo pela pessoa.

## Limites deste checkpoint

- O [scheduler de rotinas](routines.md) deixa de materializar/despachar trabalho novo enquanto parado. Tarefas já criadas usam as mesmas guardas de geração e ferramenta; a reconciliação de resultados conhecidos permanece possível. Agendas antigas continuam preparatórias. VM/apps/máquina pessoal e seus aceites permanecem separados.
- Um novo pedido manual de rotina também é recusado enquanto parado. Consultar o recibo de um pedido já confirmado ou repetir seu UUID para recuperar o mesmo recibo não cria outra tarefa. A parada não converte uma resposta perdida em autorização para uma segunda execução.
- Pedir uma sugestão textual pela conversa passa pela guarda de geração do chat. Ler uma sugestão existente e revisar/salvar uma definição pausada não despacha modelo ou ferramenta; essa definição continua exigindo confirmação própria para execução manual ou ativação programada.
- Pausar uma única abelha, tarefa ou ambiente específico continua nos controles próprios de cada recurso.
- A parada é uma regra do serviço Bees. Não bloqueia processos externos que acessem o banco ou as credenciais diretamente, nem interrompe um efeito remoto já despachado.

## Validação da retomada

`packages/core/tests/test_task_control_consumption.py` cobre parada/retomada e replay
com SQLite e HTTP loopback reais, reabrindo os serviços e o worker. Mantém uma tarefa
pausada com resultado pago, uma pausada antes do despacho, uma cancelada e uma chamada
com resposta HTTP 503 de resultado desconhecido. As linhas de tarefas, execuções,
chamadas, comandos, mensagens, consumo e leases permanecem iguais; o worker não faz
novas requisições. O consumo confirmado e a reserva incerta continuam contando, e a
retomada individual do resultado desconhecido ainda exige reconhecimento humano.

## Novas superfícies (agente, executores, canais, MCP, equipe, webhooks)

- **Comandos de executor** (computador da abelha e conector pessoal): o comando nasce na fila com a geração da parada global. Ao parar, todos os comandos ainda na fila expiram (`global_stop`); no envio, o servidor reconfere parada/geração, tarefa cancelada, abelha inativa, computador pronto, controle humano (ações visuais) e a concessão pessoal atual — qualquer diferença expira o comando sem chegar ao executor. Comandos já enviados aparecem em `in_flight.executor_commands`: a parada não garante que o efeito remoto terminou.
- **Computadores:** durante a parada não é possível criar computador; remover continua permitido.
- **Canais:** nenhuma mensagem sai para Telegram/Discord durante a parada; as respostas acumuladas são entregues após a retomada. Mensagens recebidas durante a parada recebem aviso de que nada foi executado.
- **MCP, equipe e webhooks** passam pelo mesmo início de efeito (`begin`) ou pelo caminho manual de rotinas, ambos recusando parada global.
