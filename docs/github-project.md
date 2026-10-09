# Planejamento e execução no GitHub

O [repositório](https://github.com/L0tus-Program/Bees) contém código, documentação, testes e histórico. O [Project Bees](https://github.com/users/L0tus-Program/projects/3/views/1?layout=board) organiza histórias e subtarefas. A visibilidade privada do Project é preservada; todas as issues possuem informações suficientes para consulta pelo repositório.

## Escopo e marcos

O MVP é individual: uma abelha persistente, modelos substituíveis, memória, tarefas, entregas e rotinas. Os dois ambientes fazem parte do aceite final: computador próprio com navegador/apps e recursos autorizados da máquina pessoal. Alta autonomia exige escopos concretos, políticas atuais e decisão humana quando necessário. Equipes, voz, canais adicionais e marketplace pertencem à evolução.

| Marco | Resultado verificável |
| --- | --- |
| M0 — Fundação | Estado, modelos, execução textual, políticas, contratos e instalação do plano de controle. |
| M1 — Assistente no computador próprio | VM persistente real, navegador/app, intervenção humana e entrega com fontes. |
| M2 — Assistente na máquina pessoal | Conector opcional, recursos explicitamente autorizados e revogação efetiva. |
| M3 — Responsabilidade contínua | Skills, rotinas, limites, recuperação e portabilidade. |
| M4 — MVP utilizável | Três fluxos completos, setup gráfico, idiomas e validação dos ambientes suportados. |
| Evolução | Funcionalidades posteriores, com prioridade revista depois do MVP. |

Não há datas ou estimativas de esforço. M1 permite uma primeira entrega útil; o MVP completo termina em M4, com os dois ambientes comprovados. Um módulo documentado ou um teste com hardware falso não equivale a uma capacidade de produção.

## Organização das issues

- **Histórias `BEES-001` a `BEES-028`:** preservam os IDs, requisitos e estados do planejamento original. As seis concluídas permanecem fechadas com evidências; histórias parcialmente implementadas continuam abertas.
- **Subtarefas `BEES-NNN.n`:** recortes executáveis do trabalho restante, ligados pela hierarquia nativa de subissues. Não repetem funcionalidades já entregues. Seus planos técnicos distinguem código existente de componentes propostos.
- **Evoluções `FUT-001` a `FUT-012`:** propostas P1/P2 posteriores ao MVP, sem promessa de implementação imediata ou dependência obrigatória para a versão individual.
- **Prioridades:** P0 é requisito do MVP; P1 é evolução; P2 é visão futura.
- **Status:** Todo é pendente; In Progress é trabalho em andamento; Done exige critérios comprovados e issue fechada. Bloqueios registram motivo e próximo passo na issue, com o rótulo `estado:bloqueado`.
- **Campos do Project:** prioridade, tipo, marco, ID de rastreabilidade e ordem sugerida. Milestones do repositório reproduzem os portões de entrega; rótulos identificam tipo, prioridade e área.

Dependências em cada descrição são pré-requisitos de comportamento. Uma capacidade parcial já disponível pode permitir avanço em paralelo, mesmo com a história ampla aberta. Não transforme esses vínculos em impedimentos artificiais nem autorize efeitos reais por inferência. Os pré-requisitos de aceite final continuam explícitos.

## Como manter o planejamento

Antes de implementar, leia a issue, consulte os arquivos e verifique suas dependências. Durante o trabalho, mantenha o recorte e o estado atualizados. A cada checkpoint, registre commit, validação, evidência e limitações; feche somente o que satisfaz seu aceite. Ao dividir uma subtarefa, crie uma issue real e preserve vínculos/IDs, sem duplicar trabalho.

O guia [CONTRIBUTING](../CONTRIBUTING.md) descreve branches, testes, revisão e checkpoints. As [ADRs](adr/0001-foundation.md) e os documentos operacionais continuam sendo a referência de arquitetura. Issues representam o planejamento e podem evoluir mediante decisões explícitas; exemplos técnicos não alteram políticas, não concedem acesso a máquinas e não substituem autorização do usuário.

## Visões do Project

- [Histórias MVP](https://github.com/users/L0tus-Program/projects/3/views/1) — Quadro das 28 histórias, agrupadas por estado.
- [Execução](https://github.com/users/L0tus-Program/projects/3/views/2) — Quadro das subtarefas abertas; itens Done ficam fora desta visão.
- [Evolução](https://github.com/users/L0tus-Program/projects/3/views/3) — Tabela das 12 propostas posteriores ao MVP.
- [Backlog completo](https://github.com/users/L0tus-Program/projects/3/views/4) — Tabela com todos os itens, inclusive os concluídos.

## Mapa das histórias

Importação de **09/10/2026**: 28 histórias, 71 subtarefas do escopo restante e 12 evoluções. Uma subtarefa técnica adicional, CI-001, registra a correção da primeira CI Windows remota. A partir dela foram abertas BEES-011.6 (P2), para o endurecimento posterior dos diagnósticos PowerShell, e CI-002 (P1), para as falhas intermitentes das interops HTTP no Windows. As descrições incluem objetivo, estado comprovado, plano técnico, dependências, referências, critérios de aceite e validação. Consulte as issues para o estado atual; os estados abaixo são o snapshot da importação.

| História | Estado inicial | Prioridade | Marco |
| --- | --- | --- | --- |
| [BEES-001 — Fechar arquitetura inicial e fronteiras de execução](https://github.com/L0tus-Program/Bees/issues/1) | concluído | P0 | M0 |
| [BEES-002 — Preparar repositório e ambiente reproduzível](https://github.com/L0tus-Program/Bees/issues/2) | concluído | P0 | M0 |
| [BEES-003 — Persistir o estado do assistente](https://github.com/L0tus-Program/Bees/issues/3) | concluído | P0 | M0 |
| [BEES-004 — Iniciar a experiência individual pelo frontend](https://github.com/L0tus-Program/Bees/issues/4) | concluído | P0 | M0 |
| [BEES-005 — Integrar modelos remoto e local por adaptadores](https://github.com/L0tus-Program/Bees/issues/5) | em andamento | P0 | M0 |
| [BEES-006 — Criar abelhas persistentes e memória editável](https://github.com/L0tus-Program/Bees/issues/9) | concluído | P0 | M0 |
| [BEES-007 — Executar tarefas sem bloquear a conversa](https://github.com/L0tus-Program/Bees/issues/10) | em andamento | P0 | M0 |
| [BEES-008 — Aplicar políticas de autonomia antes das ações](https://github.com/L0tus-Program/Bees/issues/14) | em andamento | P0 | M0 |
| [BEES-009 — Perguntar, guardar e editar escolhas do usuário](https://github.com/L0tus-Program/Bees/issues/18) | em andamento | P0 | M0 |
| [BEES-010 — Contratos de plugins e ferramentas com auditoria](https://github.com/L0tus-Program/Bees/issues/22) | em andamento | P0 | M0 |
| [BEES-011 — Criar e comprovar o computador próprio persistente da abelha](https://github.com/L0tus-Program/Bees/issues/26) | em andamento | P0 | M1 |
| [BEES-012 — Executar arquivos, terminal e apps no computador próprio](https://github.com/L0tus-Program/Bees/issues/32) | pendente | P0 | M1 |
| [BEES-013 — Navegar e pesquisar no navegador próprio com sessões e evidências](https://github.com/L0tus-Program/Bees/issues/36) | pendente | P0 | M1 |
| [BEES-014 — Visualizar e assumir/devolver controle](https://github.com/L0tus-Program/Bees/issues/40) | pendente | P0 | M1 |
| [BEES-015 — Demonstrar entrega e revisão reais no app do computador próprio](https://github.com/L0tus-Program/Bees/issues/44) | pendente | P0 | M1 |
| [BEES-016 — Conectar a máquina pessoal com recursos e alcance controlados](https://github.com/L0tus-Program/Bees/issues/48) | pendente | P0 | M2 |
| [BEES-017 — Trabalhar em arquivos, navegador e app pessoais autorizados](https://github.com/L0tus-Program/Bees/issues/52) | pendente | P0 | M2 |
| [BEES-018 — Anexar, gerar e revisar artefatos](https://github.com/L0tus-Program/Bees/issues/56) | pendente | P0 | M1 |
| [BEES-019 — Concluir uma pesquisa útil com fontes](https://github.com/L0tus-Program/Bees/issues/60) | pendente | P0 | M1 |
| [BEES-020 — Salvar procedimentos validados como skills versionadas](https://github.com/L0tus-Program/Bees/issues/64) | pendente | P0 | M3 |
| [BEES-021 — Criar e operar rotinas persistentes com agenda, políticas e histórico](https://github.com/L0tus-Program/Bees/issues/68) | pendente | P0 | M3 |
| [BEES-022 — Limitar consumo e comunicar atenção necessária](https://github.com/L0tus-Program/Bees/issues/72) | pendente | P0 | M3 |
| [BEES-023 — Exportar, restaurar e trocar provedor preservando trabalho](https://github.com/L0tus-Program/Bees/issues/77) | pendente | P0 | M3 |
| [BEES-024 — Retomar com segurança após falhas e reinícios](https://github.com/L0tus-Program/Bees/issues/81) | pendente | P0 | M3 |
| [BEES-025 — Refinar a experiência completa e idiomas iniciais](https://github.com/L0tus-Program/Bees/issues/85) | pendente | P0 | M4 |
| [BEES-026 — Validar os fluxos e preparar primeira versão utilizável](https://github.com/L0tus-Program/Bees/issues/89) | pendente | P0 | M4 |
| [BEES-027 — Subir a aplicação completa com Docker Compose](https://github.com/L0tus-Program/Bees/issues/94) | concluído | P0 | M0 |
| [BEES-028 — Concluir todo o setup do usuário final pela interface](https://github.com/L0tus-Program/Bees/issues/95) | em andamento | P0 | M0 |

## Subtarefas executáveis

Os filhos são issues reais vinculadas à história mãe. A ordem dos IDs facilita consulta; a descrição de cada issue informa quais pré-requisitos são necessários ao seu recorte. Todas as 71 subtarefas de planejamento começam pendentes; CI-001 foi aberta em andamento durante a organização.

### BEES-005 — Integrar modelos remoto e local por adaptadores

- [BEES-005.1 — Comprovar delegação suportada com modelo remoto real](https://github.com/L0tus-Program/Bees/issues/6)
- [BEES-005.2 — Validar conversa e delegação em backend local real](https://github.com/L0tus-Program/Bees/issues/7)
- [BEES-005.3 — Documentar troca remoto/local com preservação e incompatibilidades](https://github.com/L0tus-Program/Bees/issues/8)

### BEES-007 — Executar tarefas sem bloquear a conversa

- [BEES-007.1 — Coordenar etapas de tarefa com ferramentas reais e limites](https://github.com/L0tus-Program/Bees/issues/11)
- [BEES-007.2 — Persistir espera e exclusividade dos recursos de execução](https://github.com/L0tus-Program/Bees/issues/12)
- [BEES-007.3 — Demonstrar tarefa real com progresso, controles e resultado persistente](https://github.com/L0tus-Program/Bees/issues/13)

### BEES-008 — Aplicar políticas de autonomia antes das ações

- [BEES-008.1 — Declarar escopos e autoridade para as ferramentas reais iniciais](https://github.com/L0tus-Program/Bees/issues/15)
- [BEES-008.2 — Aplicar guarda online na fronteira dos executores reais](https://github.com/L0tus-Program/Bees/issues/16)
- [BEES-008.3 — Reavaliar autonomia em cada ação de rotina](https://github.com/L0tus-Program/Bees/issues/17)

### BEES-009 — Perguntar, guardar e editar escolhas do usuário

- [BEES-009.1 — Vincular decisões próprias aos snapshots de ferramentas reais](https://github.com/L0tus-Program/Bees/issues/19)
- [BEES-009.2 — Exibir alcance e rascunho revisável nas decisões de ferramentas](https://github.com/L0tus-Program/Bees/issues/20)
- [BEES-009.3 — Comprovar revogação e reutilização exata nos dois ambientes](https://github.com/L0tus-Program/Bees/issues/21)

### BEES-010 — Contratos de plugins e ferramentas com auditoria

- [BEES-010.1 — Definir transporte fechado de ferramentas e journal do executor](https://github.com/L0tus-Program/Bees/issues/23)
- [BEES-010.2 — Integrar chamadas e resultados de ferramentas ao ciclo limitado de modelo](https://github.com/L0tus-Program/Bees/issues/24)
- [BEES-010.3 — Reutilizar contratos e revogação de ferramentas nas rotinas](https://github.com/L0tus-Program/Bees/issues/25)

### BEES-011 — Criar e comprovar o computador próprio persistente da abelha

- [BEES-011.1 — Preparar kit e workspace VMMS com vínculos e validação fechados](https://github.com/L0tus-Program/Bees/issues/27)
- [BEES-011.2 — Integrar inscrição técnica e acionamento humano por plano explícito](https://github.com/L0tus-Program/Bees/issues/28)
- [BEES-011.3 — Comprovar provisionamento Hyper-V físico em janela humana autorizada](https://github.com/L0tus-Program/Bees/issues/29)
- [BEES-011.4 — Integrar boot humano, identidade guest e ponte física exclusiva](https://github.com/L0tus-Program/Bees/issues/30)
- [BEES-011.5 — Comprovar rede filtrada, recursos e persistência do ambiente próprio](https://github.com/L0tus-Program/Bees/issues/31)
- [BEES-011.6 — Isolar módulos PowerShell nos diagnósticos de Hyper-V](https://github.com/L0tus-Program/Bees/issues/114) (P2)

### BEES-012 — Executar arquivos, terminal e apps no computador próprio

- [BEES-012.1 — Definir transporte e executor de arquivos/comandos no guest](https://github.com/L0tus-Program/Bees/issues/33)
- [BEES-012.2 — Integrar tarefas, política e aprovações concretas aos efeitos do guest](https://github.com/L0tus-Program/Bees/issues/34)
- [BEES-012.3 — Entregar arquivos duráveis e app suportado com limites demonstrados](https://github.com/L0tus-Program/Bees/issues/35)

### BEES-013 — Navegar e pesquisar no navegador próprio com sessões e evidências

- [BEES-013.1 — Criar driver Chromium próprio e contratos de observação/entrada](https://github.com/L0tus-Program/Bees/issues/37)
- [BEES-013.2 — Aplicar políticas e proveniência à extração e formulários web](https://github.com/L0tus-Program/Bees/issues/38)
- [BEES-013.3 — Persistir sessões próprias e preparar reautenticação/handoff](https://github.com/L0tus-Program/Bees/issues/39)

### BEES-014 — Visualizar e assumir/devolver controle

- [BEES-014.1 — Entregar preview autenticado com frames reais e estados de conexão](https://github.com/L0tus-Program/Bees/issues/41)
- [BEES-014.2 — Arbitrar controle humano exclusivo e devolução explícita](https://github.com/L0tus-Program/Bees/issues/42)
- [BEES-014.3 — Entregar login privado e verificar ausência de coleta de segredos](https://github.com/L0tus-Program/Bees/issues/43)

### BEES-015 — Demonstrar entrega e revisão reais no app do computador próprio

- [BEES-015.1 — Definir caso útil e validação do app inicial suportado](https://github.com/L0tus-Program/Bees/issues/45)
- [BEES-015.2 — Demonstrar tarefa no app com acompanhamento e intervenção humana](https://github.com/L0tus-Program/Bees/issues/46)
- [BEES-015.3 — Comprovar revisão, persistência e falhas da entrega do app](https://github.com/L0tus-Program/Bees/issues/47)

### BEES-016 — Conectar a máquina pessoal com recursos e alcance controlados

- [BEES-016.1 — Criar identidade, pareamento e revogação do conector pessoal](https://github.com/L0tus-Program/Bees/issues/49)
- [BEES-016.2 — Implementar concessões de recursos e arquivos com contenção efetiva](https://github.com/L0tus-Program/Bees/issues/50)
- [BEES-016.3 — Integrar journal, disponibilidade e controles do executor pessoal](https://github.com/L0tus-Program/Bees/issues/51)

### BEES-017 — Trabalhar em arquivos, navegador e app pessoais autorizados

- [BEES-017.1 — Implementar adapters de navegador/app pessoais com sessão explícita](https://github.com/L0tus-Program/Bees/issues/53)
- [BEES-017.2 — Implementar exclusividade, handoff e autenticação privada pessoais](https://github.com/L0tus-Program/Bees/issues/54)
- [BEES-017.3 — Demonstrar fluxo pessoal com bloqueio, revogação e reconexão](https://github.com/L0tus-Program/Bees/issues/55)

### BEES-018 — Anexar, gerar e revisar artefatos

- [BEES-018.1 — Persistir blobs e publicar versões com download autorizado](https://github.com/L0tus-Program/Bees/issues/57)
- [BEES-018.2 — Entregar anexação e revisão de arquivos na conversa](https://github.com/L0tus-Program/Bees/issues/58)
- [BEES-018.3 — Gerar relatório editável e tabela válidos em tarefa real](https://github.com/L0tus-Program/Bees/issues/59)

### BEES-019 — Concluir uma pesquisa útil com fontes

- [BEES-019.1 — Definir corpus, contrato de fontes e rubrica de pesquisa](https://github.com/L0tus-Program/Bees/issues/61)
- [BEES-019.2 — Compor pesquisa real com redirecionamento e entrega persistente](https://github.com/L0tus-Program/Bees/issues/62)
- [BEES-019.3 — Validar qualidade da pesquisa e deduplicar atenção relevante](https://github.com/L0tus-Program/Bees/issues/63)

### BEES-020 — Salvar procedimentos validados como skills versionadas

- [BEES-020.1 — Criar contratos e persistência de skills/revisões imutáveis](https://github.com/L0tus-Program/Bees/issues/65)
- [BEES-020.2 — Converter processo validado em rascunho de skill revisável](https://github.com/L0tus-Program/Bees/issues/66)
- [BEES-020.3 — Executar revisão de skill com entradas e permissões atuais](https://github.com/L0tus-Program/Bees/issues/67)

### BEES-021 — Criar e operar rotinas persistentes com agenda, políticas e histórico

- [BEES-021.1 — Definir agenda, ocorrências e semântica temporal duráveis](https://github.com/L0tus-Program/Bees/issues/69)
- [BEES-021.2 — Integrar scheduler ao worker com claims, limites e política atual](https://github.com/L0tus-Program/Bees/issues/70)
- [BEES-021.3 — Gerenciar rotinas por interface/conversa e histórico relevante](https://github.com/L0tus-Program/Bees/issues/71)

### BEES-022 — Limitar consumo e comunicar atenção necessária

- [BEES-022.1 — Persistir e aplicar limites de consumo por execução e agente](https://github.com/L0tus-Program/Bees/issues/73)
- [BEES-022.2 — Implementar parada global durável em todos os despachos](https://github.com/L0tus-Program/Bees/issues/74)
- [BEES-022.3 — Persistir atenção e preferências de aviso sem duplicidade](https://github.com/L0tus-Program/Bees/issues/75)
- [BEES-022.4 — Exibir orçamento, atividade e operações encerrando na interface](https://github.com/L0tus-Program/Bees/issues/76)

### BEES-023 — Exportar, restaurar e trocar provedor preservando trabalho

- [BEES-023.1 — Especificar e gerar exportação lógica versionada sem segredos](https://github.com/L0tus-Program/Bees/issues/78)
- [BEES-023.2 — Restaurar em staging com revisão humana de acesso e rotinas pausadas](https://github.com/L0tus-Program/Bees/issues/79)
- [BEES-023.3 — Validar continuação remoto/local e reconstrução de dados derivados](https://github.com/L0tus-Program/Bees/issues/80)

### BEES-024 — Retomar com segurança após falhas e reinícios

- [BEES-024.1 — Compor recuperação operacional com evidência e decisões humanas](https://github.com/L0tus-Program/Bees/issues/82)
- [BEES-024.2 — Executar matriz de falhas reais e verificar contagem dos efeitos](https://github.com/L0tus-Program/Bees/issues/83)
- [BEES-024.3 — Entregar diagnóstico humano, resultado parcial e runbook de retomada](https://github.com/L0tus-Program/Bees/issues/84)

### BEES-025 — Refinar a experiência completa e idiomas iniciais

- [BEES-025.1 — Integrar navegação, atenção e estados dos dois ambientes](https://github.com/L0tus-Program/Bees/issues/86)
- [BEES-025.2 — Localizar entregas no histórico e oferecer atalhos descobríveis](https://github.com/L0tus-Program/Bees/issues/87)
- [BEES-025.3 — Concluir idioma, tema, responsividade e acessibilidade do ciclo](https://github.com/L0tus-Program/Bees/issues/88)

### BEES-026 — Validar os fluxos e preparar primeira versão utilizável

- [BEES-026.1 — Consolidar matriz de regressão e executar CI com evidências](https://github.com/L0tus-Program/Bees/issues/90)
- [BEES-026.2 — Comprovar os três fluxos completos com ambientes e modelos reais](https://github.com/L0tus-Program/Bees/issues/91)
- [BEES-026.3 — Validar instalação limpa sem terminal e publicar guias de primeira versão](https://github.com/L0tus-Program/Bees/issues/92)
- [BEES-026.4 — Escolher licença e revisar dependências antes do release formal](https://github.com/L0tus-Program/Bees/issues/93)
- [CI-001 — Corrigir CI Windows preservando owner privado e bytes da âncora](https://github.com/L0tus-Program/Bees/issues/112)
- [CI-002 — Investigar falhas intermitentes das interops HTTP do provisionador no Windows](https://github.com/L0tus-Program/Bees/issues/115) (P1)

### BEES-028 — Concluir todo o setup do usuário final pela interface

- [BEES-028.1 — Guiar emissão privada e inscrição local do provisionador pela interface](https://github.com/L0tus-Program/Bees/issues/96)
- [BEES-028.2 — Empacotar e integrar launcher operacional preservando estado privado](https://github.com/L0tus-Program/Bees/issues/97)
- [BEES-028.3 — Concluir launchers e primeiro acesso nas distribuições suportadas](https://github.com/L0tus-Program/Bees/issues/98)
- [BEES-028.4 — Guiar recuperação de setup e preparo do modelo local pela interface](https://github.com/L0tus-Program/Bees/issues/99)

## Evolução após o MVP

| Proposta | Prioridade |
| --- | --- |
| [FUT-001 — Mais provedores/capacidades, incluindo Anthropic e xAI conforme demanda](https://github.com/L0tus-Program/Bees/issues/100) | P1 |
| [FUT-002 — Telegram, Discord e WhatsApp por adaptadores com identidades/permissões](https://github.com/L0tus-Program/Bees/issues/101) | P1 |
| [FUT-003 — Voz ao vivo, ditado e mensagens de áudio](https://github.com/L0tus-Program/Bees/issues/102) | P1 |
| [FUT-004 — Colaboração/delegação entre várias abelhas e grupos](https://github.com/L0tus-Program/Bees/issues/103) | P1 |
| [FUT-005 — Mais apps/sistemas, conectores de serviços, MCP e geração de formatos avançados](https://github.com/L0tus-Program/Bees/issues/104) | P1 |
| [FUT-006 — Gatilhos de eventos e pesquisa proativa autorizada com limites](https://github.com/L0tus-Program/Bees/issues/105) | P1 |
| [FUT-007 — Templates compartilháveis e catálogo/marketplace público](https://github.com/L0tus-Program/Bees/issues/106) | P1 |
| [FUT-008 — Integração opcional com OptioAI após validar Bees funcional](https://github.com/L0tus-Program/Bees/issues/107) | P1 |
| [FUT-009 — Distribuição npm/pip quando houver beta e stack/empacotamento definidos](https://github.com/L0tus-Program/Bees/issues/108) | P1 |
| [FUT-010 — Ensino por demonstração e personalização visual avançada](https://github.com/L0tus-Program/Bees/issues/109) | P2 |
| [FUT-011 — Equipes com tenants próprios, memória compartilhada e papéis, se fizer sentido](https://github.com/L0tus-Program/Bees/issues/110) | P2 |
| [FUT-012 — Apps móveis nativos e administração empresarial](https://github.com/L0tus-Program/Bees/issues/111) | P2 |

Os IDs preservam rastreabilidade; fechar uma issue depende do comportamento comprovado, e não da data ou quantidade de checkpoints. O Project é a referência operacional para progresso posterior.
