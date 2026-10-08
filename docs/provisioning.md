# Autorização e componente de provisionamento

Checkpoint preparatório de BEES-011, referências F21/F23 do briefing. A interface permite revisar um plano de hardware e autorizar sua criação uma vez. A aprovação é persistida; **a aplicação ainda não instala nem inicia uma VM**. O catálogo continua `planned`, com `provisionable: false` e `usable: false`.

O componente nativo é uma biblioteca interna separada do diagnóstico do host. Há transporte autenticado com o core, mas não há launcher de hardware, emissão gráfica de credencial ou supervisor de produção neste checkpoint. Seu comportamento foi verificado com hardware de teste; isso não comprova Hyper-V, isolamento ou disponibilidade de desktop.

## Plano revisável pela interface

Na aba **Computador**, um pedido com hospedeiro confirmado pode receber um plano. A preparação usa o pedido canônico e o catálogo confiável da instalação. Não consulta o modelo nem concede acesso a arquivos pessoais.

O plano fixa instalação, abelha, pedido, job, hospedeiro e suas revisões, recursos, identidade derivada da VM e os hashes/tamanhos do [kit Linux](guest-image.md). Não contém caminhos livres, shell, URL de download, senha, VMID inventado ou credencial administrativa. Os recursos aceitos continuam 1–4 CPUs, 2–8 GiB de RAM e 20–100 GiB de disco.

A autorização cobre somente hardware Hyper-V de geração 2, disco VHDX gerenciado, ISO fixado, sem adaptadores de rede e **sem boot**. Não autoriza instalar componentes do Windows, elevar privilégios, criar rede, iniciar o guest ou executar ferramentas. Essas capacidades exigem integração e decisões próprias.

É preciso conferir o hospedeiro e confirmar a ação concreta. A autorização dura 15 minutos e não cria regra persistente para novas VMs. Pode ser revogada na mesma interface. Revogar bloqueia novos despachos e publicação de resultados; não desfaz um efeito já iniciado nem apaga evidência de resultado desconhecido.

Mudanças no hospedeiro, pedido, abelha, instalação ou catálogo invalidam o contexto. Expiração da autorização permite nova decisão sobre um plano ainda válido. Um plano revogado pode ser substituído por outro plano; esse novo plano exige nova revisão humana. A consulta não renova prazos nem altera o journal.

## API humana

Prefixo: `/api/v1/agents/{agent_id}/environments/{environment_id}/provisioning`.

| Método e sufixo | Resultado |
| --- | --- |
| `GET` | Plano atual, resumo público do host ativo e disponibilidade de preparação. |
| `POST /prepare` | Plano imutável, com revisões esperadas do pedido/host e UUID do comando. |
| `POST /{plan_id}/authorize` | Autorização pontual vinculada ao hash/revisão do plano e UUID. |
| `POST /{plan_id}/revoke` | Revogação da autorização com hash/revisão e UUID. |

Todas as rotas desta seção exigem sessão humana; mutações conservam Host/Origin/CSRF. A verificação do vínculo abelha/pedido/plano ocorre dentro da transação. Replay conserva o estado atual, sem criar efeito ou renovar a autorização. Alterar os dados de um comando já registrado é conflito. Nenhuma dessas rotas emite credencial de executor ou consome `host_jobs`.

`preparation_available` indica possibilidade de planejar com catálogo e host confirmado, não acesso ao Hyper-V, arquivos da imagem presentes ou capacidade de criar VM. Mesmo um plano autorizado continua `usable: false`.

## Transporte próprio do provisionador

O prefixo `/api/v1/provisioner/runtime` exige exatamente um `Authorization: Bearer bp_…`, autenticado no core. Sessão humana, cookie, CSRF ou credencial de diagnóstico `bh_` não substituem essa credencial. Host, Origin, JSON e limite de corpo continuam obrigatórios; o canal não flexibiliza as rotas humanas. Credenciais inválidas recebem 401; conflitos de contexto/autoridade recebem 409, sem detalhes privados.

| Método e sufixo | Operação do core |
| --- | --- |
| `GET /session` | Identidade pública da credencial ativa, sem plano ou segredo. |
| `POST /claim` | Reserva exclusiva de plano autorizado, dono e geração. |
| `POST /current` | Revalidação online do claim antes do próximo passo. |
| `POST /renew` | Renovação limitada e idempotente; não revive claim vencido. |
| `POST /begin` | Intent persistido antes do efeito; replay nunca autoriza outro despacho. |
| `POST /receipt` | Confirmação vinculada ao intent e resultado fechado. |
| `POST /unknown` | Conserva incerteza e quarentena; não repete nem libera o efeito. |

As rotas recebem somente DTOs do core. Não há rota de emissão, bootstrap, shell, recuperação, reconhecimento ou execução de hardware. O serviço registra a autoridade e os receipts; não executa PowerShell nem acessa o hipervisor. O supervisor interno descrito abaixo compõe o executor, mas ainda não existe inscrição/guiada ou launcher operacional para ativá-lo.

`HTTPAuthority` recebe de composição confiável uma origem local, credencial privada e os UUIDs esperados de instalação/host/provisionador. Não recebe esses dados do modelo. Aceita somente loopback; `localhost` é resolvido uma vez, exige endereços loopback e fixa o IP discado, conservando Host/Origin/SNI. HTTPS usa validação padrão de certificado. Não usa proxy do ambiente, redirecionamento ou fallback de origem.

Respostas exigem JSON UTF-8 fechado, até 16 KiB, sem compressão, chaves duplicadas, valores não finitos ou campos extras. O adaptador confere identidade, hash/pins do plano, dono, geração, revisão, estado e UUID do efeito. Não repete automaticamente requisições nem gera novos UUIDs para contornar perda de resposta. Os identificadores de intent e publicação vêm do journal antes da chamada.

Os métodos são síncronos; cada chamada usa uma conexão assíncrona própria sob prazo total de cinco segundos, incluindo cabeçalhos e corpo. O cancelamento fecha a conexão, sem thread de watchdog pendente. Uma chamada dentro de loop assíncrono ativo é recusada. Esse prazo protege o transporte HTTP; não amplia o orçamento do comando nativo nem demonstra renovação supervisionada durante VHDX real.

## Autoridade persistente do core

### Consultar e revogar cadastros pela interface

Na aba **Computador**, abra **Provisionador local → Ver cadastros** no host confirmado. A lista é consultada apenas ao abrir o painel e tem atualização/paginação explícitas. Um cadastro ativo registra uma credencial; não comprova conexão, acesso ao Hyper-V ou VM pronta. Hosts revogados continuam permitindo a gestão de seus cadastros.

**Revogar cadastro** abre uma confirmação humana. A revogação impede novas guardas de efeito; uma operação em voo pode continuar e um resultado desconhecido exige revisão. Não apaga VM, disco, journal ou dados. A interface conserva revisão e UUID da decisão; falha de resposta exige **Atualizar cadastros** antes de prosseguir, sem repetição automática. Mudança de revisão ou cadastro já revogado exige nova revisão humana.

O prefixo `/api/v1/environments/hosts/{host_id}/provisioners` exige sessão humana. `GET` recebe `offset`/`limit` e retorna somente identificador do provisionador, instalação, host, revisão e estado, com indicação da próxima página. `POST /{provisioner_id}/revoke` recebe revisão esperada e UUID idempotente. O vínculo host/instalação é verificado antes de replay; mutações mantêm Host/Origin/CSRF. Segredo, hash da credencial, IDs privados e caminhos não são expostos. Credenciais `bp_`/`bh_` não autenticam essas rotas.

A emissão permanece privada, sem botão de cadastro ou endpoint de bootstrap. Fechar o painel cancela requisições locais; não desfaz uma revogação já confirmada pelo serviço. O gerenciamento usa as tabelas existentes do schema8.

A migração `0008_provisioning.sql` acrescenta planos imutáveis, autorizações, credenciais próprias, gerações por host, claims exclusivos, intents/receipts e comandos idempotentes. Esses registros são canônicos, não cache. A migração exige escritores parados e backup, conforme [persistência](persistence.md).

A credencial `bp_` do provisionador é distinta de `bh_`, usada apenas para diagnóstico. Sua emissão é operação privada do operador, sem rota pública neste checkpoint. SQLite guarda somente o hash; o segredo retornado uma vez não entra em plano, eventos, contexto do modelo ou argumentos de processo.

O claim reserva o host por 60 segundos, com geração monotônica e dono próprio. Renovação exige contexto atual, não revive lease vencida e tem duração total máxima de cinco minutos. A aprovação só é consumida atomicamente no primeiro intent de efeito, junto da transição do pedido/job; reservar um claim não a consome.

Cada operação exige o resultado confirmado da anterior, mesmo vínculo de claim/dono/geração e nova reavaliação de credencial, host, diagnóstico recente, contexto e autorização. A sequência fechada é `create_vhd`, `create_vm`, `configure_vm`, `remove_nic`, `attach_iso`, `verify`. Nenhum replay recebe permissão para repetir o efeito.

O VMID físico vem de `New-VM`, que não aceita um parâmetro `-Id`. O identificador passa a ser obrigatório e constante nos receipts seguintes. A verificação final registra recursos, VM parada, ausência de rede e ISO esperado. Mesmo esse receipt mantém o ambiente em `provisioning`, sem declarar desktop pronto. [Contrato oficial de New-VM](https://learn.microsoft.com/en-us/powershell/module/hyper-v/new-vm?view=windowsserver2025-ps).

Lease vencida sem intent pode ser abortada. Havendo despacho sem conclusão, o estado passa a `outcome_unknown`, bloqueando novos efeitos na instalação individual. Revogar o host e parear outro UUID não contorna essa quarentena. Reconhecer o risco não comprova encerramento do executor anterior e não libera o bloqueio. Reconciliação/recuperação são internas; não há rota de retry, exclusão do journal ou declaração arbitrária de VM pronta.

## Componente nativo fechado

### Inscrição local privada e verificação offline

`EnrollmentStore` armazena a credencial própria `bp_` em estado separado do diagnóstico `bh_`. É um componente interno: ainda não possui inscrição guiada, launcher ou comando de ativação. A composição confiável deve escolher a raiz fixa e fornecer origem e UUIDs esperados da instalação, host, provisionador e emissão. O conteúdo do bootstrap não escolhe caminhos ou concede autoridade para executar planos.

O bootstrap é JSON privado fechado de até 8 KiB, com expiração de até 15 minutos, origem loopback canônica e credencial protegida no objeto em memória. A validação da origem é sintática, sem DNS ou conexão. UUIDs nulos, campos extras/duplicados, números não finitos, credencial de diagnóstico, expiração ou vínculo diferente são recusados. A expiração é conferida novamente antes da remoção do bootstrap.

A inicialização aceita somente uma raiz nova, com ancestral privado. Cria marcador imutável e lock nativo, grava a cifra provisória com fsync e confere a leitura antes de apagar o bootstrap. Confirma sua ausência antes de publicar a cifra final e remover o estágio. Windows exige DPAPI CurrentUser; Linux exige chave Fernet externa explícita. Não há fallback em texto aberto, troca de origem, rotação ou sobrescrita de credencial existente.

`open()`, `load()` e `check_only()` exigem o conjunto final exato, lock exclusivo, arquivos privados sem links/hardlinks e marcador compatível com o conteúdo cifrado e vínculo esperado. Não criam, corrigem permissões, completam estágio ou reconstroem estado ausente. Crash com estágio presente conserva a evidência e impede abertura, mesmo depois de apagar o bootstrap. Segredo emitido e perdido exige revogação humana e nova inscrição futura; não é recuperado por replay da emissão.

`check_only()` permanece offline e sem escrita; retorna somente IDs e `configured_local: true`. Isso comprova integridade local, não credencial ativa, conexão, privilégios, Hyper-V ou VM pronta. Só uma composição posterior poderá carregar o `SecretStr` e consultar a sessão runtime autenticada. A consulta continua sujeita a revogação pelo core.

O formato local 1 é independente do schema 8 canônico e dos journals de ações. A exclusão de toda a raiz e de todos os marcadores elimina evidência local de uso: ausência não deve ser usada pela composição para autorizar nova inicialização. Raiz única, entrega guiada, aquisição durável do claim e recuperação operacional ainda precisam ser integradas. Nenhuma inscrição real é feita automaticamente na instalação padrão.

### Operações nativas

`apps/host/src/bees_host/provisioning` reúne contratos, verificação local do kit, journal, runner e script PowerShell fixo. Não recebe texto livre para executar. O PowerShell usa caminho absoluto do sistema, ambiente mínimo e janela oculta; o processo não recebe credenciais por argumentos/stdin. Ausência de privilégios ou componentes encerra a operação. Não há UAC, instalação de Hyper-V ou alteração de registro/rede automática.

O kit precisa estar previamente copiado em área privada do operador, com hashes/tamanhos fixados. O componente não baixa imagens. O hardware usa um layout derivado do plano. Dados privados de controle exigem ACL da conta atual; diretórios/discos usados pelo VMMS precisam de ACL própria compatível com SYSTEM, Administrators e o acesso específico da VM. Essa fronteira ainda exige prova com Hyper-V real.

O journal local é separado do banco do core, com inicialização explícita, marcador de identidade, DELETE/FULL e lock nativo. Perda, corrupção ou estado parcial falham restritivamente; abrir estado ausente não o recria. PID e instante de criação do filho são gravados antes de enviar `GO`; o filho aguarda esse sinal antes do efeito. Há nova guarda do core imediatamente antes de `GO` e da publicação do receipt.

As guardas Windows verificam contenção de caminho, reparse points, hardlinks e leitura/escrita por identidades estrangeiras antes das mutações. A consulta nativa de arquivo usa P/Invoke fixo em memória, sem compilador, DLL temporária ou escrita no diretório do sistema. Os testes executam essas guardas somente em arquivos descartáveis; não carregam Hyper-V nem comprovam a ACL criada pelo VMMS em uma VM real.

Uma interrupção mantém evidências e impede repetição. A reconciliação consulta inventário gerenciado sem limpar `unknown`; antes dela, o componente precisa comprovar que o processo anterior não continua em execução. PID reutilizado não é tratado como o mesmo processo. A recuperação operacional com revisão humana continua pendente; não existe comando de reset ou liberação automática.

O orçamento atual de comando é 30 segundos, exigindo pelo menos 35 segundos de lease disponível antes de `GO`. Criar um VHDX fixo de 20–100 GiB pode exceder esse tempo. Antes de executar hardware real, é necessário medir a duração; timeout conserva `unknown`, sem tentar criar o disco/VM novamente. A renovação supervisionada não amplia esse orçamento.

## Supervisor exclusivo e parada

`Supervisor` é uma composição interna de `Runner`, autoridade autenticada, kit local e `SupervisorStore`. Não possui CLI, busca automática de jobs, emissão de segredo ou endpoint de ativação. A composição confiável futura deve escolher uma única raiz fixa do ledger por instalação/host; criar outro diretório para contornar quarentena não é recuperação suportada. O helper diagnóstico empacotado continua separado.

O ledger local usa SQLite stdlib, DELETE/FULL, marcador próprio, schema fechado e inicialização explícita em diretório privado. Abrir usa `mode=rw`; perda, corrupção, arquivo extra ou estado parcial falham restritivamente. Guarda IDs/vínculos/revisões/prazos e UUIDs dos pedidos, sem plano integral, caminho do kit ou credencial. Não substitui o journal canônico do core nem altera seu schema8; o journal de operações por plano também permanece no formato1.

Durante toda a sequência, o supervisor mantém o lock nativo do host e o lock do journal do plano. Reserva a corrida no ledger antes de iniciar qualquer efeito. Cada claim pode ser usado uma única vez; uma corrida anterior interrompida, desconhecida ou ainda marcada como ativa bloqueia novos efeitos. Mesmo uma sequência parcialmente confirmada não é retomada automaticamente após crash. Não há API local de reconhecimento ou limpeza dessa quarentena neste checkpoint.

Renovações recebem UUID durável antes da rede, preservando o mesmo claim, dono, geração, plano e host. Há consulta online antes do intent, depois de `READY`, antes de `GO`, durante a espera e antes do receipt. Na espera, a consulta é realizada a cada cinco segundos; lease com até 45 segundos restantes exige renovação, que precisa deixar mais de 35 segundos disponíveis. O teto monotônico local é cinco minutos; a autoridade conserva seu próprio teto contado desde a criação do claim e a expiração da aprovação. Lease vencida não é renovada.

O processo é observado no thread dono, usando leitura não bloqueante de pipe no Windows/POSIX; não existem threads leitoras ou de renovação que sobrevivam ao fechamento. O prazo do comando inclui saída, EOF, término do filho e execução das guardas. A guarda usa chamadas HTTP individualmente limitadas a cinco segundos; uma consulta seguida de renovação pode consumir dois desses prazos. O vencimento é conferido antes e depois do callback síncrono, que não é preemptível. Parada/revogação/perda de conexão encerra somente o filho criado pelo objeto atual e conserva o resultado incerto. Encerrar PowerShell **não comprova que VMMS cancelou seu efeito**.

A quarentena local é gravada antes da tentativa de avisar `/unknown`. Um pedido de renovação sem resposta permanece pendente; um pedido distinto de `unknown` pode ser registrado sem apagar essa evidência. Nenhum deles recebe retry automático. Credencial revogada, serviço indisponível ou receipt já confirmado podem impedir o aviso, mantendo o bloqueio local e exigindo recuperação privada do core. Uma confirmação canônica já gravada nunca é substituída para justificar novo despacho.

`reconcile()` conserva os estados e consulta somente o inventário depois de verificar PID/instante de criação do executor anterior. Processo ainda ativo ou quiescência não comprovada impedem a consulta. `matched` não concede lease, não libera o ledger e não repete efeito. Hashes do kit e inspeções de preflight continuam podendo consumir tempo antes do intent; sem lease atual suficiente, o processo falha antes de iniciar o efeito.

`New-VM` cria um adaptador mesmo sem `SwitchName`. O script verifica que ele não está conectado e o remove no escopo da VM criada antes da verificação final. Nenhuma operação chama `Start-VM`. [Comportamento oficial de New-VM](https://learn.microsoft.com/en-us/powershell/module/hyper-v/new-vm?view=windowsserver2025-ps).

## Aceite restante

- Inscrição privada/guiada, raiz única de operação, composição com launcher e pacote atualizado; o supervisor interno e o transporte HTTP já estão implementados, mas o helper empacotado anterior continua diagnóstico.
- Verificação do script/ACL/VMID e falhas com Hyper-V real, depois de autorização concreta para os componentes necessários do Windows.
- Instalação humana do Debian/kit, integração da ponte no guest e persistência após reinício.
- Rede filtrada e provas de isolamento externas ao guest antes de habilitar conectividade.
- Desktop, transporte Hyper-V sockets físico e integração das ferramentas/limites reais.

Testes cruzados em `scripts/tests/test_provisioning_interoperability.py` usam core, runner, SQLite e locks reais, com backend de hardware falso: sequência completa, revogação enquanto o filho aguarda `GO`, perda da resposta após confirmação canônica e expiração de lease depois do efeito. Nenhum teste dessa fixture cria VM ou demonstra isolamento.

## Evidências dos checkpoints — 07–08/10/2026

A suíte completa Windows passou com 1.207 testes e 27 skips; a validação Linux de core, host, guest e APIs afetadas passou com 936 testes e 17 skips. Depois da guarda adicional de leitura por SID estrangeiro, os 60 testes de provisionamento/interoperabilidade passaram no Windows, e 55 no Linux com cinco skips exclusivos do Windows. Esses números descrevem execuções separadas, não devem ser somados como cobertura única.

A interface passou em 404 testes, lint, tipos e build. Uma instalação descartável comprovou revisão humana, autorização, persistência após reload, revogação, traduções e viewport de 390 px sem overflow. A imagem Docker passou no fluxo de primeiro acesso, cofre, conversa, worker separado, políticas, ferramentas, pedidos de computador e recriação com persistência. O wheel do host inclui o script PowerShell exato; o executável de diagnóstico anterior não foi substituído.

Ruff, formatação, lock de dependências e diff foram conferidos. Os testes não usaram credenciais reais de modelo, não criaram VM, não habilitaram Hyper-V e não alteraram redes do hospedeiro. A aprovação na interface é evidência de autorização persistente, não de execução física.

A instalação local foi atualizada pelo launcher, com API/worker parados durante a migração 7→8 e backup automático. API e worker ficaram saudáveis; integridade e chaves estrangeiras passaram, preservando identidade, abelha, conversa, mensagens e cofre existentes. Nenhuma identidade de teste foi criada na instalação padrão.

O checkpoint seguinte acrescentou o transporte HTTP: 1.309 testes passaram na suíte Windows completa (27 skips), e 1.037 na validação Linux de core, host, guest, APIs afetadas e interoperabilidade (18 skips). Inclui 72 testes do adaptador, 19 da API runtime e dez de HTTP real: Uvicorn em subprocesso próprio, execução do runner com hardware falso, revogação antes de GO, falha HTTP após receipt já confirmado, perda do serviço, replay/renovação/dono/unknown e slowdrip nos cabeçalhos/corpo. As guardas de UUID exato, estado/revisão monotônicos e prazo total foram revisadas independentemente. Imagens e wheel foram reconstruídos, com fontes do transporte/script exatas; não foi ativado um executável de provisionamento.

O supervisor interno passou na suíte Windows completa com 1.427 testes e 27 skips. A suíte Linux afetada de host/guest/core/API/HTTP passou com 521 testes e 14 skips; após a guarda de quiescência dos processos registrados, os 30 testes de runner/supervisor passaram novamente em ambos os sistemas. A revisão independente conferiu parada antes de GO e o aviso de unknown depois de renovação perdida. Os sete cenários novos de HTTP usam serviço em subprocesso próprio, inclusive resposta503 depois do commit de renovação ou receipt, mantendo efeitos incertos sem repetição. Os testes nativos de pipe/EOF/filho executam somente processos próprios e arquivos descartáveis.

Ruff, formatação, diff e regras do gitignore passaram. A imagem de verificação foi reconstruída; o wheel contém as cinco fontes exatas de supervisor, ledger, backend, runner e script. A interface não mudou neste checkpoint, mantendo a validação anterior de 433 testes. API/worker da instalação padrão continuaram saudáveis, sem alteração de schema/dados/cofre ou emissão de bp_. O executável diagnóstico anterior não foi substituído, e nenhum hipervisor, VM ou rede foi ativado.

O gerenciamento humano de cadastros passou com 1.452 testes na suíte Windows completa (27 skips), 90 testes afetados no Linux e 465 testes da interface, além de lint, tipos, build e Ruff. Casos negativos incluem credenciais runtime sem sessão humana, Host/Origin/CSRF, host errado, revisão obsoleta, replay, paginação, host revogado e preservação de claims/efeitos em voo ou desconhecidos. A revisão independente corrigiu e conferiu o vínculo de instalação/host.

Na instalação descartável, a revogação persistiu após recarregar; português, inglês, espanhol e viewport de 390 px passaram, sem rolagem horizontal. Servidor e navegador próprios foram encerrados. A instalação padrão foi atualizada pelo launcher: API/worker saudáveis, integridade/FK aprovadas e hashes dos dados anteriores e do cofre preservados. O schema permanece8; nenhum cadastro bp_, claim ou efeito de hardware foi criado na instalação padrão. As imagens de aplicação e de verificação foram reconstruídas. O pacote de diagnóstico continua sem ativação do provisionador.

A inscrição privada local passou em 109 testes nativos Windows (um skip), cinco testes de interoperabilidade e revisão independente. A validação Linux de host, guest, core e APIs afetadas e interoperabilidade passou com 650 testes e 16 skips. Inclui DPAPI/Fernet reais, arquivos/ACL/locks nativos, subprocessos interrompidos após estágio ou publicação, concorrência por um bootstrap, vínculo/cifra trocados, relógio/expiração antes da remoção e perda de entrega sem reemissão. A prova integrada usa emissão privada, API/core reais e consulta de sessão/revogação, sem claims, jobs ou efeitos de hardware.

A suíte completa Windows passou com 1.566 testes e 28 skips, incluindo o executável diagnóstico anterior nos testes opt-in. A imagem de verificação foi reconstruída. O wheel contém as sete fontes exatas de inscrição, supervisor, ledger, transporte, runner, backend e PowerShell. Ruff, formatação, diff, referências locais e regras do gitignore passaram. O executável diagnóstico anterior não foi substituído. A interface permanece na validação anterior de 465 testes. A instalação padrão segue saudável; este componente interno não alterou banco, schema, cofre ou cadastro real e não possui ativação automática.
