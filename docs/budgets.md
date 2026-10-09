# Consumo e limites

Primeiro checkpoint de BEES-022.1 em `packages/core/src/bees_core/budgets.py`. Toda geração do chat, da delegação, da CLI e do worker deixa um registro canônico de consumo, e cada abelha pode ter um limite de tokens. O limite é local. **Não é teto da fatura do fornecedor** e não interrompe uma resposta já em andamento.

## Registro de consumo

`usage_entries` (migração 0010) guarda:
- abelha, origem (`chat` ou `task`), conversa, chamada do journal (tarefas), provedor e modelo;
- a reserva;
- a liquidação.

O registro é criado no **mesmo commit da autorização imediatamente antes da rede**, junto da política e, no worker, do contador da tarefa. Nenhum registro existe para gerações que não chegaram à rede.

| Estado | Quando | Quanto conta |
| --- | --- | --- |
| `reserved` | Autorizado, sem liquidação (em voo, ou processo morto antes de liquidar) | A reserva |
| `confirmed` | Resposta recebida, inclusive quando depois descartada por conflito de estado ou contrato | Total informado completo e positivo; a reserva se o uso estiver ausente, parcial, zerado, acima de 10¹² ou `unknown` |
| `unknown` | Timeout de leitura, conexão cortada, 5xx, 408/409/425/499, resposta inválida, cancelamento após o envio ou dono expirado | A reserva |
| `released` | Recusa HTTP 400/401/403/404/413/422/429, redirecionamento recusado ou conexão não estabelecida (nenhum byte enviado) | Zero |

A liquidação é final: um gatilho impede alterá-la e as linhas não podem ser apagadas. Uso ausente, zerado ou fora de escala nunca vira zero. Se a própria liquidação falhar, a resposta paga é preservada e o registro continua `reserved`, contando a reserva.

No worker, uma recusa HTTP explícita libera a reserva no consumo, mas o journal da chamada continua `outcome_unknown`. O journal protege contra repetição de efeito; o consumo registra a evidência de cobrança. Quando a resposta se perde, o Bees não repete a chamada para descobrir o consumo.

**Reserva:**
- **Entrada:** estimativa declarada (`chars_div_3_v1`), igual ao tamanho do pedido serializado dividido por 3. Para textos latinos comuns ela superestima, já que ficam perto de 4 caracteres por token; outras escritas (como CJK) podem ser subestimadas.
- **Saída:** soma-se uma folga de saída (`output_allowance`, padrão 4096).
- **Natureza:** é uma estimativa, não contagem do fornecedor. A folga é só contábil: nenhum teto de saída (`max_tokens`) é enviado ao provedor. Uma resposta maior que a folga é contada pelo total informado e pode ultrapassar o limite; a próxima geração é que fica bloqueada.

## Limite por abelha

`budget_limits` guarda, por abelha:
- `token_limit`;
- uma janela móvel (`window_seconds`, padrão 24 h; usa janela, não dia do calendário, para não depender de fuso);
- a folga de saída;
- o estado `active` ou `disabled`.

O limite precisa comportar ao menos uma geração (`token_limit` maior que `output_allowance`).

O consumo é verificado duas vezes:
- **Pré-checagem:** junto da política, antes de gravar a mensagem do usuário ou de pedir decisão humana no worker.
- **Autorização:** imediatamente antes da rede, quando a reserva é gravada. A soma da janela é agregada em SQL.

Se o consumo contado mais a nova reserva ultrapassar o limite, a geração é recusada com `budget_exhausted` e **nenhuma geração é solicitada ao provedor** (o Ollama ainda faz seu preflight de catálogo):
- **Chat:** a API responde `409` e a mensagem não entra no histórico. Uma escrita concorrente que exceda o timeout do SQLite na autorização responde `state_conflict`, sem geração.
- **Worker:** a tarefa pausa com atenção (`budget_exhausted`), sem incrementar chamadas nem despachar. Retomar exige revisar o limite ou esperar a janela.

As reservas de chat e worker usam transações de escrita serializadas no SQLite, então duas gerações simultâneas não compartilham a mesma folga. Desativar o limite é escolha explícita; o histórico continua contando.

## API

Rotas com sessão; edição com Origin, CSRF e CAS:

- `GET /api/v1/agents/{agent_id}/budget`: limite atual, consumo contado na janela, uso informado, tokens restantes, reservas abertas, registros incertos e quantidade de registros.
- `PUT /api/v1/agents/{agent_id}/budget`: `token_limit`, `window_seconds`, `output_allowance` e `status`. Sem `expected_revision` cria o primeiro limite; editar exige a revisão lida. Revisão divergente retorna `409`.

A interface de orçamento e atividade é a BEES-022.4.

## Limites deste checkpoint

- Não há limite de ferramentas, recursos, disco ou CPU (dependem de executores reais).
- Não há valor monetário: preços não são consultados.
- Não há orçamento global da instalação.
- Um processo morto depois de reservar deixa o registro `reserved`, contado até sair da janela. A recuperação do worker o converte em `unknown`; o chat não tem recuperação própria.
- A estimativa de entrada é aproximada e independe do tokenizador do modelo.

## Validação

Testes com SQLite e HTTP de teste (`packages/core/tests/test_budgets.py` e `apps/api/tests/test_budgets_api.py`) cobrem:
- reserva antes da rede e uso informado;
- uso zerado ou fora de escala contando a reserva, com a resposta paga preservada no chat e no worker;
- envio bloqueado sem mensagem órfã; pré-checagem antes de pedir decisão no worker;
- lista explícita de liberação, conexão não estabelecida e escrita concorrente;
- agregação SQL igual à regra por registro;
- uso parcial fora do total informado;
- uso ausente que não vira zero;
- bloqueio sem requisição;
- liberação somente em 4xx; 5xx, timeout e resposta inválida como `unknown`;
- reserva incerta contando no limite;
- resposta descartada que ainda registra consumo;
- duas reservas concorrentes;
- liquidação final e linhas append-only;
- CAS do limite;
- vínculo com o journal do worker;
- pausa do worker sem despacho;
- morte real do worker após aceite, com recuperação para `unknown`;
- upgrade 9→10;
- API com sessão, CSRF, CAS, validação e 409 sem chegar ao provedor.
