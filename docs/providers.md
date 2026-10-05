# Modelos substituíveis

O Bees mantém configuração, histórico, memória e tarefas no próprio banco. A conversa envia uma janela recente e memórias selecionadas, descritas em [perfis e memória](memory.md); o histórico canônico completo permanece preservado. Nesta etapa o driver envia texto e declarações de funções para um único endpoint escolhido pelo operador. Receber uma chamada de função não executa ferramenta: permissões, plugins e executores são entregas posteriores.

## Protocolos e capacidades

| Adaptador | Endpoints | Escopo |
| --- | --- | --- |
| `openai_compatible` | `/chat/completions`, `/models` na base configurada | API remota HTTPS ou servidor compatível em loopback. Texto e funções conforme declaração explícita do modelo. |
| `ollama` | `/api/tags`, `/api/show`, `/api/chat` | Ollama em loopback, modelo instalado e local. Capacidades verificadas na resposta do servidor. |

O nome do adaptador descreve um protocolo, não exige assinatura OpenAI. Não há catálogo central de modelos ou fornecedor obrigatório, modelo padrão, fallback, retry automático, redirecionamento HTTP ou uso implícito de proxies do ambiente. A configuração remota aceita a URL base do provedor, incluindo seu prefixo, por exemplo `https://api.openai.com/v1`. HTTP fora de loopback, usuário/senha na URL, query e fragmento são recusados.

Texto e funções formam o contrato inicial. Streaming, imagens, áudio, ferramentas hospedadas pelo fornecedor, Responses API e formatos específicos de outros protocolos ainda não são suportados. Não declarar compatibilidade apenas porque um endpoint é “OpenAI compatible”. O diagnóstico remoto confirma acesso e presença do modelo em `/models`; isso não comprova geração nem suporte às funções. A declaração de funções exige documentação/teste do modelo escolhido.

Ollama precisa ter sido instalado e iniciado pelo operador. Bees não instala serviço, baixa modelos ou liga máquinas. Antes de enviar a conversa, consulta o catálogo e os metadados do modelo; rejeita nomes cloud e metadados de encaminhamento remoto. O operador precisa confiar no daemon: os probes não impedem adulteração nem troca do modelo entre consulta e geração. Para modelo servido em outra máquina, use um endpoint remoto compatível explícito, em HTTPS. O adaptador usa `think: false`; raciocínio separado da resposta ainda não integra este contrato.

Mensagens, chamadas e resultados usam tipos do Bees. Identificadores de chamadas são preservados no histórico; o protocolo Ollama recebe o nome da função associado ao identificador. Argumentos de novas chamadas são validados pelo JSON Schema declarado. Históricos concluídos mantêm chamadas mesmo quando a ferramenta deixou de estar habilitada.

O subconjunto inicial aceita objetos/propriedades, tipos, itens, enumerações e limites simples. Referências, regex, `uniqueItems` e combinadores/condicionais são recusados para limitar a resolução e o custo de validação; estrutura e tamanho dos argumentos também são limitados. Não há confiança na promessa de JSON válido do modelo.

## Configuração e credenciais

### Seleção pela interface

A criação e a edição de uma abelha oferecem listas de provedores e modelos. OpenAI, OpenRouter, Gemini e Ollama têm endereços preconfigurados; o usuário não precisa procurar URLs. **Buscar modelos** consulta o provedor escolhido com a chave informada ou com uma referência existente explicitamente autorizada. A busca não gera respostas, envia histórico, baixa modelos, salva credenciais nem autoriza salvar a configuração; **Testar conexão** continua obrigatório antes de criar ou alterar uma abelha.

O catálogo de provedores é servido pelo Bees; os modelos vêm da API escolhida, sem lista de identificadores fixada no frontend. O catálogo OpenAI usa um filtro conservador de famílias de conversa, Gemini filtra famílias de texto e OpenRouter usa modalidades anunciadas. Esses filtros não comprovam compatibilidade de geração ou ferramentas; modelos novos ou específicos podem exigir revisão do adaptador. Ollama lista apenas modelos locais anunciados, mantendo a verificação de capacidades no diagnóstico. Configurações existentes permanecem selecionadas mesmo antes de atualizar a lista.

As opções **API compatível personalizada** e **Ollama personalizado** preservam servidores adicionais. Nelas, a URL fica explícita e um identificador manual é permitido nas opções avançadas. Os mesmos limites de destino e credenciais continuam aplicados. O catálogo não introduz fallback de modelo ou fornecedor.

Rotas autenticadas: `GET /api/v1/providers` devolve as opções de conexão; `POST /api/v1/models/discover` recebe `provider_id`, chave transitória ou referência autorizada e endpoint apenas para conexão personalizada. A resposta contém somente identificadores e nomes dos modelos. A descoberta respeita CSRF, sessão, concorrência e revisão do agente; não devolve valores de credenciais. Endereços de presets não podem ser substituídos nessa rota.

Fontes dos presets e catálogos: [OpenAI — listar modelos](https://developers.openai.com/api/reference/resources/models/methods/list), [Gemini — compatibilidade OpenAI e listagem](https://ai.google.dev/gemini-api/docs/openai#list-models), [OpenRouter — catálogo](https://openrouter.ai/docs/api/api-reference/models/list-all-models-and-their-properties) e [Ollama — modelos locais](https://docs.ollama.com/api/tags).

Checkpoint de 05/10/2026: 479 testes Python e 94 testes web aprovados, além de Ruff, ESLint, typecheck/build e build Docker. Os 24 casos sem execução no Windows dependem de Linux/POSIX ou symlink. O fluxo Docker com projeto descartável validou catálogo, descoberta, CSRF, reutilização de credencial, conversa, reinício e recriação. No navegador, uma instalação própria de teste validou criação/edição com seletores, erro de chave, quatro presets, troca de destino limpando credencial, pt-BR/en/es e viewport de 390 px sem overflow horizontal. Respostas e catálogos usaram transporte controlado; não foi usada chave real nem comprovada geração real neste checkpoint.

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

Tokens são informados quando a resposta do fornecedor contém contadores válidos. O contrato distingue `reported`, `estimated` e `unknown`; não há cálculo de preço nem estimador de tokens implementado. Ausência de contadores é desconhecida, nunca consumo zero. Um valor informado não é garantia do total cobrado.

Timeout, falha de conexão, autenticação, modelo ausente, capacidade incompatível, limite de payload e resposta inválida têm diagnóstico próprio. O padrão é 30 segundos por operação HTTP, deadline de 60 segundos para a sessão (incluindo probes), e 1 MiB por requisição/resposta de geração. Catálogos têm limite separado de 4 MiB (`max_catalog_bytes`) e a descoberta aceita no máximo 4096 entradas; isso permite listas maiores sem ampliar o limite de uma resposta de conversa. Esses valores são configuráveis. Respostas comprimidas são recusadas; parsing e validação síncronos têm limites de estrutura/tamanho, mas o deadline assíncrono não preempta CPU. Limites por tarefa e orçamento virão com o executor. Não há repetição automática, inclusive após timeout: o fornecedor pode já ter processado a solicitação.

A entrada do usuário fica preservada quando uma solicitação aceita falha. Não existe fila/retomada automática nesta CLI. Nenhuma transação SQLite permanece aberta durante a rede. Respostas são gravadas somente se agente e conversa continuam na revisão observada e o contexto de memória selecionado permanece o mesmo. Conflito/cancelamento pode descartar uma resposta já gerada, com consumo externo possível; o processo informa falha e não repete.

A biblioteca interna também aceita mensagens de resultado `tool`. Quando uma resposta solicita várias funções, o chamador fornece todos os resultados em um lote; lote incompleto, duplicado ou sem chamada correspondente é recusado antes da gravação/rede. O Bees valida a associação e adapta a ordem para o protocolo de destino. Este contrato não autoriza nem realiza o efeito descrito pela função.

## Validação e limites atuais

Testes usam transportes controlados e servidores HTTP de loopback com mensagens de teste. Eles exercitam serialização, conversa/funções/resultados nos dois protocolos, restrições de destino, erros sem credenciais e preservação de estado. Isso comprova o driver e seu contrato, não a qualidade de um modelo nem equivalência completa com Dots/Grok Bot.

Nesta máquina não foi encontrado Ollama instalado/escutando na inspeção inicial; não foi feita geração contra serviço pago. A comprovação com um modelo local e um remoto reais continua necessária para completar o aceite operacional. O onboarding integra estes drivers enquanto essa verificação é preparada.

## Fontes dos protocolos

Consultadas em 04/10/2026:

- [OpenAI Docs — Create chat completion](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create): mensagens, funções, identificadores e consumo.
- [Ollama — Generate a chat message](https://docs.ollama.com/api/chat) e [API oficial no repositório](https://github.com/ollama/ollama/blob/main/docs/api.md): mensagens, função/resultados e contadores.
- [Ollama — OpenAPI](https://github.com/ollama/ollama/blob/main/docs/openapi.yaml): capacidades e metadados de modelos remotos.
- [HTTPX — Environment variables](https://www.python-httpx.org/environment_variables/): proxies implícitos e `trust_env`.
- [jsonschema — Referencing](https://python-jsonschema.readthedocs.io/en/stable/referencing/): resolução de schemas sem consulta remota.

## Interface autenticada

A interface exige sessão, permite testar a conexão, criar a abelha e enviar texto. O teste consulta disponibilidade/capacidades e gera uma confirmação vinculada à sessão e à configuração por cinco minutos; na edição, também à abelha. Alterar endpoint, modelo, capacidades ou chave exige novo teste. Geração ocorre apenas ao enviar a conversa; o teste de catálogo não comprova que o modelo responde corretamente.

Há duas chamadas de modelo simultâneas por processo e uma por conversa na API web. Mensagens são persistidas antes da chamada; falha pode deixar a mensagem enviada sem resposta. Atualize o histórico antes de decidir repetir: timeout não comprova que o fornecedor deixou de processar/cobrar. A aplicação não faz retry automático. Limites aqui não são orçamento de consumo; limites por tarefa serão implementados no executor.
