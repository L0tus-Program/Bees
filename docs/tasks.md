# Tarefas em segundo plano

Este checkpoint de BEES-007 entrega tarefas **textuais** com o modelo configurado na abelha. O usuário fornece objetivo e resultado esperado, acompanha o estado e consulta a resposta. Navegação, arquivos, aplicativos, ferramentas e rotinas dependem das próximas histórias; uma resposta do modelo não comprova execução dessas ações.

## Uso

Na conversa da abelha, abra **Tarefas** e informe o pedido no título, as orientações no objetivo e o resultado desejado. Os três campos são enviados ao modelo. Por exemplo: título “Redigir redação sobre acesso à cultura no Brasil”, objetivo “Argumentar sobre as desigualdades de acesso” e resultado esperado “Redação com introdução, desenvolvimento e conclusão”. O perfil permanente da abelha continua no contexto, mas não substitui o pedido atual.

A tarefa tem conversa própria: mensagens do chat comum não a redirecionam implicitamente. Use **Redirecionar** para registrar uma nova instrução. O chat permanece disponível, e fechar o navegador não encerra o serviço nem apaga o resultado. Ao selecionar uma tarefa concluída, a **Resposta da tarefa** aparece antes das orientações e decisões.

A correção de 07/10/2026 inclui o título nas novas preparações do worker; antes, o título aparecia na interface, mas não no pedido ao modelo. Resultados existentes e snapshots já preparados/aprovados são preservados, sem chamada paga adicional. Uma tarefa concluída com o pedido antigo pode ser delegada novamente pelo usuário, gerando uma nova execução. “Resposta pronta” indica resposta salva; não é uma avaliação automática de qualidade.

Validação da correção: 1.313 testes Python Windows (27 skips), 630 testes Linux de core/API/worker afetados (quatro skips), 413 testes web, lint, tipos, build e Ruff aprovados. Regressões verificam tema presente somente no título, perfil permanente distinto, isolamento entre tarefas/abelhas, redirecionamento e reuso de snapshots. A interface foi conferida em instalação descartável com provedor falso explícito, incluindo resposta antes das decisões e viewport de 390 px sem overflow. A atualização local conservou o hash da resposta anterior e o contador de chamadas; nenhuma chamada real adicional validou a qualidade da nova redação.

O painel consulta estado por GET a cada cinco segundos enquanto está visível. Consultas, reload e novas sessões não disparam gerações. Se o executor estiver indisponível, a fila permanece no banco; reiniciar o executor permite consumir tarefas ainda não despachadas.

## Delegação pela conversa

No chat, marque **Permitir tarefa em segundo plano nesta mensagem** e peça, por exemplo: “Prepare em segundo plano uma redação sobre o acesso à cultura no Brasil, com introdução, desenvolvimento e conclusão”. A abelha pode solicitar uma tarefa textual usando `create_text_task`. A seleção vale somente para o envio atual; texto do modelo, memória ou documento não habilita essa autorização. Sem seleção, o chat continua respondendo normalmente.

A capacidade de chamadas de ferramentas precisa estar declarada no modelo da abelha. A declaração não comprova suporte do provedor: se o modelo não oferecer essa capacidade, a interface mantém a delegação manual em **Tarefas**. Não há troca automática de modelo. A seleção permite delegar, mas não obriga o modelo a escolher a ferramenta.

O serviço aceita no máximo uma tarefa por turno, com uma chamada de geração e 120 segundos ativos. Há limite de cinco tarefas não terminadas por abelha para essa modalidade, incluindo tarefas criadas manualmente. O modelo fornece apenas título, objetivo e resultado esperado; não escolhe orçamento, provedor, permissões ou ambiente. A geração inicial do chat é separada da chamada do worker, e ambas podem consumir créditos. Esses limites não são orçamento financeiro.

O chat mostra um cartão com estado e resposta da tarefa, preservados ao recarregar ou voltar à conversa. A tarefa mantém sua conversa exclusiva e os controles do painel **Tarefas**. Autorizar delegação não substitui as regras de autonomia: o worker reavalia a geração e pode aguardar uma decisão ou ser bloqueado. Nenhuma tarefa textual executa navegador, arquivos ou aplicativos.

Criação da tarefa, vínculo com o pedido, resposta do modelo e resultados de ferramenta são confirmados na mesma transação. Uma falha antes da confirmação não deixa uma tarefa órfã; uma resposta HTTP perdida depois dela é reconciliada pela consulta. A interface envia `client_request_id` por turno: repetir o mesmo identificador e conteúdo devolve a confirmação salva, sem gerar novamente. O mesmo identificador com conteúdo ou autorização diferentes é conflito; uma entrada sem resposta confirmada também recusa repetição, pois o provedor pode ter consumido créditos. Enviar deliberadamente outro pedido é um novo turno e pode criar outra tarefa. Não há repetição automática de chamada paga.

O estado exibido é uma projeção dos registros canônicos. A conclusão do worker não insere mensagens na conversa principal nem invalida uma geração do chat em andamento. Turnos posteriores recebem até quatro resultados concluídos recentes daquela origem, com proveniência e um teto de 8.000 caracteres incluindo a serialização. São dados não confiáveis, separados das instruções; resultados grandes podem ser truncados no contexto do modelo, embora a interface preserve a resposta inteira. Isso permite pedir um resumo ou discutir o resultado no chat sem outra execução da tarefa.

A consulta autenticada `GET /api/v1/agents/{id}/delegations?conversation_id=UUID&offset=0&limit=100` pagina somente delegações daquela abelha e conversa. Consultar não executa modelos ou ferramentas. A API de chat aceita `allow_delegation` booleano estrito, falso por padrão; a CLI e o serviço de provedores genérico continuam sem executor implícito de ferramentas. Chamadas rejeitadas têm resultado explícito no histórico e na interface; uma frase do modelo afirmando que criou uma tarefa não é confirmação do serviço.

O vínculo usa metadados nas entidades existentes do schema 8, sem alterar migrações anteriores. Mensagens permanecem append-only: a composição confirma vínculos, resposta e resultados de ferramenta na mesma unidade de trabalho, antes de criar a mensagem final. A execução não exige uma segunda geração para anunciar o resultado da ferramenta. A interface consulta as 100 delegações mais recentes; registros anteriores continuam preservados e a API permite paginação. Não há criação de rotinas, delegação recursiva ou executor genérico de funções neste checkpoint.

Validação de 07/10/2026: 1.343 testes Windows (27 skips), 923 testes Linux de core/API/worker (12 skips), 72 testes Linux do transporte do host e 433 testes web aprovados, além de Ruff/formatação, lint, tipos e build. Os 30 novos casos Python e 20 web cobrem consentimento, limites, isolamento, CAS/replay, falha após commit, rollback, contexto alterado, políticas, protocolo completo de resultados e resposta persistida. A revisão gráfica descartável comprovou criação pelo chat, atualização da resposta, reload, continuação do chat, pt-BR/en/es e viewport de 390 px sem overflow. A suíte completa revelou EOF sem progresso no servidor de teste do transporte; seu encerramento foi corrigido, preservando o prazo e os controles do produto. A repetição completa passou. Testes e revisão usaram modelos falsos; a qualidade e a escolha da ferramenta pelo modelo real continuam para aceite manual.

Docker local atualizado pelo launcher em `localhost:8080`, com API e worker saudáveis. A comparação de hashes preservou os agentes, conversas, mensagens, tarefas, runs e chamadas anteriores, além do cofre e fingerprint da chave; integridade SQLite e foreign keys válidas. Nenhuma identidade de teste foi criada na instalação padrão. O schema permanece 8 e não há VM criada ou ferramenta externa ativada por esta entrega.

| Controle | Efeito |
| --- | --- |
| Pausar | Persiste a intenção de parar. Uma chamada em andamento pode terminar antes da confirmação da pausa. |
| Retomar | Continua dentro dos limites restantes; aceita explicitamente a configuração atual quando houve mudança. |
| Redirecionar | Registra uma instrução na tarefa. Resposta anterior obsoleta não conclui o novo objetivo. |
| Cancelar | Encerra a tarefa e interrompe a espera local; isso não garante cancelamento ou estorno no provedor. |

Tarefas possuem os estados `queued`, `running`, `waiting_approval`, `waiting_resource`, `paused`, `completed`, `failed` e `cancelled`. Uma regra **Perguntar** para geração deixa a tarefa em `waiting_approval`; uma negativa a pausa. O cartão da tarefa permite autorizar uma vez ou salvar uma regra no escopo explícito; a confirmação válida volta à fila automaticamente. Também é possível revisar a regra em **Autonomia** e depois retomar: retomada não concede permissão pontual nem contorna políticas. Ver [aprovações](approvals.md) e [políticas](policies.md). Este executor textual não solicita aprovação de ferramentas. O status concluído significa resposta persistida, sem avaliação automática de sua qualidade.

## Limites e falhas

Uma tarefa faz uma geração final por padrão. Novas instruções e retomadas podem consumir chamadas adicionais: os limites padrão são três chamadas e 120 segundos ativos, com máximos de 50 chamadas e 1.800 segundos. Os contadores não reiniciam ao pausar/retomar. Esses limites não equivalem a orçamento financeiro ou limite de tokens; BEES-022 complementará esse controle.

O journal distingue chamada preparada, despacho iniciado, resposta confirmada, falha sem efeito comprovada e resultado desconhecido. Timeout, cancelamento ou queda após iniciar o despacho podem deixar consumo no provedor sem resposta registrada. **Nenhuma chamada desconhecida é repetida automaticamente.** Uma nova tentativa exige decisão explícita, com aviso sobre possível consumo adicional; o registro anterior continua desconhecido.

Os controles também consideram o journal de ações de ferramentas internas. Uma ação de resultado desconhecido impede novo despacho, redirecionamento e retomada sem reconhecimento, mesmo quando não há chamada de modelo desconhecida ou o registro está fora da página exibida. O detalhe apresenta um histórico separado de ferramentas, sem argumentos ou resultados privados. Se houver efeitos desconhecidos de modelo e ferramenta, uma decisão concreta cobre o conjunto observado; o histórico anterior permanece preservado. Esse controle não ativa ferramentas nas tarefas textuais nem concede acesso ao computador.

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

SQLite é a fonte de estado: tarefas, comandos, execuções, chamadas e leases são persistidos na migração 0003. Transações terminam antes da rede. O despacho de geração só é registrado depois de validar contexto, controles e políticas atuais, inclusive após o preflight local do Ollama. Falhas desse preflight não representam geração iniciada. Confirmação do journal, mensagem e resultado ocorrem na mesma unidade de trabalho, após validar geração da lease e snapshot do contexto. Consultas públicas não expõem request/configuração privada, referências de credencial ou erros brutos do provedor.

Conversas de tarefas não aceitam geração pelo endpoint de chat comum ou pela CLI de chat: somente o worker pode consumir essa fila, mantendo controles, orçamento e journal.

Os cenários de cancelamento, concorrência e replay usam a curadoria OD-01 como referência; a implementação não incorpora o executor em memória do Open Dots. Validações usam provedores e contas descartáveis, sem consumir créditos reais. Aceite com modelos reais remoto/local continua separado em BEES-005.

## Evidência do checkpoint — 05/10/2026

A suíte Windows aprovou 535 casos Python, com 24 skips de plataforma; quatro novos casos do launcher também passaram após verificar atualização com API parada e `NoBuild` (539 casos cobertos no conjunto). A interface aprovou 139 testes, lint e build. O alvo Linux `verification` aprovou 128 casos, com quatro skips Windows. Ruff e formatação passaram.

O aceite Docker descartável confirmou pausa/retomada da fila, geração pelo processo worker, chat separado e resultado/cofre preservados após reinício e recriação. Pela interface, delegação e resposta controlada reapareceram após reload. Testes negativos incluem interrupção real de subprocesso antes/depois do despacho, efeito aceito sem resposta, dois workers, fencing antigo, comandos repetidos, cancelamento, redirecionamento, snapshot alterado, limites e rollback da confirmação do journal.

BEES-007 permanece em andamento para integrar esperas de aprovação/recurso e coordenação de ambientes reais nas próximas histórias. Este checkpoint não demonstra ferramentas, computadores, orçamento financeiro ou geração com um modelo real.

## Decisões de tarefas — BEES-009

Uma política Perguntar cria um cartão persistente na conversa e no detalhe da tarefa. Permitir uma vez ou salvar autorização no escopo concreto coloca a mesma execução na fila, preservando seu contexto. Sempre perguntar e bloquear salvam regras sem gerar. [Aprovações](approvals.md) descreve validade, efeitos e revogação. Retomar continua sem conceder autorização; uma execução nova não carrega os IDs da aprovação anterior. Recuperação após queda preserva também o journal da ação autorizada.

BEES-007 passa a ter espera e decisão humana para gerações textuais. Recursos e coordenação dos ambientes reais permanecem pendentes; não se trata de execução de ferramentas ou modelo real comprovado.
