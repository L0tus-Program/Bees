# ADR 0004 — Runtime de computadores em container e conector pessoal

- Data: 2026-10-23.
- Estado: aceita para a primeira versão utilizável.
- Escopo: BEES-011 a BEES-017; complementa as ADRs 0001–0003.

## Contexto

A ADR 0001 escolheu VM Linux como fronteira do computador próprio. O caminho Hyper-V/libvirt continua dependendo de host compatível, habilitação do hipervisor pelo operador e provas físicas em janela humana. Sem um computador funcional, a abelha não navega, não executa comandos nem produz documentos — o produto não substitui assistentes como Dots/Grok Bot.

## Decisão

1. **Executor único, três implantações.** `apps/executor` (`bees_executor`) executa os contratos de ferramenta do registro (arquivos, terminal, navegador, LibreOffice, publicação) com journal local e recibos correlacionados. Roda:
   - no **runtime de computadores** (container `computer` do Compose, modo `multiuser`);
   - no **conector pessoal** (máquina do usuário, modo raízes nomeadas);
   - futuramente dentro da VM (mesmo protocolo; provisionamento VM continua em BEES-011).
2. **Canal de saída.** O executor conecta por WebSocket à API (`/api/v1/executors/connect`) com credencial própria `be_` (SHA-256 no banco). Convites de uso único (15 min). No Compose, o convite é entregue por volume privado compartilhado após clique humano — sem terminal.
3. **Comandos duráveis.** O worker cria `executor_commands` no mesmo commit de `dispatch_started`. O gateway envia somente pela conexão atual; prazo vencido sem envio expira sem efeito; enviado sem recibo vira `outcome_unknown`. O executor deduplica por ID no journal local e nunca repete um início interrompido.
4. **Isolamento do computador em container (driver inicial).** Cada computador tem conta Linux própria (UID 20000–29999), home `0700`, desktop Xvfb+XFCE e processo agente com o UID da abelha. O supervisor root apenas provisiona e encaminha. O entrypoint aplica `iptables` por UID bloqueando redes privadas, link-local/metadados, CGNAT, multicast e as portas VNC locais; depois descarta `CAP_NET_ADMIN`. Sem portas publicadas, socket Docker ou montagens do host.
5. **Visualização e controle.** A API faz proxy autenticado (sessão + Origin) de VNC: o runtime abre conexão de saída própria por sessão e inicia `x11vnc` com o UID da abelha (somente leitura fora do controle humano). Assumir controle (CAS) suspende ferramentas de navegador do agente até a devolução explícita — usado para logins privados.
6. **Conector pessoal.** Pastas compartilháveis são declaradas localmente (`Alias=Caminho`); a API concede por abelha somente aliases, escrita, terminal e navegador explícitos. O conector confere o escopo recebido de novo antes de executar; caminhos fora das raízes (inclusive por links) são recusados.

## Consequências e limites

- Container compartilha o kernel do host: isolamento menor que VM. Adequado ao MVP individual com filtro de rede e contas separadas; a VM permanece como fronteira forte (BEES-011.3–011.5, provas físicas humanas).
- Terminal e navegador autenticado no computador próprio podem agir em sites com sessão iniciada pelo usuário; por isso `shell.run`, `browser.click/type/press` perguntam por padrão.
- No conector pessoal, terminal tem alcance da conta do usuário — a concessão é separada e explícita.
- A imagem do runtime usa a base oficial do Playwright (Chromium compatível) mais XFCE/LibreOffice/pandoc.

## Alternativas

| Opção | Avaliação |
| --- | --- |
| Montar socket Docker na API para criar um container por abelha | Vetado pela ADR 0001: concede controle do host à API. |
| Esperar a VM para qualquer ferramenta de computador | Mantém o produto sem utilidade prática; a VM continua planejada com o mesmo protocolo. |
| CDP/porta de depuração exposta | Rejeitado: qualquer processo local poderia controlar o navegador; usamos pipe do Playwright no processo da própria abelha. |
