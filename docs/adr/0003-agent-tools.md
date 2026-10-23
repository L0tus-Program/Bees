# ADR 0003 — Runtime geral de ferramentas do agente e primeira versão utilizável

- Data: 2026-10-23.
- Estado: aceita.
- Escopo: BEES-008, BEES-009, BEES-010, BEES-012, BEES-013, BEES-016 e BEES-019; complementa as ADRs 0001 e 0002.

## Contexto

Até aqui o único efeito executável por modelo era `text.normalize`, com contratos fixos em literais. A primeira versão utilizável exige que a abelha pesquise, leia fontes, publique entregas, use memória e, quando conectados, opere o computador próprio e a máquina pessoal — mantendo intenção, política, decisão humana, journal e fencing do plano de controle.

## Decisão

1. **Registro confiável (`bees_core.agent_tools.catalog`)**. Cada `ToolSpec` define nome, função exposta ao modelo, ação de política, alvo (`web`, `control_plane`, `computer`, `personal`), efeito (`read`, `write`, `external`), política padrão, JSONSchema fechado, modelo de entrada e **modelo de parâmetros de política**. Somente campos curtos e explícitos (domínio, caminho, tipo) entram na política; o conteúdo entra apenas como `input_hash`. Manifestos instaláveis agrupam contratos do registro; continuam sem código, URL ou credencial.
2. **Intenção → decisão → efeito (`AgentToolService`)**. Mesma semântica de BEES-009/010: intenção raiz `tool.intent` com snapshot privado; `ask` cria pedido humano e libera leases; o efeito tem UUID derivado; `begin` reavalia autoridade online, consome a decisão, conta o limite e registra `dispatch_started` na mesma transação, antes do executor. Publicação exige o dono atual; resultado incerto vira `outcome_unknown` com quarentena.
3. **Decisões humanas**: `allow_once`, `allow_rule` (escopo exato, como antes), `allow_tool` (regra explícita de ferramenta + ambiente, sem parâmetros; regras Perguntar/Bloquear continuam prevalecendo) e `deny`. Recusa não pausa a tarefa: o modelo recebe a rejeição e adapta o plano.
4. **Ciclo do agente (`AgentTaskWorker`)**: tarefa `agent_task` com até 50 gerações, 120 efeitos, 8 propostas por turno e 30 minutos ativos. Cada proposta é resolvida numa iteração própria da fila; o bloco `assistant/tool_calls` sempre recebe resultados canônicos antes da próxima geração. Janela de histórico maior que o chat comum.
5. **Chat com agente**: uma mensagem no modo agente cria a tarefa vinculada à conversa; passos e decisões aparecem no cartão; o resultado final é anexado uma única vez à conversa de origem.
6. **Alvos**:
   - `web`: leitura pública a partir do worker, sem cookies/credenciais, validando cada salto (http/https, portas comuns, endereços globais, IP efetivamente conectado). Proxies do ambiente não são herdados; `BEES_WEB_PROXY` é opt-in explícito.
   - `control_plane`: memória e entregas no próprio Bees.
   - `computer` e `personal`: executores remotos pareados, descritos nas próximas entregas (gateway com comando durável e journal local).

## Consequências

- A mesma fronteira de autoridade atende pesquisa, arquivos, terminal, navegador e apps; novas ferramentas exigem `ToolSpec` revisado e testes negativos, não apenas manifesto.
- Leitura web pela instalação usa a rede do plano de controle. É uma escolha explícita (ferramentas `web.*`), não fallback do computador próprio.
- Prompt injection continua possível no conteúdo lido. Mitigações: conteúdo sempre rotulado como não confiável, efeitos de escrita com política `ask` por padrão e escopos explícitos. A decisão humana continua sendo a garantia para efeitos sensíveis.
- O executor global continua com um slot; tarefas longas aguardam umas às outras.

## Alternativas

| Opção | Avaliação |
| --- | --- |
| Generalizar `ResourceToolService` in-place | Arriscaria regressões no recurso de texto já aceito; mantido separado e compatível. |
| Frameworks de agente (LangGraph etc.) | Não carregam o estado canônico, políticas e fencing exigidos pela ADR 0001. |
| Ferramentas hospedadas do fornecedor | Dependem do provedor e não passam pelo journal/política do Bees. |
