# ADR 0001 — Fundação individual e fronteiras de execução

- Data: 2026-10-04.
- Estado: aceita para implementação inicial.
- Escopo: BEES-001; decisões de produto consolidadas para um MVP individual.
- Revisão: reavaliar por evidência de uso, incompatibilidade operacional ou mudança de requisitos.

## Contexto

Bees precisa manter agentes, memória, tarefas, rotinas, decisões e entregas independentemente do fornecedor de modelos. A mesma pessoa deve delegar trabalho a um computador próprio da abelha ou à máquina pessoal autorizada. A conversa deve permanecer disponível durante execução, com autonomia alta e escolhas sobre ações sensíveis persistentes/editáveis.

A primeira instalação deve ser viável localmente e em VPS. Equipes/tenants, marketplace público, voz e coordenação entre várias abelhas não justificam infraestrutura distribuída neste ciclo.

## 1. Stack e alternativas

| Área | Decisão | Alternativa considerada e motivo |
| --- | --- | --- |
| Serviço | Python 3.14, FastAPI, Pydantic e HTTPX; monólito modular | Node/TypeScript também atende e unifica linguagens. Python facilita integração com automação e arquivos; não é alegação de maior desempenho. |
| Frontend | React, TypeScript e Vite; SPA estática | Next.js oferece servidor/SSR, mas o produto autenticado não precisa desse runtime inicialmente. Não implica dependência de Vercel. |
| Persistência | SQLite WAL via APSW, disco local ao serviço | PostgreSQL aumenta operação da instalação individual. Adotar quando houver contenção medida ou serviço distribuído; não implementar ambos agora. |
| Migrações | SQL numerado, checksum e aplicação controlada | SQLAlchemy/Alembic são opções válidas, mas não necessários com driver APSW e esquema pequeno. Contratos de domínio encapsulam SQL/exceções. |
| Execução | API e worker como processos do mesmo produto | BackgroundTasks da API não será fila durável; reiniciar frontend/API não deve apagar trabalho. |
| Agenda/fila | Estado e ocorrências persistidos no banco, claim/lease e polling limitado | Sem Redis/Celery obrigatórios no MVP. Evoluir depois de medir necessidade. |
| Modelos | Adaptadores próprios sobre contratos e HTTPX | Framework de agentes não será fonte de estado. Primeiro remoto compatível com API suportada e backend local Ollama; capacidades sempre explícitas. |

Dependências ficam fixadas em uv.lock e package-lock.json. Python 3.14.4 e Node 22.17.0 foram encontrados na máquina de desenvolvimento; manter versão mínima compatível no projeto e validação Linux na CI.

React/Vite produz arquivos estáticos; preview é ferramenta de desenvolvimento. Next pode exportar estático, com restrições para recursos dependentes de servidor. [React](https://react.dev/learn/build-a-react-app-from-scratch), [Vite](https://vite.dev/guide/static-deploy.html), [Next.js](https://nextjs.org/docs/app/guides/static-exports).

Tarefas de longa duração usarão fila/estado do produto. FastAPI documenta execução de BackgroundTasks após a resposta; não há persistência de objetivo do Bees nesse mecanismo. [FastAPI](https://fastapi.tiangolo.com/tutorial/background-tasks/).

## 2. Estrutura e fronteiras

- apps/api: transporte HTTP, composição e ciclo de vida do serviço.
- packages/core: domínio/casos de uso futuros, independente de FastAPI, fornecedor e hipervisor.
- apps/worker: coordenador futuro, agenda, checkpoints e distribuição de ações.
- apps/web: interface, mensagens traduzidas e cliente API.
- apps/connector: conector pessoal futuro; não compartilha banco do serviço.
- runtime: imagens/scripts futuros para computador próprio e agentes de execução.
- docs: decisões, contratos, instalação e operação versionáveis.

Não criar todos os pacotes antes de existir comportamento que os justifique. O scaffold inicial contém apenas apps/api e apps/web; os demais são fronteiras de evolução.

Plugins de código não são importados arbitrariamente no processo que contém credenciais/banco. Tools tipadas, manifesto e permissão são contratos; código não confiável precisa de processo/ambiente apropriado.

### Plano de controle

Domínio, políticas, estado e credenciais de modelos permanecem no serviço. API e worker usam repositórios/UnitOfWork no mesmo host, com transações curtas. Nenhum executor recebe acesso ao arquivo SQLite. Host local/VPS usa filesystem local, não pasta de rede/sincronização.

Uma chamada de modelo, browser ou conector não mantém transação aberta.

### Fluxo de uma ação

1. Preparar entradas validadas e persistir intenção.
2. Consultar política atual para agente, ambiente, ferramenta, alvo, identidade e parâmetros.
3. Se necessário, guardar decisão humana pontual ou regra persistente com escopo.
4. Despachar ação por ID com prazo, limites, revisão e geração do lease.
5. Executor faz autorização online imediatamente antes do efeito e registra seu journal.
6. Resultado e checkpoint são persistidos; efeito de resultado desconhecido é reconciliado.

O protocolo e as rotinas não substituem controles no executor.

## 3. Computador próprio

**Escolha:** VM Linux persistente com desktop leve, Chromium e LibreOffice Writer como primeiro app de referência. Terminal e workspace durável ficam dentro da VM. Docker pode empacotar serviços ou ferramentas dentro desse ambiente; não será a fronteira universal de segurança do computador.

- Linux com KVM/libvirt quando disponível.
- Windows com Hyper-V quando suportado; frontend/conector podem usar VM própria em outro host se a máquina não puder hospedar o hipervisor.
- VPS sem virtualização/nested disponível mantém o plano de controle e se conecta a VM em outro host do usuário. Não afirmar suporte a VM local em qualquer VPS.
- Guest executor abre conexão de saída com o serviço e informa capacidades.
- VNC privado com noVNC por gateway autenticado; nunca porta VNC pública.
- Disco persistente, snapshots para manutenção e workspace separado de diretórios temporários.
- Sem montagem automática de disco/perfil pessoal, socket Docker/libvirt, clipboard compartilhado ou interfaces administrativas do host.
- Rede do guest deve bloquear destinos privados, loopback do host e metadata por padrão. Reservar exceção restrita ao endpoint autenticado do plano de controle para a conexão de saída; outras concessões precisam ser explícitas. A efetividade será comprovada na implementação.
- Limites iniciais de referência: 2 vCPU, 4 GB RAM, 30 GB disco; hipótese operacional, sem inferência local incluída ou benchmark. Ajustar após medição.

VM não torna toda ação segura: contas/sites autenticados no guest continuam expostos ao código executado nele. Container compartilha características do kernel e exige configuração própria; não é substituto automático da VM para código arbitrário. [Docker](https://docs.docker.com/engine/security/), [libvirt](https://libvirt.org/formatdomain.html).

Hyper-V tem requisitos de edição/hardware; não é capacidade garantida em Windows Home. A primeira integração completa do host Windows depende desses requisitos. Não habilitar hipervisor, privilégios ou serviços do sistema implicitamente. [Microsoft Hyper-V](https://learn.microsoft.com/en-us/windows-server/virtualization/hyper-v/get-started/Install-Hyper-V), [noVNC](https://novnc.com/info.html).

**Suporte inicial:** API/frontend em Windows e Linux; computador próprio Linux; conector pessoal Windows 11 x64. Outros sistemas são expansão, com capacidades declaradas. Linux deve ser validado pela CI/ambiente de teste, não apenas pela intenção da documentação.

## 4. Máquina pessoal

Conector Python executa na sessão interativa do usuário, sem administrador/LocalSystem. Interface local futura permite parear, pausar e revogar. Conectar identifica a máquina, mas não concede todos os recursos.

| Recurso | Concessão/garantia inicial |
| --- | --- |
| Arquivos | Raízes reais autorizadas; leitura/escrita distintas; conferir caminhos abertos e links/reparse points. Prefixo textual não basta. |
| Navegador | Perfil Bees separado. Sessão existente só com integração e concessão próprias; não copiar cookies/perfil pessoal implicitamente. |
| App suportado | Adaptador UI Automation com catálogo demonstrado; mostrar qual app e sessão serão usados. |
| Shell/desktop amplo | Concessão separada de alcance equivalente à conta do sistema. Não prometer confinamento por pasta/janela. |
| Sessão bloqueada/UAC | Aguardar usuário. Não automatizar desktop protegido nem elevar privilégios. |

A implementação Windows inicial pode usar Playwright para navegador e UI Automation/pywinauto para apps. Job Objects ajudam a encerrar árvores de processos; não limitam privilégios de arquivo/rede por si só. [Playwright](https://playwright.dev/python/docs/api/class-browsertype#browser-type-launch-persistent-context), [pywinauto](https://pywinauto.readthedocs.io/en/latest/getting_started.html), [Job Objects](https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects), [serviços interativos](https://learn.microsoft.com/en-us/windows/win32/services/interactive-services).

**Limite de garantia:** shell/desktop genérico com internet e sessões pode enviar, comprar ou apagar por caminhos que escapam de ferramentas tipadas. Revisão semântica por IA é complementar. Garantias fortes de destino/operação existem nos adaptadores determinísticos ou em isolamento que aplique a restrição; a interface diferencia isso de concessão ampla. A mesma limitação vale para shell/desktop dentro da VM, inclusive diante de contas autenticadas.

## 5. Autenticação, segredos e pareamento

Decisão para BEES-004/008/016; ainda não implementada no scaffold:

- Uma identidade Bees, senha com Argon2id e sessões opacas revogáveis. Primeiro acesso usa bootstrap de uso único emitido no terminal.
- Cookie HttpOnly/SameSite e Secure sob HTTPS; mutações com proteção CSRF; validar Host/Origin, inclusive no acesso local.
- Loopback por padrão. Acesso remoto exige autenticação e HTTPS/rede privada apropriada; nenhuma hospedagem SaaS obrigatória.
- Conector/guest tem credencial própria, convite curto de uso único, identidade do serviço visível, revogação e rotação separadas.
- Pareamento não autoriza pasta, shell, navegador ou conta externa.
- SecretStore separado; banco contém referências opacas. Windows: DPAPI no escopo do usuário. Linux/VPS: cofre disponível ou armazenamento criptografado com chave provisionada separadamente e permissões restritas.
- Segredos de modelos ficam no plano de controle; não entram no frontend, VITE_* ou contexto do agente.
- Cookies, perfis e arquivos autenticados do ambiente também são sensíveis, fora da exportação comum.
- Handoff privado suspende screenshots, DOM, tracing e coleta do modelo antes de credenciais; retomar exige ação explícita.
- Cofre/criptografia local não protege contra comprometimento integral da conta do host.

Referências: [OWASP Session Management](https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html), [DPAPI](https://learn.microsoft.com/en-us/windows/win32/api/dpapi/nf-dpapi-cryptprotectdata), [Playwright autenticação](https://playwright.dev/python/docs/auth).

## 6. Protocolo e eventos

- REST /api/v1 para comandos/consultas. Comandos sensíveis usam chave de idempotência e revisão esperada.
- SSE multiplexado para progresso, estado e atenção, com cursor durável, deduplicação e refetch se a retenção do cursor expirar. Não é fila de execução.
- WebSocket autenticado para conexão de saída dos executores. Transporte visual é separado do SSE.
- Envelope de ação versionado: device_id, environment_id, run_id, action_id, tool/version, parâmetros/hash, identidade, policy_revision, lease_generation, deadline e limites.
- Mesmo action_id com parâmetros diferentes é rejeitado. Replay conhecido devolve estado/resultado, sem efeito novo.
- Executor só inicia nova ação com autorização válida/online. Conexão perdida não é permissão implícita.
- Revogação/parada global afetam novas ações; efeito já iniciado pode ter ocorrido. Reportar operações ainda encerrando.
- Um dono por tela: takeover invalida comandos do agente, aguarda reconhecimento e concede lease humano. Queda da conexão humana deixa agente pausado; devolução explícita.
- Tokens do modelo podem ter stream temporário; eventos duráveis são transições/mensagens completas, com limites de payload e frequência.

SSE é unidirecional; WebSocket exige controle de buffers/volume pela aplicação. [SSE](https://developer.mozilla.org/en-US/docs/Web/API/Server-sent_events/Using_server-sent_events), [WebSocket](https://developer.mozilla.org/en-US/docs/Web/API/WebSocket).

## 7. Estado, retomada e rotinas

SQLite por APSW 3.53.4.0 fixado no projeto. Verificar SQLite efetivo >=3.51.3, rejeitando 3.52.0 retirada, antes de abrir estado canônico. Não usar fallback automático para sqlite3.

WAL/FULL/FKs, timeout de lock e transações curtas. Um escritor por vez é esperado; API/worker no mesmo host. Driver encapsulado em repositórios/UnitOfWork. Migrações numeradas/checksum, backup anterior e serviço quiescido. Exportação lógica permite migração futura.

A biblioteca sqlite3 do Python desta máquina carrega 3.50.4; APSW evita trocar DLL global. Wheels oficiais incluem SQLite e suportam Windows/Linux. [APSW instalação](https://rogerbinns.github.io/apsw/install.html), [APSW 3.53.4.0](https://pypi.org/project/apsw/3.53.4.0/).

SQLite documenta um bug WAL corrigido em 3.51.3 e a necessidade de banco em filesystem local. FULL é escolhido para durabilidade do journal. [SQLite WAL](https://sqlite.org/wal.html), [PRAGMA](https://sqlite.org/pragma.html#pragma_synchronous), [releases](https://sqlite.org/changes.html).

- Estados de ação: prepared → awaiting_approval/ready → dispatch_started → confirmed, failed_no_effect ou outcome_unknown.
- Persistir intenção antes do efeito e confirmação/checkpoint na mesma transação.
- Banco/outbox não oferece exactly-once para navegador/sistema externo. Resultado desconhecido exige reconciliação; não é falha segura para repetir.
- Lease persistido com geração/fencing por execução e recurso exclusivo. Expiração não prova que o executor antigo parou.
- Conector tem journal local durável por action_id; deduplicação não elimina a janela entre efeito e registro.
- Eventos/outbox na transação do estado; entrega pelo menos uma vez, consumidores deduplicam.
- Rotina: fuso IANA e instantes UTC; unicidade por rotina/revisão/data. Padrão inicial pula atrasadas/sobrepostas com motivo; catch-up é configuração explícita e limitada.
- Horário inexistente pula a ocorrência; repetido executa uma vez com instante escolhido/documentado.
- Artefatos versionados em staging/ready com hashes; banco e filesystem exigem reconciliação, não transação única.
- Exportação/restauração valida dados e mantém rotinas/credenciais externas sem ativação automática.
- Backup coerente por Backup API/snapshot testado, sem copiar só o arquivo ativo.

[Transações SQLite](https://sqlite.org/lang_transaction.html), [Backup SQLite](https://sqlite.org/backup.html).

## 8. Limites, validação e consequências

Referência inicial revisável: duas tarefas coordenadas, uma ação visual por ambiente, 30 minutos ativos e 50 chamadas de modelo por tarefa; espera por usuário/recurso não consome tempo ativo. Limites por ferramenta/payload desde a primeira execução. Esses números serão medidos, não são promessa de desempenho.

Casos obrigatórios antes de declarar capacidades prontas:

- Troca remoto/local preserva agente, memória e tarefa.
- Fechar frontend/reiniciar serviço preserva trabalho e decisões.
- Parâmetros alterados, revisão revogada, replay e lease antiga não autorizam efeito.
- Queda antes/depois de efeito produz recuperação correta, sem duplicidade cega.
- Caminhos/links fora do escopo e acesso guest ao host/rede privada são bloqueados.
- Takeover impede controle concorrente e sessão bloqueada aguarda humano.
- Segredos não aparecem em logs, contexto, traces ou exportação.
- Relatório e app reais são demonstrados, não simulação de status.

Consequências: duas linguagens e dois tipos de executor, necessidade de virtualização para hospedar o computador próprio, SQLite limitado a um host de controle, conector pessoal Windows inicial e trabalho explícito de retomada. Não introduzir broker, cluster, tenant ou framework de agentes sem necessidade.

## 9. Estado de implementação desta decisão

BEES-001 documenta o desenho; não cria hipervisor, VM, conectores, políticas ou credenciais. BEES-002 prepara API/frontend e ambiente reproduzível. Os demais itens comprovam as capacidades reais antes de completar o MVP.

Nesta máquina foram encontrados Python/Node/uv e WSL. O Docker CLI existe, mas o daemon não estava disponível na inspeção. Nenhum Docker, hipervisor ou serviço do sistema foi iniciado/habilitado para resolver esta ADR.
