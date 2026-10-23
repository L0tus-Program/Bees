# Agente com ferramentas

A abelha pode executar pedidos com ferramentas reais: pesquisar na web, ler páginas, consultar e salvar memória e publicar arquivos de entrega. Computador próprio e máquina pessoal acrescentam arquivos, terminal, navegador e apps quando conectados ([computador](computer.md) e [conector pessoal](connector.md)). A decisão de arquitetura está na [ADR 0003](adr/0003-agent-tools.md).

## Uso

1. Configure um modelo com suporte declarado a chamadas de ferramenta.
2. No chat, clique **Ativar ferramentas recomendadas** (uma vez por abelha). Isso instala, habilita e concede os pacotes `bees.web`, `bees.workspace`, `bees.computer` e `bees.personal`; as ferramentas de computador e máquina pessoal só aparecem para o modelo quando o computador está pronto ou o conector foi concedido. A aba **Ferramentas** permite revogar cada ferramenta.
3. Escolha **Agente com ferramentas** e envie o pedido. Um cartão mostra o andamento, os passos executados e as decisões pendentes; o resultado final aparece como resposta da abelha na conversa.

Também é possível criar uma tarefa do agente sem chat: `POST /api/v1/agents/{agent}/agent-tasks`.

## Ferramentas iniciais

| Ferramenta | Função | Alvo | Política padrão |
| --- | --- | --- | --- |
| `web.search` | `web_search` | web pública | Permitir |
| `web.fetch` | `web_fetch` | web pública | Permitir |
| `memory.search` | `memory_search` | plano de controle | Permitir |
| `memory.save` | `memory_save` | plano de controle | Perguntar |
| `deliverable.publish` | `publish_file` | plano de controle | Permitir |

As políticas são editáveis em **Autonomia**. O escopo de uma regra pode usar `tool_name`, `action`, `environment_id` e parâmetros explícitos (por exemplo `domain` para `web.fetch`, `kind` para `publish_file`).

## Decisões

Quando a política exige revisão, a tarefa aguarda e o cartão mostra a ação proposta:

- **Permitir uma vez**: somente esta intenção exata.
- **Sempre permitir esta ferramenta**: cria regra Permitir para a ferramenta neste ambiente. Regras Perguntar/Bloquear continuam prevalecendo.
- **Recusar**: a ação não acontece; a abelha recebe a recusa e continua o plano.

Decisões usam UUID e revisão (CAS); reenviar a mesma decisão confirma a anterior.

## Pesquisa na web

`web_search` usa DuckDuckGo HTML por padrão, sem chave. Alternativas por ambiente da instalação:

| Variável | Efeito |
| --- | --- |
| `BEES_SEARCH_PROVIDER=searxng` + `BEES_SEARXNG_URL` | SearXNG próprio (JSON). |
| `BEES_SEARCH_PROVIDER=brave` + `BEES_BRAVE_API_KEY` | Brave Search API. |
| `BEES_WEB_PROXY` | Proxy HTTP explícito para leitura web. Proxies do ambiente nunca são herdados. |

`web_fetch` aceita somente http(s) públicos nas portas 80/443/8080/8443, sem credenciais na URL. Cada redirecionamento é validado; endereços privados, loopback, link-local, CGNAT e metadados são recusados, inclusive o IP efetivamente conectado. HTML vira texto legível com título, descrição e links; PDFs são extraídos por página. Limite de 5 MiB por resposta.

## Limites e garantias

- Até 50 gerações, 120 efeitos, 8 propostas por turno e 30 minutos ativos por tarefa (padrão 30 gerações e 20 minutos).
- Cada proposta passa por intenção, política e, se necessário, decisão humana; o efeito começa somente depois do commit de `dispatch_started`.
- Resultados de ferramentas são dados não confiáveis no contexto do modelo.
- Efeito com resultado desconhecido pausa a tarefa e exige reconhecimento humano antes de retomar.
- Parada global impede novas intenções e efeitos; a tarefa volta à fila sem efeito.
