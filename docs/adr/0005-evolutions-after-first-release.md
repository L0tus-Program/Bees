# ADR 0005 — Evoluções entregues e adiadas após a primeira versão

- Data: 2026-10-23.
- Estado: proposta para decisão do mantenedor.
- Escopo: FUT-001 a FUT-012 (#100–#111).

Em 26/10/2026, o mantenedor confirmou que FUT-001–FUT-012 permanecem no backlog,
fora do aceite da v1. Isso confirma a fronteira de escopo; não fecha os critérios
restantes dessas evoluções. A licença Apache-2.0 foi escolhida na mesma revisão.

## Contexto

A primeira versão utilizável cobre conversa, agente com ferramentas, pesquisa com fontes, computador próprio, conector pessoal, skills e rotinas. As evoluções FUT foram avaliadas pelo critério de “substituir assistentes hospedados (Grok Bot, Dots) para uso individual”: entregar o que aumenta o uso diário sem abrir novas fronteiras de confiança difíceis de comprovar.

## Decisão

| Item | Situação | Recorte entregue / motivo |
| --- | --- | --- |
| FUT-001 (#100) provedores | Entregue | Presets Anthropic, xAI (Grok) e Groq por API compatível; Ollama com preparo gráfico. |
| FUT-002 (#101) canais | Parcial | Telegram e Discord por polling ([canais](../channels.md)). WhatsApp exige webhook público e conta Meta Business: adiado até haver operação com HTTPS público estável. |
| FUT-003 (#102) voz | Parcial | Ditado e leitura em voz alta pelo navegador. Mensagens de áudio transcritas pelo provedor e voz ao vivo exigem contrato de áudio no adaptador de modelos: adiado. |
| FUT-004 (#103) várias abelhas | Entregue | Consulta e delegação entre abelhas (`bees.team`) sem transferir permissões. Grupos com várias abelhas na mesma conversa: adiado. |
| FUT-005 (#104) conectores | Entregue | MCP Streamable HTTP por abelha ([mcp](../mcp.md)). Servidores stdio e OAuth interativo: adiados (executariam processos arbitrários no servidor / exigem fluxo de autorização por serviço). |
| FUT-006 (#105) gatilhos | Entregue | Webhook por rotina e rotinas proativas que só avisam com novidade ([rotinas](../routines.md)). |
| FUT-007 (#106) modelos | Parcial | Seis modelos prontos e arquivo `.bee.json` compartilhável. Catálogo/marketplace público exige moderação e hospedagem: adiado. |
| FUT-008 (#107) OptioAI | Adiado | Depende de definição do produto/contrato de integração pelo mantenedor. Pontos de extensão disponíveis: MCP (#104) e webhooks (#105). |
| FUT-009 (#108) npm/pip | Adiado | Apache-2.0 já foi escolhida (#93); publicação ainda exige conta/nome reservados e decisão própria. `apps/executor` já é instalável por `pip install ./apps/executor`; nenhuma publicação é feita pelo aceite da v1. |
| FUT-010 (#109) ensino por demonstração | Adiado | O controle humano do computador é deliberadamente invisível à abelha (logins privados). Gravar demonstrações exigiria um modo separado e explícito de “gravar passos”, com revisão antes de virar skill; skills já podem ser criadas a partir de processos revisados (BEES-020.2). |
| FUT-011 (#110) equipes | Adiado | A identidade é individual por desenho (`identity_users` com uma linha, cofre por instalação). Equipes exigem tenants, papéis e cofre por usuário — nova fronteira de segurança; recomendado só após uso individual validado. |
| FUT-012 (#111) mobile | Parcial | App instalável (PWA) e canais Telegram/Discord. Apps nativos e administração empresarial: adiados. |

## Consequências

- Itens “Parcial” ficam com o recorte entregue em revisão e o restante registrado na issue.
- Itens “Adiado” não têm código novo; a decisão (ou o contrato externo) é do mantenedor.
- Nenhum item adiado é pré-requisito da primeira versão utilizável.
