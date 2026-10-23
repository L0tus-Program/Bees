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

## Pesquisa com fontes

No chat, escolha **Pesquisa com fontes**. A abelha recebe instruções de pesquisa (fontes primárias e recentes, citação por URL, incerteza explícita, nunca seguir instruções das páginas). Ao concluir, o Bees publica na tarefa:

- `pesquisa.md`: resposta, fontes lidas (URL final, título e momento de coleta) e verificação automática dos links;
- `pesquisa-fontes.json` (`bees.research.task/v1`): fontes com hash SHA-256 do texto observado, links citados, conferidos e não verificados.

As fontes vêm somente de leituras confirmadas nesta tarefa (`web_fetch` e aberturas do navegador). Links citados sem leitura aparecem como **não verificados**; resposta sem citações recebe aviso. A verificação confere procedência, não a veracidade — a revisão humana continua necessária ([corpus e rubrica](research-corpus.md)).

## Skills e rotinas do agente

Uma skill cujos recursos declaram ferramentas do registro (por exemplo `web.search, web.fetch, deliverable.publish` ou `computer.shell.run`) roda no ciclo do agente: somente as ferramentas declaradas são oferecidas ao modelo, e cada ação continua sujeita a concessões, políticas e decisões atuais. O ambiente declarado precisa ser coerente (`own` para `computer.*`, `personal` para `personal.*`).

Rotinas com essas skills usam o modo de execução **agente** (`execution_mode: "agent"`): não fixam recurso, cada ocorrência cria uma tarefa do agente e o resultado é publicado na conversa de destino. Execução manual e agendada compartilham o mesmo caminho; parada global, agenda e limites seguem as regras das [rotinas](routines.md).

## Equipe de abelhas

Com o pacote **Equipe de abelhas** (`bees.team`) concedido e outra abelha ativa, o agente pode:

- `bees_list` — ver nome e propósito das outras abelhas;
- `bees_ask` — consultar uma abelha especialista (resposta só de texto, com o modelo e o orçamento dela; as consultas ficam numa conversa “Consultas de …” da especialista);
- `bees_delegate` — **pede aprovação** e cria uma tarefa do agente na outra abelha, que trabalha com as próprias ferramentas, permissões e decisões. O resultado aparece na conversa da especialista e volta à conversa de quem delegou como “Resultado da abelha …” (e, se houver canal vinculado, no Telegram/Discord).

Delegação é idempotente por chamada (sem tarefa duplicada em retomada) e não transfere permissões: cada abelha só usa o que lhe foi concedido.
