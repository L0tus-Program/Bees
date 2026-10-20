# Ciclo limitado de modelo e transformação local

BEES-010.2 acrescenta `model_tool_task`, um modo explícito de tarefa para `normalize_text`. A função propõe `text.normalize@1` no processo POSIX próprio. Não oferece arquivos, rede, terminal, navegador, apps, VM, conector pessoal ou funções hospedadas do provedor. Chat, `create_text_task` e tarefas comuns continuam textuais.

## Preparação pelo operador

API e worker precisam usar o mesmo diretório de dados e schema18. Com identidade e abelha ativas, prepare o recurso local:

```sh
cd /workspace/Bees
.venv/bin/python -m bees_worker.text_resource --agent UUID_DA_ABELHA --data-dir /CAMINHO/PRIVADO/DOS/DADOS
```

O comando é idempotente e recusa diretórios inseguros, links, plataforma incompatível, banco ausente ou schema antigo. Não migra o banco nem inicia modelo, instala extensão, concede uso ou emite convite/credencial de host. Cria identidade de instalação se ainda faltar e journal privado em `text-resources/{agent UUID}`, sob o diretório canônico do banco. Uma instalação parcial é preservada para diagnóstico, sem recriação silenciosa. Esse processo de texto não é uma sandbox para código arbitrário.

Em **Ferramentas**, instale o contrato do catálogo, habilite-o e conceda o uso à abelha separadamente. Configure um modelo com suporte declarado a tool calls. Inicie o worker atual normalmente; seu heartbeat anuncia versão1 do ciclo. Preparar recurso, conceder ferramenta e iniciar worker são condições distintas.

## Uso e criação recuperável

Ao delegar uma tarefa, a opção **Permitir transformação local de texto nesta tarefa** começa desmarcada e aparece somente quando o recurso preparado, a concessão, o modelo e um worker compatível estão disponíveis. Ela permite ao modelo propor remover espaços ou mudar maiúsculas/minúsculas. Não substitui a [revisão própria da ferramenta](resource-tool-approvals.md).

O teto é de 4 gerações, 16 propostas resolvidas/efeitos e 120 segundos ativos, com no máximo 4 propostas em um turno e 64 KiB de resposta normalizada. Argumentos do texto mantêm os limites menores do contrato: 4.096 caracteres/8.192 bytes. A tarefa pode encerrar com progresso parcial antes dos tetos quando o próximo resultado não cabe no contexto ou a autoridade deixa de ser válida. O modo não combina geração de arquivos neste checkpoint.

Antes de criar, a aba conserva o corpo exato do pedido humano e seu UUID em `sessionStorage`, até receber um recibo verificável. O conteúdo é privado desta aba; não inclua credenciais na descrição. Perda de resposta, resposta inválida e expiração da sessão conservam o pedido após reload, sem retry automático. **Verificar o mesmo pedido** repete somente a criação idempotente, sem criar outra tarefa. O recibo vincula UUID, hash e tarefa; pode retornar a tarefa já em espera ou concluída. Descartar o pedido da aba não cancela uma tarefa existente, e a interface explica essa diferença.

## Coordenação e autoridade

A fila padrão escolhe o worker pelo modo canônico da tarefa. O recurso vem do caminho fixo da instalação, com identidade conferida; o modelo não fornece caminho, agente, instalação, plugin, grant, ambiente ou identidade. A seleção do contrato instalado é feita no serviço. A API humana aceita somente o pedido e os limites fechados, sem campos de autoridade.

A chamada do modelo é uma proposta. A composição de ferramentas habilita explicitamente um parser de propostas estruturalmente limitadas; os demais consumidores continuam estritos. Chamadas desconhecidas, argumentos inválidos ou ferramenta desabilitada recebem resultado canônico de rejeição, sem executor. Definições disponíveis são conferidas novamente antes da geração, inclusive depois do preflight Ollama. Instalação, concessão, contexto, política e fencing são reavaliados no preparo e GO do efeito.

Se a política da geração exigir Perguntar, a aprovação corresponde somente àquela geração. A proposta posterior da ferramenta cria outra decisão, com alvo, argumentos, TTL e alcance próprios. Espera humana libera leases; aprovar coloca a tarefa na fila para um dono novo. A ferramenta roda fora da transação, com consumo, contagem e início registrados atomicamente.

Cada proposta usa UUID derivado do ModelCall e tool_call_id. A ação concreta e o resultado de ferramenta têm correlação determinística própria. O histórico registra o bloco assistant/tool_calls e seus toolresults; a próxima geração só começa depois de fechar o bloco. Resultados confirmados projetam somente saída textual validada e ID de ação, sem snapshot, credencial ou autoridade privada. Consultas comuns de tarefa não devolvem argumentos ou saídas privadas do journal.

## Reinício, perdas e limites

Chamadas e efeitos desconhecidos conservam quarentena, sem retry/fallback automático. Revogar durante preflight impede o efeito; revogar após o início não desfaz a operação e pode impedir a publicação de confirmação. Uma geração paga de resposta desconhecida não é repetida pelo worker.

Quando modelo e efeito já estão confirmados no banco, mas faltou gravar somente o toolresult local, a recuperação relê esses registros e reconcilia a projeção sem repetir modelo ou ferramenta. Uma nova geração de continuação permanece sujeita ao orçamento e à política originais. A diferença para um efeito desconhecido é determinada pelo estado canônico, nunca pela alegação do modelo.

Cancelar ou retomar explicitamente fecha propostas antigas ainda pendentes como não iniciadas, confirmadas ou desconhecidas conforme o journal, sem executar nada. Retomar cria outro run, não transfere aprovação e conserva contadores da tarefa. Redirecionamento invalida a decisão anterior. Ao atingir teto, o motivo e o progresso parcial ficam persistidos; uma resposta final válida conclui a tarefa somente após validação normal.

Poda conserva blocos completos. Se o resultado recém-obtido não estiver no contexto preparado, a tarefa pausa com `tool_result_too_large`, sem pagar outra geração que ignoraria esse resultado. IDs históricos continuam impedindo reutilização de tool_call_id depois da poda.

## API, persistência e evidência

- GET `/api/v1/agents/{agent}/tool-task-capabilities`: sondagem somente leitura, sem inicializar recurso.
- POST `/api/v1/agents/{agent}/tool-tasks`: criação explícita com sessão/Origin/CSRF, UUID e recibo. Replay reconcilia mesmo com worker indisponível ou concessão revogada depois.
- Aprovações usam o canal separado `/tool-approvals`; não há endpoint HTTP de execução do efeito.

Não há migração nova. Schema18 já representa os registros e metadata canônica. Backups frios de `data/` conservam os journals locais; exportação lógica não reativa recursos, e restauração permanece inativa, sem grants ou credenciais herdados.

Validação controlada cobre parser OpenAI/Ollama, política da geração separada da ferramenta, concessão revogada no preflight, recursos e argumentos incorretos, limites, cancelamento, reinício, perda de geração/efeito e falha de commit de toolresult. Chromium usa API, worker padrão em processo separado, servidor HTTP local de modelo e processo real da ferramenta: pt-BR/en/es em390/1280, perda de resposta após criação, reload com o mesmo UUID e reinício durante a aprovação. O aceite de uma ferramenta escolhida por modelo remoto real permanece separado; estes testes não fizeram chamadas pagas.
