# Atividade e consumo

BEES-022.4 reúne consumo, histórico operacional e caminhos para os controles humanos
na vista **Consumo** de cada abelha. A projeção usa o estado canônico do Bees; consultar
ou recarregar não gera respostas, retoma tarefas, resolve avisos nem repete efeitos.

## O que é observado

O resumo separa tokens informados, estimados, reservados e incertos. Valores ausentes
não viram zero. A unidade é **tokens**; não há conversão para dinheiro, CPU, disco ou
tempo de máquina. A reserva usa a fonte declarada `chars_div_3_v1`, com a folga de
saída descrita em [consumo e limites](budgets.md).

O intervalo delimita o consumo e os eventos exibidos. As contagens de operações em
voo, resultados desconhecidos e esperas consultam **todo o estado relevante** do
escopo: não dependem da página nem do intervalo. Uma operação antiga ainda pode exigir
atenção. Uma reserva aberta comprova autorização sem liquidação; não comprova que o
processo ou a conexão continuam vivos. Cancelar ou parar pode ser seguido pelo registro
de consumo de um envio já autorizado.

A timeline liga eventos à tarefa, execução e ação correspondentes. Exibe horários com
fuso, transições, decisões e códigos de falha públicos. O evento de reserva mostra a
reserva e mantém ausentes as contagens ainda não informadas. O evento final mostra a
liquidação imutável; o resumo agrega o estado observado atual. Os resultados completos permanecem no
detalhe da tarefa. Conteúdo de pedidos, respostas, parâmetros, credenciais e identificação
privada do executor não fazem parte desta consulta.

Uma tarefa textual aparece como execução textual **sem computador associado**. A
configuração de um modelo não comprova uma conexão viva. Não se infere VM pronta,
sessão de navegador ou recurso pessoal a partir desses registros.

## Consulta autenticada

`GET /api/v1/agents/{agent_id}/activity` exige sessão humana. Parâmetros:

| Parâmetro | Regra |
| --- | --- |
| `task_id` | Filtra uma tarefa pertencente à abelha. |
| `run_id` | Exige `task_id` e execução pertencente à tarefa. |
| `since`, `until` | Datas com fuso; intervalo passado, crescente, de até 31 dias. O padrão é 24 horas. |
| `window_seconds` | Janela calculada pelo relógio do serviço, sem combinar com `since`/`until`. A interface usa 24 horas, 7 ou 30 dias. |
| `limit` | De 1 a 100; padrão 50. |
| `before`, `snapshot_seq` | Cursor exclusivo e sequência do snapshot para continuar a consulta. |

A resposta fechada contém abelha, escopo, intervalo, unidade, resumo, operações,
eventos e cursor. Campos extras na consulta são recusados. Vínculo incorreto não
devolve dados de outra abelha ou tarefa.

Uma única transação de leitura fixa os dados e `snapshot.event_seq`, a sequência
global de eventos. `snapshot.observed_at` informa a observação em UTC e permite
distinguir novas derivadas temporais sem nova revisão de entidade. A continuação
conserva intervalo e escopo. Se a sequência mudou, responde `409`: atualize a primeira
página em vez de misturar versões. Não há reconstrução histórica das tabelas mutáveis.

Os snapshots de [orçamento](budgets.md) e [parada global](safety.md) seguem a mesma
referência. Uma janela móvel pode mudar mesmo quando a revisão do limite permanece
igual. Esses snapshots são referências de consulta; as guardas de execução continuam
nos serviços canônicos, independentemente de paginação ou UI.

## Resolução humana

O limite pode ser editado com a revisão confirmada. Atenção pendente permite abrir a
tarefa e suas decisões ou editar preferências em [Atenção](attention.md). Silenciar ou
marcar como lido não resolve uma aprovação nem confirma resultado desconhecido.

**Parar tudo** e **Retomar** usam confirmação humana e CAS. O sucesso depende da
resposta canônica, não do clique. Uma resposta perdida preserva o pedido original para
replay. A retomada global não reconhece resultados desconhecidos nem retoma tarefas
pausadas por si só.

Falha, carregamento e resposta obsoleta conservam o último dado confirmado com aviso
de desatualização. Não liberam orçamento nem substituem estado atual por um snapshot
regressivo. A consulta pode ser atualizada conservando o contexto escolhido.

## Limites

Esta entrega usa os serviços existentes de geração textual, limites, parada e atenção.
Rotinas, conexões de ambientes reais, ferramentas externas e limites de recursos
continuam nos respectivos recortes. O painel não antecipa essas capacidades. Não há
migração adicional de schema nem atualização automática da instalação do usuário.

## Validação de 12/10/2026

- `packages/core/tests/test_activity.py` e `apps/api/tests/test_activity_api.py`:
  36 novos casos, com SQLite real, commit concorrente e consulta HTTP loopback.
  Cobrem autenticação, parâmetros fechados, vínculos, cursor, intervalo, eventos
  inválidos, privacidade, histórico com mais de mil execuções e operações fora da janela.
  Cancelamento antes da resposta é sincronizado no servidor HTTP de teste e exige
  o erro canônico `cancelled_after_dispatch`; resposta já confirmada conserva o uso
  informado. O teste não depende de qual lado vence uma corrida de agendamento.
- Suíte Python completa: 2.164 passaram, 38 skips específicos do ambiente Windows.
- Interface: 637 testes, TypeScript, ESLint e build aprovados. Casos negativos
  cobrem snapshot, microssegundos, bindings, abort, reload, campos extras e replay
  do mesmo pedido de parada após perda de resposta.
- GUI em instalação descartável, API real e dados próprios: consulta e paginação,
  recusa `409` após alteração concorrente conservando a página, limite com CAS,
  confirmação de parada/retomada por teclado, contexto após reload e acesso às tarefas.
  pt-BR/en/es e viewport móvel de 390 px conferidos sem rolagem horizontal.

Os dados de QA foram gerados com transporte de modelo próprio de teste. A consulta,
as mutações humanas e a sessão da GUI usaram o backend real; isso não equivale a
teste com um modelo pago ou computador próprio. A revisão de persistência não
encontrou bloqueadores. O endurecimento adicional do writer de consumo ficou em
[BEES-022.5 #128](https://github.com/L0tus-Program/Bees/issues/128), prioridade baixa.
