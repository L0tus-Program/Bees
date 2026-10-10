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

Conteúdo de conversas, sites, documentos ou modelos não chega a esse canal. Não existe parada ou retomada automática.

## Onde a parada é verificada

A verificação ocorre antes de cada efeito novo e dentro da mesma transação que o autorizaria:

- **Chat**: antes de gravar a mensagem, junto da política e do limite de consumo, e de novo na autorização imediatamente antes da rede. A segunda verificação também compara a geração: parar e retomar durante a preparação de um chat ainda recusa esse envio. Nesse caso a mensagem já gravada fica sem resposta, como numa revogação de política no mesmo intervalo.
- **Worker**: não reivindica tarefas enquanto parado. Uma tarefa reivindicada antes da parada volta para a fila sem iniciar a chamada (`calls_started` inalterado, chamada preparada descartada com `global_stop`), tanto antes quanto depois do preflight do provedor. Quando a recusa ocorre depois do preflight, o tempo gasto nele conta no limite de tempo ativo da tarefa, como nas demais falhas antes do despacho. A autorização final compara a geração; uma tarefa preparada antes de uma parada nunca segue com a geração antiga, mesmo após a retomada.
- **Ferramentas**: antes de `dispatch_started`. A ação continua `ready`, sem efeito, e pode ser despachada depois da retomada. Aqui a verificação compara só o estado, não a geração; a única ferramenta atual é interna e sem efeito externo. A cerca por geração deve acompanhar os executores reais.
- **Provisionamento**: o claim registra a geração vigente e não é criado com o Bees parado. `begin` recusa com `provisioning_global_stop` enquanto parado ou quando o claim é de geração anterior, inclusive depois da retomada. Uma etapa cujo `begin` já foi confirmado segue: guarda online, renovação, recibo e registro de `unknown` continuam aceitos, para que o resultado seja publicado. A sequência para na etapa seguinte. Hoje o supervisor do host trata essa recusa como qualquer recusa da autoridade e deixa a sequência em quarentena local, que exige recuperação humana ainda não implementada; o mesmo acontece se o claim for recusado durante a aquisição. A distinção entre recusa definitiva e incerteza no helper é a [BEES-011.8](https://github.com/L0tus-Program/Bees/issues/124). A retomada não libera essa quarentena. Veja [provisionamento](provisioning.md).

## Retomada

Retomar libera novas operações; nada que foi recusado é reenviado por isso:
- tarefas na fila voltam a ser reivindicadas, com políticas, aprovações e limites de consumo reavaliados. Isso inclui as tarefas devolvidas à fila pela parada; para não executá-las, pause ou cancele cada uma antes de retomar;
- tarefas pausadas, canceladas ou com resultado desconhecido continuam aguardando decisão humana;
- aprovações consumidas não voltam a valer; uma aprovação concedida e ainda não consumida continua válida dentro do seu prazo de 15 minutos;
- uma mensagem de chat recusada precisa ser enviada de novo pela pessoa.

## Limites deste checkpoint

- Rotinas e ambientes reais ainda não existem; quando forem implementados, precisam da mesma verificação antes de cada efeito.
- Pausar uma única abelha, tarefa ou ambiente específico continua nos controles próprios de cada recurso.
- A parada é uma regra do serviço Bees. Não bloqueia processos externos que acessem o banco ou as credenciais diretamente, nem interrompe um efeito remoto já despachado.
