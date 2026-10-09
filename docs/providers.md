# Modelos substituíveis

O Bees mantém configuração, histórico, memória e tarefas no próprio banco. A conversa envia uma janela recente e memórias selecionadas, descritas em [perfis e memória](memory.md); o histórico canônico completo permanece preservado. Nesta etapa o driver envia texto e declarações de funções para um único endpoint escolhido pelo operador. Receber uma chamada de função não executa ferramenta: permissões, plugins e executores são entregas posteriores.

## Protocolos e capacidades

| Adaptador | Endpoints | Escopo |
| --- | --- | --- |
| `openai_compatible` | `/chat/completions`, `/models` na base configurada | API remota HTTPS ou servidor compatível em loopback. Texto e funções conforme declaração explícita do modelo. |
| `ollama` | `/api/tags`, `/api/show`, `/api/chat` | Ollama em loopback, modelo instalado e local. Capacidades verificadas na resposta do servidor. |

O nome do adaptador descreve um protocolo, não exige assinatura OpenAI. O Bees inclui sugestões de modelos por provedor, sem fornecedor ou modelo obrigatório, fallback, retry automático, redirecionamento HTTP ou uso implícito de proxies do ambiente. A configuração remota aceita a URL base do provedor, incluindo seu prefixo, por exemplo `https://api.openai.com/v1`. HTTP fora de loopback, usuário/senha na URL, query e fragmento são recusados.

Texto e funções formam o contrato inicial. Streaming, imagens, áudio, ferramentas hospedadas pelo fornecedor, Responses API e formatos específicos de outros protocolos ainda não são suportados. Não declarar compatibilidade apenas porque um endpoint é “OpenAI compatible”. O diagnóstico remoto confirma acesso e presença do modelo em `/models`; isso não comprova geração nem suporte às funções. A declaração de funções exige documentação/teste do modelo escolhido.

Ollama precisa ter sido instalado e iniciado pelo operador. Bees não instala serviço, baixa modelos ou liga máquinas. Antes de enviar a conversa, consulta o catálogo e os metadados do modelo; rejeita nomes cloud e metadados de encaminhamento remoto. O operador precisa confiar no daemon: os probes não impedem adulteração nem troca do modelo entre consulta e geração. Para modelo servido em outra máquina, use um endpoint remoto compatível explícito, em HTTPS. O adaptador usa `think: false`; raciocínio separado da resposta ainda não integra este contrato.

Mensagens, chamadas e resultados usam tipos do Bees. Identificadores de chamadas são preservados no histórico; o protocolo Ollama recebe o nome da função associado ao identificador. Argumentos de novas chamadas são validados pelo JSON Schema declarado. Históricos concluídos mantêm chamadas mesmo quando a ferramenta deixou de estar habilitada.

O subconjunto inicial aceita objetos/propriedades, tipos, itens, enumerações e limites simples. Referências, regex, `uniqueItems` e combinadores/condicionais são recusados para limitar a resolução e o custo de validação; estrutura e tamanho dos argumentos também são limitados. Não há confiança na promessa de JSON válido do modelo.

## Configuração e credenciais

### Seleção pela interface

A criação e a edição de uma abelha oferecem listas prontas de provedores e sugestões de modelos. OpenAI, OpenRouter, Gemini e Ollama têm endereços preconfigurados; o usuário não precisa procurar URLs. É possível escolher uma sugestão ou **digitar outro modelo em qualquer provedor**, sem buscar a lista. Conexões personalizadas não presumem quais modelos seu servidor oferece.

**Buscar modelos** é opcional e agrega opções à lista pronta, preservando a seleção atual e os identificadores manuais. Consulta o provedor escolhido com a chave informada ou uma referência existente explicitamente autorizada. A busca não gera respostas, envia histórico, baixa modelos nem salva credenciais. Falhas de catálogo deixam as sugestões e a digitação disponíveis. **Testar conexão** também é opcional: consulta catálogo/capacidades; uma falha nesse teste não impede salvar a configuração.

As sugestões ficam no catálogo versionado do serviço, não dependem de rede e não comprovam acesso da conta, instalação local ou geração. Na descoberta, OpenAI usa um filtro conservador de famílias de conversa, Gemini filtra famílias de texto e OpenRouter usa modalidades anunciadas. Esses filtros não comprovam compatibilidade de geração ou ferramentas; modelos novos ou específicos podem exigir revisão do adaptador. Ollama descobre somente modelos locais anunciados; a verificação de isolamento/capacidades antes da conversa permanece obrigatória. Uma sugestão Ollama precisa ser instalada pelo operador.

As opções **API compatível personalizada** e **Ollama personalizado** preservam servidores adicionais. Nelas, a URL fica explícita. Os mesmos limites de destino e credenciais continuam aplicados. O catálogo não introduz fallback de modelo ou fornecedor.

Rotas autenticadas: `GET /api/v1/providers` devolve conexões e sugestões `models: [{id, name}]`; `POST /api/v1/models/discover` recebe `provider_id`, chave transitória ou referência autorizada e endpoint apenas para conexão personalizada. A resposta contém somente identificadores e nomes dos modelos. A descoberta respeita CSRF, sessão, concorrência e revisão do agente; não devolve valores de credenciais. Endereços de presets não podem ser substituídos nessa rota.

Antes de criar/salvar, a interface chama `POST /api/v1/models/prepare`: valida localmente destino, configuração e disponibilidade da credencial/cofre, sem rede nem gravação de chave. Devolve somente um `validation_token` de uso único por cinco minutos, vinculado à sessão/configuração e à abelha na edição. Isso permite servidores de conversa sem `/models`; não afirma autenticação remota ou suporte do modelo. O salvamento preserva revisão otimista, replay de criação e rollback do cofre. Referências existentes `env:`/`vault:` só podem ser reutilizadas explicitamente na mesma conexão de uma abelha; a ponte interna `env:BEES_REQUEST_KEY` é reservada.

Fontes dos presets e catálogos: [OpenAI — listar modelos](https://developers.openai.com/api/reference/resources/models/methods/list), [Gemini — compatibilidade OpenAI e listagem](https://ai.google.dev/gemini-api/docs/openai#list-models), [OpenRouter — catálogo](https://openrouter.ai/docs/api/api-reference/models/list-all-models-and-their-properties) e [Ollama — modelos locais](https://docs.ollama.com/api/tags).

Sugestões consultadas em 05/10/2026: [OpenAI GPT-5 mini](https://developers.openai.com/api/docs/models/gpt-5-mini), [GPT-5 nano](https://developers.openai.com/api/docs/models/gpt-5-nano), [GPT-4.1](https://developers.openai.com/api/docs/models/gpt-4.1), [GPT-4.1 mini](https://developers.openai.com/api/docs/models/gpt-4.1-mini), [GPT-4o mini](https://developers.openai.com/api/docs/models/gpt-4o-mini); [Gemini 3.8 Flash](https://ai.google.dev/gemini-api/docs/models/gemini-3.8-flash) e [3.5 Flash-Lite](https://ai.google.dev/gemini-api/docs/models/gemini-3.5-flash-lite); OpenRouter [GPT-4.1 mini](https://openrouter.ai/openai/gpt-4.1-mini), [Gemini 2.5 Flash](https://openrouter.ai/google/gemini-2.5-flash), [Claude Sonnet 4](https://openrouter.ai/anthropic/claude-sonnet-4); Ollama [Llama 3.2](https://ollama.com/library/llama3.2) e [Qwen 3](https://ollama.com/library/qwen3). Novas versões podem ser adicionadas por descoberta ou digitação. A disponibilidade no intermediário pode diferir da API direta.

Checkpoint de 05/10/2026: 479 testes Python e 94 testes web aprovados, além de Ruff, ESLint, typecheck/build e build Docker. Os 24 casos sem execução no Windows dependem de Linux/POSIX ou symlink. O fluxo Docker com projeto descartável validou catálogo, descoberta, CSRF, reutilização de credencial, conversa, reinício e recriação. No navegador, uma instalação própria de teste validou criação/edição com seletores, erro de chave, quatro presets, troca de destino limpando credencial, pt-BR/en/es e viewport de 390 px sem overflow horizontal. Respostas e catálogos usaram transporte controlado; não foi usada chave real nem comprovada geração real neste checkpoint.

Checkpoint seguinte, 05/10/2026: 493 testes Python e 116 web aprovados, com os mesmos 24 skips de plataforma, além de Ruff, ESLint e typecheck/build. Build Docker e aceite com volumes descartáveis comprovaram catálogo negado (403), preparação local, modelo manual não listado e conversa sem diagnóstico prévio, mantendo cofre, CSRF, reinício e recriação. No navegador descartável, sugestões apareceram sem chave/busca, catálogo negado preservou criação, modelo manual permitiu criar/conversar e troca de modelo com credencial salva funcionou sem teste remoto. Revisão independente não encontrou bloqueadores. O usuário relatou descoberta OpenAI funcionando após corrigir sua chave; geração real continua sem aceite neste checkpoint. Nenhuma chave real foi utilizada pelos testes.

### Configuração para operadores

Os exemplos em [examples/providers](../examples/providers) são modelos de configuração, não configurações prontas: substitua o campo `model` pelo identificador disponível no seu servidor. Habilite `capabilities.tool_calls` apenas para um modelo que suporte funções.

`secret_ref` aceita `env:NOME_DA_VARIAVEL` ou `vault:UUID` criado pelo cofre do Bees. O valor é resolvido no processo imediatamente antes da requisição e enviado no cabeçalho de autenticação ao endpoint escolhido. Não copie a chave para o JSON, o banco, o chat ou variáveis `VITE_*`. O resolver não lê `.env` automaticamente e não tenta outras credenciais quando uma referência está ausente. A configuração da abelha guarda somente a referência. Erros públicos não reproduzem corpo de resposta, cabeçalho ou entrada de configuração inválida.

O onboarding guarda chaves em arquivos cifrados pelo DPAPI CurrentUser no Windows ou por Fernet com chave externa no Linux. API e CLI usam o mesmo resolver composto, sem fallback silencioso. Variáveis de ambiente exigem provisionamento e proteção pelo operador; exportação de segredos continua pendente. [Primeiro acesso](onboarding.md) descreve configuração e limites do cofre.

## Primeiro fluxo pelo terminal

Execute da raiz após `uv sync --locked`. Prepare uma cópia privada do JSON de exemplo, preferencialmente dentro de `data/` (ignorado pelo Git). Para remoto, provisione a variável referenciada no terminal ou supervisor de forma privada. Ollama local normalmente dispensa chave.

Verificar disponibilidade, sem gerar uma conversa:

```sh
uv run --locked bees-model diagnose --config data/model.json --data-dir data
```

Criar uma abelha e sua primeira conversa:

```sh
uv run --locked bees-model create --name Pesquisadora --config data/model.json
```

O resultado contém `agent_id`, `conversation_id` e `revision`. Usar os dois identificadores na conversa:

```sh
uv run --locked bees-model chat --agent UUID_DA_ABELHA --conversation UUID_DA_CONVERSA --text "Olá, em que você pode ajudar?"
```

Sem `--text`, o comando lê a entrada padrão. O driver envia as instruções da abelha e o histórico dessa conversa ao modelo configurado. Não anexa automaticamente todas as memórias, arquivos ou outras conversas. Recuperação seletiva de memória pertence à próxima entrega de agentes.

Trocar o backend/modelo, fornecendo a revisão atual:

```sh
uv run --locked bees-model configure --agent UUID_DA_ABELHA --expected-revision 1 --config data/outro-modelo.json
```

A troca preserva o estado e recusa incompatibilidades com funções presentes no histórico. A nova configuração é escolha explícita do operador: a próxima conversa poderá enviar o histórico ao novo endpoint. O Bees não migra dados para outro fornecedor para contornar erro. A criação/configuração não realiza geração; use diagnóstico e uma conversa de teste para comprovar o backend. Instâncias em execução conservam o provedor já selecionado; edição concorrente impede gravar uma resposta como se tivesse usado a nova configuração.

`--data-dir` ou `BEES_DATA_DIR` escolhe o mesmo diretório privado da API. A CLI usa o acesso do operador do sistema; a interface web exige sessão e CSRF. Pare o serviço antes de atualizar dependências ou migrar o banco.

## Uso, erros e concorrência

Rejeição remota HTTP 401 é `authentication_failed`; HTTP 403 é `access_denied`, pois pode representar restrição de conta/chave/destino em vez de chave inválida. Erros HTTP do provedor incluem `error.upstream_status` numérico quando disponível. O HTTP local continua usando o contrato da API (por exemplo, 502 para falha remota); nunca reproduz corpo, cabeçalhos ou chave do fornecedor. O código e a mensagem traduzida orientam o usuário, e uma falha de descoberta mantém a seleção disponível.

Tokens são informados quando a resposta do fornecedor contém contadores válidos. O contrato distingue `reported`, `estimated` e `unknown`; não há cálculo de preço. Ausência de contadores é desconhecida, nunca consumo zero. Um valor informado não é garantia do total cobrado. Cada geração deixa um registro de consumo e pode ser bloqueada pelo limite da abelha antes da rede; a reserva usa uma estimativa declarada de entrada, separada do uso informado. Consulte [consumo e limites](budgets.md).

Timeout, falha de conexão, autenticação, modelo ausente, capacidade incompatível, limite de payload e resposta inválida têm diagnóstico próprio. O padrão é 30 segundos por operação HTTP, deadline de 60 segundos para a sessão (incluindo probes), e 1 MiB por requisição/resposta de geração. Catálogos têm limite separado de 4 MiB (`max_catalog_bytes`) e a descoberta aceita no máximo 4096 entradas; isso permite listas maiores sem ampliar o limite de uma resposta de conversa. Esses valores são configuráveis. Respostas comprimidas são recusadas; parsing e validação síncronos têm limites de estrutura/tamanho, mas o deadline assíncrono não preempta CPU. Limites por tarefa e orçamento virão com o executor. Não há repetição automática, inclusive após timeout: o fornecedor pode já ter processado a solicitação.

A entrada do usuário fica preservada quando uma solicitação aceita falha. Não existe fila/retomada automática nesta CLI. Nenhuma transação SQLite permanece aberta durante a rede. Respostas são gravadas somente se agente e conversa continuam na revisão observada e o contexto de memória selecionado permanece o mesmo. Conflito/cancelamento pode descartar uma resposta já gerada, com consumo externo possível; o processo informa falha e não repete.

A biblioteca interna também aceita mensagens de resultado `tool`. Quando uma resposta solicita várias funções, o chamador fornece todos os resultados em um lote; lote incompleto, duplicado ou sem chamada correspondente é recusado antes da gravação/rede. O Bees valida a associação e adapta a ordem para o protocolo de destino. Este contrato não autoriza nem realiza o efeito descrito pela função.

## Validação e limites atuais

Testes usam transportes controlados e servidores HTTP de loopback com mensagens de teste. Eles exercitam serialização, conversa/funções/resultados nos dois protocolos, restrições de destino, erros sem credenciais e preservação de estado. Isso comprova o driver e seu contrato, não a qualidade de um modelo nem equivalência completa com Dots/Grok Bot.

### Modelos reais comprovados

| Modelo | Protocolo | Capacidades declaradas | Conversa | Delegação `create_text_task` | Uso | Evidência |
| --- | --- | --- | --- | --- | --- | --- |
| `gpt-5.4-nano` (OpenAI) | `openai_compatible` (Chat Completions) | `text`, `tool_calls` | Real, persistida e recarregada | Real: o modelo escolheu a função, o Bees confirmou uma tarefa e o worker concluiu | `reported` (entrada/saída/total) | BEES-005.1, 09/10/2026 |
| Backend local (Ollama) | `ollama` | — | Pendente | Pendente | — | BEES-005.2; Ollama não instalado na máquina de validação |

Aceite remoto de 09/10/2026, na instalação Docker padrão do mantenedor, com abelha e credencial de teste no cofre:

- **Com permissão no turno:** o pedido "resuma em três tópicos curtos a história da apicultura no Brasil" gerou `finish_reason: tool_calls`. O serviço confirmou uma única tarefa, com o tema no título, objetivo e resultado esperado, limite de 1 chamada e 120 s. O worker concluiu em uma chamada (cerca de 1,7 s). O resultado reapareceu no chat e na lista de tarefas após recarregar a página. Uma delegação real anterior, de 08/10, também continuou visível após reiniciar os containers.
- **Sem permissão no turno:** o mesmo pedido terminou com `finish_reason: stop`, sem tarefa ou delegação nova. O modelo justificou a recusa com um motivo inventado ("fora do objetivo"), em vez da ausência da função. A garantia vem do serviço, não do texto do modelo.
- **Uso:** as três chamadas registraram uso `reported` do provedor: chat negativo 3.657/81 tokens, chat com delegação 3.959/101 e tarefa 218/125. Nenhum valor foi estimado ou zerado por ausência.
- **Qualidade observada:** formato atendido (três tópicos em português), com conteúdo genérico e sem marcos históricos concretos. A tarefa recebe somente título, objetivo e resultado esperado escritos pelo modelo do chat. Detalhes do pedido que ele não transcrever não chegam ao worker; em um pedido de teste anterior com exemplos concretos, eles foram substituídos por valores genéricos.
- **Não induzidos no provedor real:** argumentos inválidos e timeout ou resposta perdida, para não gerar custo ou estado incerto. Esses casos continuam comprovados por testes de falha controlada: recusa sem criar tarefa, ausência de repetição paga e `unknown` preservado.

Uma execução de um modelo não comprova qualidade geral, outros modelos do mesmo fornecedor ou o backend local. O onboarding integra estes drivers; a comprovação local continua em BEES-005.2.

## Fontes dos protocolos

Consultadas em 04/10/2026:

- [OpenAI Docs — Create chat completion](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create): mensagens, funções, identificadores e consumo.
- [Ollama — Generate a chat message](https://docs.ollama.com/api/chat) e [API oficial no repositório](https://github.com/ollama/ollama/blob/main/docs/api.md): mensagens, função/resultados e contadores.
- [Ollama — OpenAPI](https://github.com/ollama/ollama/blob/main/docs/openapi.yaml): capacidades e metadados de modelos remotos.
- [HTTPX — Environment variables](https://www.python-httpx.org/environment_variables/): proxies implícitos e `trust_env`.
- [jsonschema — Referencing](https://python-jsonschema.readthedocs.io/en/stable/referencing/): resolução de schemas sem consulta remota.

## Interface autenticada

A interface exige sessão e permite criar a abelha e enviar texto sem consulta remota prévia. Ao salvar, prepara localmente a configuração e recebe uma autorização de uso único por cinco minutos, vinculada à sessão/configuração e à abelha na edição. O teste opcional consulta disponibilidade/capacidades e também pode emitir uma confirmação, mas a interface sempre prepara a configuração atual antes de persistir. Geração ocorre apenas ao enviar a conversa; sugestões, preparação local e teste de catálogo não comprovam que o modelo responde corretamente.

Há duas chamadas de modelo simultâneas por processo e uma por conversa na API web. Mensagens são persistidas antes da chamada; falha pode deixar a mensagem enviada sem resposta. Atualize o histórico antes de decidir repetir: timeout não comprova que o fornecedor deixou de processar/cobrar. A aplicação não faz retry automático. Limites aqui não são orçamento de consumo; limites por tarefa serão implementados no executor.
