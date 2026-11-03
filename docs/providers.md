# Modelos substituíveis

O Bees mantém configuração, histórico, memória e tarefas no próprio banco. A conversa envia uma janela recente e memórias selecionadas, descritas em [perfis e memória](memory.md); o histórico canônico completo permanece preservado. O driver envia texto, imagens quando a visão estiver configurada e declarações de funções para um único endpoint escolhido pelo operador. Receber uma chamada de função não executa ferramenta: permissões, plugins e executores são entregas posteriores.

## Protocolos e capacidades

| Adaptador | Endpoints | Escopo |
| --- | --- | --- |
| `openai_compatible` | `/chat/completions` ou `/responses`, `/models` na base configurada | API remota HTTPS ou servidor compatível em loopback. Texto, imagens e funções conforme declaração explícita do modelo. |
| `ollama` | `/api/tags`, `/api/show`, `/api/chat` | Ollama em loopback, modelo instalado e local. Capacidades verificadas na resposta do servidor. |

O nome do adaptador descreve um protocolo, não exige assinatura OpenAI. O Bees inclui sugestões de modelos por provedor, sem fornecedor ou modelo obrigatório, fallback, retry automático, redirecionamento HTTP ou uso implícito de proxies do ambiente. A configuração remota aceita a URL base do provedor, incluindo seu prefixo, por exemplo `https://api.openai.com/v1`. HTTP fora de loopback, usuário/senha na URL, query e fragmento são recusados.

Texto, imagens PNG/JPEG e funções compõem o contrato. Streaming, áudio, ferramentas hospedadas pelo fornecedor, Responses API e formatos específicos de outros protocolos ainda não são suportados. Não declarar compatibilidade apenas porque um endpoint é “OpenAI compatible”. O diagnóstico remoto confirma acesso e presença do modelo em `/models`; isso não comprova geração, visão ou suporte às funções. A declaração de capacidades exige documentação/teste do modelo escolhido.

### Imagens e OCR local — #221

Em Modelo → Opções avançadas, habilite **Este modelo aceita imagens** somente para um modelo com visão. A configuração usa `capabilities.vision` (padrão falso). OpenAI compatível recebe blocos `image_url` com data URLs; Ollama recebe `images` em base64 e precisa anunciar `vision` no preflight local. Somente os bytes da versão selecionada são enviados ao destino configurado; não há URL externa de imagem, fornecedor alternativo ou repetição automática após recusa.

Sem visão, o Bees tenta OCR local antes de aceitar o pedido. A imagem permanece no Bees; somente o texto extraído é enviado, identificado como OCR em inglês, com referência/hash e aviso de erros e perda de informação visual. Isso não descreve objetos, cores ou layout. OCR indisponível, vazio ou com falha produz erro explícito antes de qualquer geração. A instalação Docker inclui Tesseract e dados de inglês. Na instalação nativa, instalar Tesseract com o idioma `eng` e colocá-lo no PATH do serviço é uma escolha explícita do operador; o Bees não instala nem baixa nada em tempo de uso. Cada extração tem prazo de 15 segundos e limite de 64 KiB de texto, em arquivos temporários privados removidos ao terminar.

O chat aceita até quatro anexos, com limites separados por turno de 64 KiB de texto e 512 KiB de PNG/JPEG. Cada imagem precisa ser estática, decodificável, corresponder ao MIME informado e ter no máximo 8 milhões de pixels. Pillow verifica o formato e a decodificação; o servidor não confia na extensão. Esses limites preservam o teto padrão de 1 MiB da requisição ao modelo. Contextos ou ferramentas maiores ainda podem exceder esse teto e são recusados antes da rede. A reserva de consumo permanece conservadora: inclui a representação serializada das imagens e não afirma contagem real de tokens visuais.

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

Antes de criar/salvar, a interface chama `POST /api/v1/models/prepare`: valida localmente destino, configuração e disponibilidade da credencial/cofre, sem rede nem gravação de chave. Devolve somente um `validation_token` de uso único por cinco minutos, vinculado à sessão/configuração e à abelha na edição. Isso permite servidores de conversa sem `/models`; não afirma autenticação remota ou suporte do modelo. O salvamento preserva revisão otimista, replay de criação e rollback do cofre. Referências existentes `env:`/`vault:` no campo bruto `secret_ref` continuam limitadas à mesma conexão da abelha. Chaves do cofre também podem ser escolhidas pelo catálogo da conta via `credential_id`, descrito abaixo; a ponte interna `env:BEES_REQUEST_KEY` é reservada.

### Preparar modelo local pela interface

Ao escolher **Ollama** na criação ou edição da abelha, o painel **Modelo local** mostra se o Ollama responde, os modelos instalados e sugestões com tamanho aproximado, memória recomendada e origem. Nada é baixado sem confirmação: o diálogo informa modelo, tamanho, origem, destino (Ollama em loopback) e que o download usa rede e disco. O progresso aparece em tempo real e pode ser cancelado; ao concluir, selecione o modelo e teste a conexão (o preflight Ollama continua obrigatório).

Na instalação Docker, ative o Ollama com `docker compose --profile local-model up -d`; os arquivos ficam no volume `ollama-models`. Falhas são classificadas: Ollama inacessível, biblioteca inacessível (rede/DNS/proxy do servidor), modelo inexistente, falta de espaço ou interrupção. Se o Bees reiniciar durante o download, repita a ação: o Ollama retoma os blobs parciais. Remover um modelo exige confirmação e é recusado durante download do mesmo modelo.

API: `GET /api/v1/local-models`, `POST /api/v1/local-models/pulls` (`confirm_download: true`), `GET /api/v1/local-models/pulls/{id}`, `POST /api/v1/local-models/pulls/{id}/cancel`, `POST /api/v1/local-models/delete` (`confirm_delete: true`). Somente endpoints loopback.

Ao editar o endereço do Ollama, o painel começa uma consulta para aquele destino
e descarta a lista, o progresso e os avisos do endereço anterior. Respostas
atrasadas e confirmações de uma tela encerrada não podem iniciar uma operação ou
preencher o painel atual. Enquanto uma confirmação/operação está pendente, os
controles do painel ficam indisponíveis para evitar pedidos concorrentes.
Sair da tela ou trocar o endereço interrompe o acompanhamento no navegador;
isso não desfaz um download que o servidor já iniciou. Ao voltar ao mesmo
endereço, o painel consulta os downloads em andamento. **Cancelar download**
continua sendo a ação explícita para interromper o download no servidor.

Fontes dos presets e catálogos: [OpenAI — listar modelos](https://developers.openai.com/api/reference/resources/models/methods/list), [Gemini — compatibilidade OpenAI e listagem](https://ai.google.dev/gemini-api/docs/openai#list-models), [OpenRouter — catálogo](https://openrouter.ai/docs/api/api-reference/models/list-all-models-and-their-properties) e [Ollama — modelos locais](https://docs.ollama.com/api/tags).

Sugestões consultadas em 05/10/2026: [OpenAI GPT-5 mini](https://developers.openai.com/api/docs/models/gpt-5-mini), [GPT-5 nano](https://developers.openai.com/api/docs/models/gpt-5-nano), [GPT-4.1](https://developers.openai.com/api/docs/models/gpt-4.1), [GPT-4.1 mini](https://developers.openai.com/api/docs/models/gpt-4.1-mini), [GPT-4o mini](https://developers.openai.com/api/docs/models/gpt-4o-mini); [Gemini 3.8 Flash](https://ai.google.dev/gemini-api/docs/models/gemini-3.8-flash) e [3.5 Flash-Lite](https://ai.google.dev/gemini-api/docs/models/gemini-3.5-flash-lite); OpenRouter [GPT-4.1 mini](https://openrouter.ai/openai/gpt-4.1-mini), [Gemini 2.5 Flash](https://openrouter.ai/google/gemini-2.5-flash), [Claude Sonnet 4](https://openrouter.ai/anthropic/claude-sonnet-4); Ollama [Llama 3.2](https://ollama.com/library/llama3.2) e [Qwen 3](https://ollama.com/library/qwen3). Novas versões podem ser adicionadas por descoberta ou digitação. A disponibilidade no intermediário pode diferir da API direta.

Checkpoint de 05/10/2026: 479 testes Python e 94 testes web aprovados, além de Ruff, ESLint, typecheck/build e build Docker. Os 24 casos sem execução no Windows dependem de Linux/POSIX ou symlink. O fluxo Docker com projeto descartável validou catálogo, descoberta, CSRF, reutilização de credencial, conversa, reinício e recriação. No navegador, uma instalação própria de teste validou criação/edição com seletores, erro de chave, quatro presets, troca de destino limpando credencial, pt-BR/en/es e viewport de 390 px sem overflow horizontal. Respostas e catálogos usaram transporte controlado; não foi usada chave real nem comprovada geração real neste checkpoint.

Checkpoint seguinte, 05/10/2026: 493 testes Python e 116 web aprovados, com os mesmos 24 skips de plataforma, além de Ruff, ESLint e typecheck/build. Build Docker e aceite com volumes descartáveis comprovaram catálogo negado (403), preparação local, modelo manual não listado e conversa sem diagnóstico prévio, mantendo cofre, CSRF, reinício e recriação. No navegador descartável, sugestões apareceram sem chave/busca, catálogo negado preservou criação, modelo manual permitiu criar/conversar e troca de modelo com credencial salva funcionou sem teste remoto. Revisão independente não encontrou bloqueadores. O usuário relatou descoberta OpenAI funcionando após corrigir sua chave; geração real continua sem aceite neste checkpoint. Nenhuma chave real foi utilizada pelos testes.

### Configuração para operadores

Os exemplos em [examples/providers](../examples/providers) são modelos de configuração, não configurações prontas: substitua o campo `model` pelo identificador disponível no seu servidor. Habilite `capabilities.tool_calls` apenas para um modelo que suporte funções.

`secret_ref` aceita `env:NOME_DA_VARIAVEL` ou `vault:UUID` criado pelo cofre do Bees. O valor é resolvido no processo imediatamente antes da requisição e enviado no cabeçalho de autenticação ao endpoint escolhido. Não copie a chave para o JSON, o banco, o chat ou variáveis `VITE_*`. O resolver não lê `.env` automaticamente e não tenta outras credenciais quando uma referência está ausente. A configuração da abelha guarda somente a referência. Erros públicos não reproduzem corpo de resposta, cabeçalho ou entrada de configuração inválida.

O onboarding guarda chaves em arquivos cifrados pelo DPAPI CurrentUser no Windows ou por Fernet com chave externa no Linux. API e CLI usam o mesmo resolver composto, sem fallback silencioso. Variáveis de ambiente exigem provisionamento e proteção pelo operador; exportação de segredos continua pendente. [Primeiro acesso](onboarding.md) descreve configuração e limites do cofre.

## Credenciais da conta

Na criação ou edição, escolha **Credencial da sua conta Bees** para reutilizar uma chave salva sem digitá-la novamente. Chaves novas são registradas no catálogo da conta; a migração 0025 adota as referências `vault:` que as abelhas existentes já usam. Trocar o modelo de uma abelha não remove sua chave do catálogo. O valor permanece cifrado no cofre.

`GET /api/v1/model-credentials` exige sessão e retorna apenas `id`, `kind` e `endpoint`. `credential_id` é aceito na descoberta, preparação, diagnóstico e salvamento. Não pode ser combinado com uma chave digitada ou `secret_ref`. O servidor exige o mesmo tipo e endpoint normalizado; trocar o destino limpa a seleção na interface. O comprovante de preparação inclui a escolha da credencial. Referências `env:` continuam sob controle do operador e não entram nesse catálogo.

O catálogo fica em `identity_users.llm_credentials_json`, fora das exportações comuns e da restauração portátil. Nenhum valor de chave entra no SQLite. O catálogo preserva as referências após trocas de configuração; gerenciamento de nomes e remoção de chaves não fazem parte deste fluxo.

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

No modo agente, a causa registrada aparece no turno e nos detalhes da tarefa, inclusive quando a chamada tem resultado desconhecido. O diagnóstico identifica código, modelo/provedor usados e, quando disponíveis, HTTP, nome do campo recusado e mensagem do provedor. A mensagem aparece diretamente no turno; os demais dados ficam em Detalhes do erro. Falhas imediatas de teste/envio também mostram o diagnóstico. Registros anteriores usam a causa do journal já existente; campos nunca registrados não são reconstruídos.

A lista de Tarefas e a Atividade lateral também mostram um resumo da causa. Respostas inválidas mantêm `code: invalid_response` e podem registrar `response_issue`, um motivo fechado: JSON/tipo de conteúdo, estrutura da resposta, texto ausente, chamada/argumentos de ferramenta, schema, identificador repetido, término ou consumo inválido. O diagnóstico não reproduz valores ou exceções de validação. Uma resposta HTTP de sucesso com envelope `error` é reconhecida e sua mensagem passa pela mesma sanitização das recusas HTTP; isso não comprova ausência de cobrança e a geração continua incerta. Falhas no preflight do Ollama conservam o motivo sem criar uma geração desconhecida. Registros antigos sem motivo específico informam essa ausência.

Recusas HTTP têm leitura limitada a 8 KB. Para HTTP 400/422, o Bees classifica `tool_schema_rejected`, `model_tools_unsupported`, `unsupported_model_parameter` e `context_window_exceeded`. Somente códigos e nomes de campos conhecidos participam da classificação. O campo de mensagem é limitado a 1.000 caracteres e normalizado, com remoção da credencial usada, texto integral refletido da requisição, URLs e padrões conhecidos de tokens. Somente esse trecho diagnóstico é persistido e exibido como texto inerte; o corpo completo e cabeçalhos continuam privados. Mensagens ausentes, JSON inválido, grande ou comprimido mantêm o código público e HTTP, sem inventar uma causa. Não se deve copiar diagnósticos para locais públicos sem revisar possíveis dados específicos devolvidos pelo provedor. O envio de funções declara `strict: false`, coerente com os campos opcionais do contrato Bees; a validação local e as autorizações continuam obrigatórias. Isso não comprova o aceite de um modelo específico. O relato da #218 confirmou que o provedor recusa ferramentas com raciocínio no GPT-5.6 em Chat Completions. O suporte a Responses descrito abaixo corrige o protocolo; o aceite real na instalação relatada continua dependendo do reteste.

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


## Seleção de API e GPT-5.6

`ProviderConfig.api` aceita `auto` (padrão, omitido ao serializar), `chat_completions` e `responses`. A seleção é local, antes do despacho: `auto` usa Responses para identificadores exatos da família `gpt-5.6` (`gpt-5.6`, `gpt-5.6-terra`, variantes e snapshots com sufixo separado por hífen); outros modelos continuam em Chat Completions. Não se infere suporte de identificadores de terceiros/namespaces. Em **Modelo → Opções avançadas → API do modelo**, é possível selecionar explicitamente o protocolo exigido por um endpoint compatível. A opção é reiniciada ao trocar provedor/endereço. Ollama continua usando seu protocolo nativo e recusa essa seleção de API.

Responses envia `input`, funções sem o wrapper `function`, resultados correlacionados por `call_id` e imagens `input_image`. O adaptador conserva o contrato interno de texto/ferramentas/consumo do Bees e mantém `strict: false`. Não configura `reasoning_effort: none`: o raciocínio do modelo permanece disponível. O Bees usa `store: false` e pede `reasoning.encrypted_content`; não depende de `previous_response_id` nem de armazenamento da conversa no fornecedor.

A mensagem normalizada conserva uma projeção tipada dos itens reapresentáveis (texto, recusa, chamadas e raciocínio cifrado), na ordem recebida e com a fase da resposta. Resumos de raciocínio e campos desconhecidos não são publicados ou reapresentados. O estado opaco acompanha a mensagem e o journal, sobrevive à reinicialização e só volta à mesma conexão/modelo/referência de credencial. Ao trocar destino/modelo, reapresenta-se apenas o histórico normalizado de texto/ferramentas. O estado cifrado não conta como texto na janela de 24 mil caracteres, mas permanece sujeito aos limites de bytes do transporte, journal e orçamento. Não há fallback de API nem repetição automática após erro ou resultado incerto.

Saídas desconhecidas, argumentos inválidos, chamadas duplicadas, raciocínio sem estado cifrado válido e ferramentas incompletas são recusados com causa fechada. Ferramentas nativas hospedadas pelo provedor não são habilitadas; propostas continuam passando pelo coordenador, regras e aprovações do Bees. Contadores de entrada/saída/total são normalizados; ausência de consumo informado permanece desconhecida.

A capacidade de visão dos modelos OpenAI compatíveis é declarada manualmente: **Configurações da abelha → Modelo → Opções avançadas → Este modelo aceita imagens → salvar**. O aviso de OCR indica esse caminho; visão desativada na configuração não comprova que o modelo é textual. Com visão habilitada, PNG/JPEG seguem como imagem tanto em Responses quanto em Chat Completions. A descoberta de capacidades do Ollama permanece independente.

Fontes consultadas em 02/11/2026: [migração para Responses](https://developers.openai.com/api/docs/guides/migrate-to-responses), [raciocínio e estado cifrado](https://developers.openai.com/api/docs/guides/reasoning) e [function calling](https://developers.openai.com/api/docs/guides/function-calling). Os testes de protocolo e Chromium usam servidor HTTP controlado; o reteste do modelo real fica registrado na #218.
