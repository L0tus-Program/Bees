# Tarefas em segundo plano

Este checkpoint de BEES-007 entrega tarefas **textuais** com o modelo configurado na abelha. O usuário fornece objetivo e resultado esperado, acompanha o estado e consulta a resposta. Navegação, arquivos, aplicativos, ferramentas e rotinas dependem das próximas histórias; uma resposta do modelo não comprova execução dessas ações.

## Uso

Na conversa da abelha, abra **Tarefas**, delegue um objetivo e informe o resultado desejado. A tarefa tem conversa própria: mensagens do chat comum não a redirecionam implicitamente. Use **Redirecionar** para registrar uma nova instrução. O chat permanece disponível, e fechar o navegador não encerra o serviço nem apaga o resultado.

O painel consulta estado por GET a cada cinco segundos enquanto está visível. Consultas, reload e novas sessões não disparam gerações. Se o executor estiver indisponível, a fila permanece no banco; reiniciar o executor permite consumir tarefas ainda não despachadas.

| Controle | Efeito |
| --- | --- |
| Pausar | Persiste a intenção de parar. Uma chamada em andamento pode terminar antes da confirmação da pausa. |
| Retomar | Continua dentro dos limites restantes; aceita explicitamente a configuração atual quando houve mudança. |
| Redirecionar | Registra uma instrução na tarefa. Resposta anterior obsoleta não conclui o novo objetivo. |
| Cancelar | Encerra a tarefa e interrompe a espera local; isso não garante cancelamento ou estorno no provedor. |

Tarefas possuem os estados `queued`, `running`, `waiting_approval`, `waiting_resource`, `paused`, `completed`, `failed` e `cancelled`. Este executor textual não solicita aprovação de ferramentas. O status concluído significa resposta persistida, sem avaliação automática de sua qualidade.

## Limites e falhas

Uma tarefa faz uma geração final por padrão. Novas instruções e retomadas podem consumir chamadas adicionais: os limites padrão são três chamadas e 120 segundos ativos, com máximos de 50 chamadas e 1.800 segundos. Os contadores não reiniciam ao pausar/retomar. Esses limites não equivalem a orçamento financeiro ou limite de tokens; BEES-022 complementará esse controle.

O journal distingue chamada preparada, despacho iniciado, resposta confirmada, falha sem efeito comprovada e resultado desconhecido. Timeout, cancelamento ou queda após iniciar o despacho podem deixar consumo no provedor sem resposta registrada. **Nenhuma chamada desconhecida é repetida automaticamente.** Uma nova tentativa exige decisão explícita, com aviso sobre possível consumo adicional; o registro anterior continua desconhecido.

Leases e gerações persistentes coordenam um despacho observado por vez. Uma lease expirada não prova que a requisição antiga parou no provedor; o fencing impede publicação de um executor antigo. A quarentena é da tarefa, permitindo outras tarefas textuais sem afirmar exclusividade da execução remota. A coordenação de telas e ambientes será acrescentada com os respectivos executores.

O destino/configuração do modelo é registrado por referência, sem valor da chave. Mudanças de perfil, modelo, contexto ou memória invalidam respostas antigas quando necessário e exigem retomada explícita. Não há troca automática de fornecedor para contornar falhas. Credenciais são resolvidas pelo cofre existente fora do contexto do modelo.

Uma resposta concluída enquanto a tarefa está pausada pode ser aproveitada ao retomar, sem outra chamada, desde que agente, memória e histórico continuem válidos. Se mudaram, a retomada usa uma nova execução e preserva a proveniência da anterior. A interface apresenta o modelo e destino atuais antes de confirmar a retomada.

## Operação

O Compose sobe API/interface e `worker` como processos separados, com o mesmo estado e cofre. Ambos usam usuário restrito, filesystem somente leitura e volumes explícitos. O worker compartilha o namespace de rede da API para preservar o endereço loopback de Ollama. Seu healthcheck verifica um heartbeat derivado; não afirma que tarefas concluíram.

Na instalação nativa, inicie a API primeiro e depois, com as mesmas variáveis/configuração:

```sh
uv run --locked bees-worker
```

O worker exige schema atual e cofre existente: não migra a base nem provisiona uma nova chave. Para atualizar, **pare API e worker antes da migração**; preserve os volumes. O launcher Windows compila antes de interromper a instalação e para ambos quando a imagem muda. Uma tarefa interrompida em rede pode exigir a decisão descrita acima.

## Contratos

Os endpoints autenticados `/api/v1/agents/{id}/tasks` e `/{task_id}` observam a fila. POST na coleção cria uma tarefa; POST em `/{task_id}/control` registra um comando. Escritas exigem sessão, origem e CSRF. Criação e controles usam UUID `client_request_id`; reutilizar o ID com outro conteúdo é conflito. Controles também exigem revisão esperada. Uma resposta perdida pode ser reconciliada sem criar outra tarefa ou repetir instruções.

SQLite é a fonte de estado: tarefas, comandos, execuções, chamadas e leases são persistidos na migração 0003. Transações terminam antes da rede. Confirmação do journal, mensagem e resultado ocorrem na mesma unidade de trabalho, após validar geração da lease e snapshot do contexto. Consultas públicas não expõem request/configuração privada, referências de credencial ou erros brutos do provedor.

Conversas de tarefas não aceitam geração pelo endpoint de chat comum ou pela CLI de chat: somente o worker pode consumir essa fila, mantendo controles, orçamento e journal.

Os cenários de cancelamento, concorrência e replay usam a curadoria OD-01 como referência; a implementação não incorpora o executor em memória do Open Dots. Validações usam provedores e contas descartáveis, sem consumir créditos reais. Aceite com modelos reais remoto/local continua separado em BEES-005.

## Evidência do checkpoint — 05/10/2026

A suíte Windows aprovou 535 casos Python, com 24 skips de plataforma; quatro novos casos do launcher também passaram após verificar atualização com API parada e `NoBuild` (539 casos cobertos no conjunto). A interface aprovou 139 testes, lint e build. O alvo Linux `verification` aprovou 128 casos, com quatro skips Windows. Ruff e formatação passaram.

O aceite Docker descartável confirmou pausa/retomada da fila, geração pelo processo worker, chat separado e resultado/cofre preservados após reinício e recriação. Pela interface, delegação e resposta controlada reapareceram após reload. Testes negativos incluem interrupção real de subprocesso antes/depois do despacho, efeito aceito sem resposta, dois workers, fencing antigo, comandos repetidos, cancelamento, redirecionamento, snapshot alterado, limites e rollback da confirmação do journal.

BEES-007 permanece em andamento para integrar esperas de aprovação/recurso e coordenação de ambientes reais nas próximas histórias. Este checkpoint não demonstra ferramentas, computadores, orçamento financeiro ou geração com um modelo real.
