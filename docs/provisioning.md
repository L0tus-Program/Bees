# Autorização e componente de provisionamento

Checkpoint preparatório de BEES-011, referências F21/F23 do briefing. A interface permite revisar um plano de hardware e autorizar sua criação uma vez. A aprovação é persistida; **a aplicação ainda não instala nem inicia uma VM**. O catálogo continua `planned`, com `provisionable: false` e `usable: false`.

O componente nativo é uma biblioteca interna separada do diagnóstico do host. Transporte autenticado, supervisor e composição sob raiz fixa estão implementados; inscrição guiada, preparo de assets/workspace e launcher/pacote operacional continuam pendentes. Seu comportamento foi verificado com hardware de teste; isso não comprova Hyper-V, isolamento ou disponibilidade de desktop.

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

No Windows, consumir o bootstrap exige um handle exclusivo de leitura/remoção, sem compartilhamento. Identidade, bytes, tamanho, links e permissões são revalidados pelo mesmo handle; o prazo é conferido depois dessas guardas, imediatamente antes de marcar a remoção. O handle é fechado antes de conferir ausência e selar a inscrição. Duas raízes concorrentes não podem consumir o mesmo arquivo. Falha ou crash conserva o estágio e impede completar a inscrição automaticamente. No POSIX, o consumo conserva a remoção atômica existente. Referências: [CreateFileW](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-createfilew) e [SetFileInformationByHandle](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-setfileinformationbyhandle).

`open()`, `load()` e `check_only()` exigem o conjunto final exato, lock exclusivo, arquivos privados sem links/hardlinks e marcador compatível com o conteúdo cifrado e vínculo esperado. Não criam, corrigem permissões, completam estágio ou reconstroem estado ausente. Crash com estágio presente conserva a evidência e impede abertura, mesmo depois de apagar o bootstrap. Segredo emitido e perdido exige revogação humana e nova inscrição futura; não é recuperado por replay da emissão.

`check_only()` permanece offline e sem escrita; retorna somente IDs e `configured_local: true`. Isso comprova integridade local, não credencial ativa, conexão, privilégios, Hyper-V ou VM pronta. Só uma composição posterior poderá carregar o `SecretStr` e consultar a sessão runtime autenticada. A consulta continua sujeita a revogação pelo core.

O formato local 1 da inscrição é independente do schema 8 canônico e dos journals de ações. A exclusão de toda a raiz e de todos os marcadores elimina evidência local de uso: ausência não deve ser usada pela composição para autorizar nova inicialização. A aquisição durável descrita abaixo já integra inscrição, transporte e supervisor como biblioteca interna. A raiz fixa offline descrita a seguir prepara seu estado; integração de produção, entrega guiada e recuperação operacional continuam pendentes. Nenhuma inscrição real é feita automaticamente na instalação padrão.

### Raiz fixa e consulta sem escrita

`RootStore` é um componente interno offline. No Windows, consulta `FOLDERID_LocalAppData` pela API nativa da conta atual e exige volume fixo local; UNC, caminhos de dispositivo, drives mapeados e volumes removíveis são recusados antes de acesso aos arquivos. No Linux, usa o diretório da conta em `pwd.getpwuid`, independente de `HOME`. Ambos derivam a pasta constante `bees-provisioner`; diretório atual, checkout, executável, porta e variáveis de caminho não escolhem outra raiz. A referência Windows é [Known Folders](https://learn.microsoft.com/en-us/windows/win32/shell/known-folders) e [SHGetKnownFolderPath](https://learn.microsoft.com/en-us/windows/win32/api/shlobj_core/nf-shlobj_core-shgetknownfolderpath).

`initialize()` recebe vínculo e bootstrap privados de composição humana confiável. Valida formato, identidade e prazo antes de criar a âncora. A criação explícita exige a raiz ausente, preserva exclusividade e não é chamada por `open()` ou `check_only()`. Antes de qualquer filho, grava marcador de identidade e lock. Inicializa `state/enrollment`, ledger versão2 em `state/ledger` e diretório privado `state/plans`. Publica por último `composition.json` fora de `state`, fixando os IDs originais dos dois stores e o mesmo vínculo.

Qualquer estado parcial, perda de filho, arquivo extra, link, troca de origem/vínculo/cifra ou substituição por stores novos válidos é recusado. A âncora permanece quando `state` é perdida; inicializar de novo não repara essa instalação. Apagar a âncora inteira elimina evidências locais, e restaurar um backup antigo com os mesmos IDs não tem proteção contra rollback demonstrada. Essa raiz não isola código da mesma conta, administradores ou outras contas que obtenham autoridade própria no core. Ausência não concede nova inscrição.

`check_only()` mantém os locks nativos, verifica ACL/ancestrais/arquivos e vínculos, decifra a inscrição local e abre o ledger com `mode=ro`. Não usa `immutable=1`. Recusa rollback journal e outros arquivos auxiliares, além de header SQLite em modo WAL, antes da conexão; não recupera journal, migra ou cria sidecars. Valida schema/histórico/integridade existentes e projeta somente IDs locais, `configured_local` e `execution_blocked_local`. Aquisições/corridas não concluídas e pedidos pendentes bloqueiam execução; a consulta não cria ticket, claim, dono ou UUID.

A consulta não usa HTTP, transporte próprio ou subprocessos. A resolução nativa da conta depende da configuração do sistema: no Linux, a base `passwd` é atendida por [módulos NSS](https://sourceware.org/glibc/manual/2.43/html_node/NSS-Basics.html), que podem consultar serviços externos. A prova de somente leitura cobre os arquivos do Bees; não demonstra ausência global de tráfego do sistema operacional.

`state/plans` admite somente diretórios privados nomeados pelo UUID canônico do plano, associados ao inventário completo do ledger. A associação confere inscrição, emissão, origem, provisionador, plano/hash, claim, dono e geração; não usa o limite de paginação do histórico. Corridas legadas sem aquisição não recebem associação retroativa com a raiz nova.

Aquisição sem claim aceito não admite journal. Após aceitação, antes de registrar a corrida, o journal pode estar ausente ou íntegro e vazio: é uma interrupção possível da sequência, sempre bloqueada, sem adoção, reparo ou retomada. Diretório parcial é recusado. Corrida registrada exige seu journal; perda, troca, órfão, identidade de journal repetida, nome inválido ou vínculo diferente falham restritivamente. Revisões e leases do journal podem avançar em ordem diferente das confirmações do ledger; a comparação usa o snapshot aceito como piso, sem consultar ou renovar a autoridade. A mesma revisão exige os metadados originais. O UUID do efeito corresponde ao pedido de begin, conforme o transporte próprio.

`Journal.open_read_only()` conserva o formato1, abre SQLite com `mode=ro`, verifica schema/integridade/identidade/operações e recusa sidecars ou header WAL antes da conexão. Mutações são recusadas. Os locks seguem raiz → ledger → journal. A consulta não chama runner, inspeção de hardware ou reconciliação; não altera PID, resultados, estado desconhecido ou bloqueio. Uma corrida marcada como concluída exige a sequência inteira confirmada, claim final confirmado e revisão/lease que alcancem o último registro do run; isso verifica coerência local, não comprova hardware físico. Uma corrida stopped não admite operações; unknown com todos os receipts conserva bloqueio. O resultado público de `RootCheck` continua limitado aos mesmos IDs e indicadores.

O `ProvisionerRuntime` descrito abaixo integra a raiz com aquisição, kit e backend como biblioteca interna. Launcher, CLI operacional, emissão gráfica e preparo humano da área VMMS continuam pendentes. Configuração local íntegra não comprova credencial ativa, conexão, permissão vigente, hardware disponível ou VM pronta. O helper diagnóstico empacotado continua sem ativação do provisionador.

### Composição operacional interna

`ProvisionerRuntime.open(binding=..., cipher=...)` deriva os caminhos da conta e verifica o estado local. Seus argumentos não incluem diretório, executor, template ou autoridade alternativos. Abrir não inicializa estado, instancia backend, verifica disponibilidade do Hyper-V, inspeciona hardware ou usa HTTP. `run(plan, cancelled=...)` exige um plano fechado explícito, da mesma instalação/host, e mantém locks na ordem raiz → assets → workspace → ledger → journal durante a aquisição e toda a sequência, incluindo fechamento dos comandos próprios e publicação de unknown. A instância também recusa execução concorrente. Plano já registrado é recusado antes de construir backend/template, inclusive depois de concluir e reabrir.

A abertura operacional do ledger usa `SupervisorStore.open_execution_locked`: adquire o lock nativo antes da conexão SQLite RW, exige versão2 e recusa arquivos auxiliares e header WAL antes e depois de conectar, antes da primeira consulta. Não migra ou recupera rollback journal. A raiz é revalidada com esse mesmo ledger travado; `Acquisition._run_locked` e supervisor compartilham seu dono/epoch, sem destravar durante o handoff. Tickets de abertura anterior continuam inválidos. Quarentena global, cancelamento, vínculo errado, kit ausente/inválido e áreas parciais impedem iniciar uma nova aquisição. Nenhuma operação altera a autoridade pontual exigida pelo core.

O controle privado `bees-provisioner` permanece separado dos irmãos fixos `bees-provisioner-assets` e `bees-provisioner-hardware`. A área assets contém identidade/lock privados e `kit`, exclusivo da conta; não contém credenciais. `AssetsBinding` repete os IDs originais da raiz, inscrição, ledger, instalação, host e provisionador. IDs de assets/kit/workspace derivam por UUIDv5 do root_id e de literais layout1 e são conferidos no marcador fechado. Identificam a topologia, não uma nova geração: não demonstram proteção contra restauração de backup ou recriação idêntica pela mesma conta.

`initialize_assets` é preparação explícita sob lock da raiz e do ledger e exige ausência de qualquer aquisição/corrida anterior, inclusive concluída. Cria somente assets com kit vazio e exige o irmão hardware ausente; não adota área existente, copia imagem, baixa arquivos ou concede ACL. `open` e `run` nunca chamam essa inicialização. A inserção validada do kit e o preparo humano da área VMMS serão integrados pela entrega guiada posterior.

O kit operacional aceita vazio para configuração local, ou o conjunto exato de nove arquivos do builder: ISO Debian, payload, inventário, kit-info, README, SHA256SUMS oficial, assinatura, chave Debian CD e SHA256SUMS.json. Kit parcial, extra, link, hardlink ou acesso estrangeiro falha restritivamente. Antes do primeiro HTTP, `LocalTemplate.verify` confere a receita e os hashes/tamanhos fixados dos artefatos usados; a presença dos nove nomes isoladamente não valida uma imagem. Não há download ou busca automática de template.

O workspace hardware deve existir com marcador e lock vinculados à mesma topologia; este componente não o prepara. No Windows, a guarda readonly exige volume fixo, dono atual e DACL protegida exata com FullControl para a conta, SYSTEM e Administrators, sem outros SIDs na raiz. Recusa ancestral/reparse/hardlink e não corrige permissões nem solicita UAC. Subdiretórios aceitam somente UUIDs canônicos associados ao inventário integral do ledger com corrida registrada; órfãos são recusados. As guardas específicas do PowerShell conferem dados e ACL por plano com o VMID físico correspondente. A guarda Linux serve à validação dos arquivos/locks; o backend Hyper-V continua exclusivo do Windows.

O ISO permanece privado no kit. O comando fixo de `attach_iso`, executado pela conta local após intent autorizado, copia-o para `installer.iso` no diretório hardware do plano e confere o hash antes de anexar. Credenciais, bancos e journals não são colocados nessa área. A separação e a verificação de ACL não comprovam traversal/acesso real do VMMS, criação de VHDX no orçamento, VM física ou isolamento. Não há bootstrap de produção, polling de jobs, recovery, ativação do provisionador no helper anterior, `Start-VM`, habilitação do hipervisor ou alteração de rede.

### Aquisição durável antes da rede

`Acquisition` recebe um plano fechado, inscrição, ledger versão2, kit e backend de composição confiável. Mantém o mesmo lock nativo do host desde as verificações locais até o encerramento do supervisor. Antes de reservar no core, confere vínculo instalação/host, raiz privada dos journals, ausência de journal do plano, preflight somente leitura e integridade do kit. Não descobre jobs ou escolhe caminhos a partir do modelo.

Uma transação grava `request_id` e `owner_id` próprios, hash/ID do plano e vínculo da inscrição/emissão/provisionador/origem antes de construir `HTTPAuthority`, inclusive antes de resolver `localhost`. A chamada de claim ocorre uma única vez. A resposta precisa corresponder ao mesmo plano e dono; sua aceitação é persistida antes de inicializar o journal. A passagem ao supervisor exige um ticket da mesma instância, thread e período de lock. Inserir a corrida e mudar a aquisição para `running` é atômico; a conclusão também altera os dois registros juntos.

Falha após reservar localmente conserva `prepared`, `accepted`, `running`, `stopped` ou `unknown`, impedindo outra tentativa mesmo depois de reabrir o banco. Nenhuma resposta perdida troca dono, cria UUID novo, reenvia claim ou adota uma aquisição antiga. O ticket em memória não é reconstruído pelos IDs persistidos. A confirmação, renovação e aviso do supervisor também exigem essa associação viva. Falha de transação invalida o ticket em memória; o estado durável segue sendo a guarda.

Antes de intent de hardware, a incerteza é conservada localmente: não se envia `/unknown` para inventar um efeito de VM. Se o core reservou o claim e a resposta se perdeu, seu registro canônico permanece e a autorização ainda não foi consumida. Revogação ou cancelamento não apagam evidências nem liberam a tentativa. Depois do primeiro intent, o supervisor conserva as guardas e o aviso de resultado desconhecido já descritos abaixo. Não existe retomada, reset ou recuperação operacional pública.

O ledger versão2 acrescenta a tabela de aquisições; schema8 do core, formato1 da inscrição, marcador do ledger e journal por plano permanecem intactos. `initialize()` continua criando ledger versão1 para o componente legado; `initialize_for_acquisition()` cria versão2 explicitamente. `open()` valida ambas, sem migrar. A nova composição recusa versão1. `migrate_for_acquisition()` exige o lock, nenhuma corrida incompleta e nenhum pedido de rede pendente. Atualiza DDL e `user_version` em uma transação, preservando marcador e histórico concluído, sem fabricar aquisições retroativas.

Os testes de interrupção da migração demonstram uma limitação concreta no Windows: um crash antes de COMMIT pode deixar o rollback journal do SQLite sem DACL protegida. A abertura preserva o arquivo e recusa acesso com `provision_private_required`; não corrige ACL, remove journal ou reinicializa o ledger. No Linux, o journal privado permite o rollback transacional do SQLite para versão1. Interrupção após COMMIT preserva versão2 e os registros. Recuperação dessa falha no Windows permanece operação futura com revisão humana.

### Operações nativas

`apps/host/src/bees_host/provisioning` reúne contratos, verificação local do kit, journal, runner e script PowerShell fixo. Não recebe texto livre para executar. O PowerShell usa caminho absoluto do sistema, ambiente mínimo e janela oculta; o processo não recebe credenciais por argumentos/stdin. Ausência de privilégios ou componentes encerra a operação. Não há UAC, instalação de Hyper-V ou alteração de registro/rede automática.

O kit precisa estar previamente copiado em área privada do operador, com hashes/tamanhos fixados. O componente não baixa imagens. O hardware usa um layout derivado do plano. Dados privados de controle exigem ACL da conta atual; diretórios/discos usados pelo VMMS precisam de ACL própria compatível com SYSTEM, Administrators e o acesso específico da VM. Essa fronteira ainda exige prova com Hyper-V real.

O journal local é separado do banco do core, com inicialização explícita, marcador de identidade, DELETE/FULL e lock nativo. Perda, corrupção ou estado parcial falham restritivamente; abrir estado ausente não o recria. PID e instante de criação do filho são gravados antes de enviar `GO`; o filho aguarda esse sinal antes do efeito. Há nova guarda do core imediatamente antes de `GO` e da publicação do receipt.

As guardas Windows verificam contenção de caminho, reparse points, hardlinks e leitura/escrita por identidades estrangeiras antes das mutações. A consulta nativa de arquivo usa P/Invoke fixo em memória, sem compilador, DLL temporária ou escrita no diretório do sistema. Os testes executam essas guardas somente em arquivos descartáveis; não carregam Hyper-V nem comprovam a ACL criada pelo VMMS em uma VM real.

Uma interrupção mantém evidências e impede repetição. A reconciliação consulta inventário gerenciado sem limpar `unknown`; antes dela, o componente precisa comprovar que o processo anterior não continua em execução. PID reutilizado não é tratado como o mesmo processo. A recuperação operacional com revisão humana continua pendente; não existe comando de reset ou liberação automática.

O orçamento atual de comando é 30 segundos, exigindo pelo menos 35 segundos de lease disponível antes de `GO`. Criar um VHDX fixo de 20–100 GiB pode exceder esse tempo. Antes de executar hardware real, é necessário medir a duração; timeout conserva `unknown`, sem tentar criar o disco/VM novamente. A renovação supervisionada não amplia esse orçamento.

## Supervisor exclusivo e parada

`Supervisor` é uma composição interna de `Runner`, autoridade autenticada, kit local e `SupervisorStore`. Não possui CLI, busca automática de jobs, emissão de segredo ou endpoint de ativação. `ProvisionerRuntime` usa o ledger vinculado à raiz fixa de `RootStore`; criar outro diretório para contornar quarentena não é recuperação suportada. O helper diagnóstico empacotado continua separado.

O ledger local usa SQLite stdlib, DELETE/FULL, marcador próprio, schema fechado e inicialização explícita em diretório privado. Abrir usa `mode=rw`; perda, corrupção, arquivo extra ou estado parcial falham restritivamente. Guarda IDs/vínculos/revisões/prazos e UUIDs dos pedidos, sem plano integral, caminho do kit ou credencial. Não substitui o journal canônico do core nem altera seu schema8; o journal de operações por plano também permanece no formato1.

Os carimbos locais de criação/atualização conservam a ordem das linhas diretamente relacionadas se o relógio do sistema recuar. Podem empatar até o relógio alcançar o último carimbo persistido; por isso não representam necessariamente o instante físico exato. Isso não altera a lease recebida do core, a expiração da autorização ou os prazos monotônicos de execução. Os validadores continuam recusando datas inconsistentes gravadas anteriormente; abrir não repara histórico.

Durante toda a sequência, o supervisor mantém o lock nativo do host e o lock do journal do plano. Reserva a corrida no ledger antes de iniciar qualquer efeito. Na versão2, só aceita a aquisição viva que já mantém esse lock; a chamada legada sem ticket é recusada. Cada claim pode ser usado uma única vez; uma corrida anterior interrompida, desconhecida ou ainda marcada como ativa bloqueia novos efeitos. Mesmo uma sequência parcialmente confirmada não é retomada automaticamente após crash. Não há API local de reconhecimento ou limpeza dessa quarentena neste checkpoint.

Renovações recebem UUID durável antes da rede, preservando o mesmo claim, dono, geração, plano e host. Há consulta online antes do intent, depois de `READY`, antes de `GO`, durante a espera e antes do receipt. Na espera, a consulta é realizada a cada cinco segundos; lease com até 45 segundos restantes exige renovação, que precisa deixar mais de 35 segundos disponíveis. O teto monotônico local é cinco minutos; a autoridade conserva seu próprio teto contado desde a criação do claim e a expiração da aprovação. Lease vencida não é renovada.

O processo é observado no thread dono, usando leitura não bloqueante de pipe no Windows/POSIX; não existem threads leitoras ou de renovação que sobrevivam ao fechamento. O prazo do comando inclui saída, EOF, término do filho e execução das guardas. A guarda usa chamadas HTTP individualmente limitadas a cinco segundos; uma consulta seguida de renovação pode consumir dois desses prazos. O vencimento é conferido antes e depois do callback síncrono, que não é preemptível. Parada/revogação/perda de conexão encerra somente o filho criado pelo objeto atual e conserva o resultado incerto. Encerrar PowerShell **não comprova que VMMS cancelou seu efeito**.

A quarentena local é gravada antes da tentativa de avisar `/unknown`. Um pedido de renovação sem resposta permanece pendente; um pedido distinto de `unknown` pode ser registrado sem apagar essa evidência. Nenhum deles recebe retry automático. Credencial revogada, serviço indisponível ou receipt já confirmado podem impedir o aviso, mantendo o bloqueio local e exigindo recuperação privada do core. Uma confirmação canônica já gravada nunca é substituída para justificar novo despacho.

`reconcile()` conserva os estados e consulta somente o inventário depois de verificar PID/instante de criação do executor anterior. Processo ainda ativo ou quiescência não comprovada impedem a consulta. `matched` não concede lease, não libera o ledger e não repete efeito. Hashes do kit e inspeções de preflight continuam podendo consumir tempo antes do intent; sem lease atual suficiente, o processo falha antes de iniciar o efeito.

`New-VM` cria um adaptador mesmo sem `SwitchName`. O script verifica que ele não está conectado e o remove no escopo da VM criada antes da verificação final. Nenhuma operação chama `Start-VM`. [Comportamento oficial de New-VM](https://learn.microsoft.com/en-us/powershell/module/hyper-v/new-vm?view=windowsserver2025-ps).

## Aceite restante

- Inscrição guiada, preparo validado do kit/workspace para VMMS, launcher e pacote atualizado; a composição operacional interna está implementada, mas o helper empacotado anterior continua diagnóstico.
- Verificação do script/ACL/VMID e falhas com Hyper-V real, depois de autorização concreta para os componentes necessários do Windows.
- Instalação humana do Debian/kit, integração da ponte no guest e persistência após reinício.
- Rede filtrada e provas de isolamento externas ao guest antes de habilitar conectividade.
- Desktop, transporte Hyper-V sockets físico e integração das ferramentas/limites reais.

Testes cruzados em `scripts/tests/test_provisioning_interoperability.py` usam core, runner, SQLite e locks reais, com backend de hardware falso: sequência completa, revogação enquanto o filho aguarda `GO`, perda da resposta após confirmação canônica e expiração de lease depois do efeito. Nenhum teste dessa fixture cria VM ou demonstra isolamento.

## Evidências dos checkpoints — 07–09/10/2026

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

A aquisição durável passou com 1.654 testes na suíte Windows completa (28 skips), 164 testes afetados e quatro novos cenários HTTP reais, em execuções separadas. Os testes incluem três mortes abruptas de processos próprios nos estados prepared/accepted/running, interrupção da migração antes/depois de COMMIT, falha de transação, troca de thread/instância/ticket, bloqueio depois de reabrir e preservação do histórico legado. O novo interop de interrupção usa KeyboardInterrupt tratado; não representa uma morte abrupta do coordenador inteiro. A perda503 depois do commit de claim comprova dono/UUID únicos, nenhum efeito e autorização não consumida. Hardware continua falso.

A revisão independente não encontrou bloqueadores. Ruff, formatação, lock offline, diff, referências e gitignore passaram; o wheel contém oito fontes exatas, incluindo aquisição. Interface e executável diagnóstico não mudaram. Docker Desktop retornou erro500 durante a tentativa de reinício do usuário, impedindo reconstruir a imagem de verificação. Na continuação, o commit92c1ef6 foi validado em cópia isolada no Ubuntu24.04/WSL, Python3.14.4 e dependências do lock: 747 testes Linux passaram, com 16 skips. Isso resolve a validação POSIX da aquisição; a reconstrução da imagem Docker continua pendente. Não foi alegada atualização/saúde do serviço padrão ou execução física.

A raiz fixa e a abertura somente leitura passaram em 61 testes Windows de raiz (três skips de symlink) e 63 novos testes readonly; 192 testes Windows afetados de ledger/aquisição/supervisor também passaram, em execuções separadas. A revisão independente conferiu bootstrap antes da âncora, volume local antes do filesystem, vínculo/IDs, locks e guardas de journal/WAL antes da conexão SQLite. A validação Linux afetada passou com 866 testes e 24 skips em snapshot das fontes finais no mesmo ambiente isolado WSL. Inclui mortes abruptas de processos próprios em quatro pontos da inscrição, concorrência entre inicializações, swap de stores íntegros, quarentena antiga fora da paginação e preservação de bytes/mtimes durante consulta. A suíte Windows completa passou com 1.778 testes e 31 skips, incluindo os testes opt-in do executável diagnóstico anterior.

Ruff, formato, lock offline, diff, referências locais e gitignore passaram. O wheel contém nove fontes exatas, incluindo raiz fixa e ledger atualizado. A raiz nativa real não foi inicializada pelos testes: usam pastas próprias e monkeypatch de descoberta; o smoke de descoberta nativa apenas consulta o sistema. Não houve emissão real, VM, mudança de rede/hipervisor, atualização de launcher ou GUI. O reinício normal do Docker terminou em timeout; a validação WSL não demonstra que o serviço Docker ou a instalação padrão voltou a funcionar.

O leitor dos journals passou em 64 novos testes Windows, e os 42 testes existentes de journal/runner/supervisor também passaram. O inventário integral passou em oito testes; 138 testes afetados de ledger/aquisição/readonly e 108 de raiz/journals (três skips de symlink Windows) passaram em execuções separadas. Os negativos incluem schema/binding/PID/receipts incompatíveis, identidade duplicada, efeito com UUID diferente, regressão de revisão/lease, operações fora da sequência, WAL sem sidecars e hotjournal após morte abrupta de processo próprio. Leitura e falha conservam bytes/mtimes; nenhuma consulta recupera ou troca unknown.

Os sete testes de interoperabilidade de aquisição passaram, incluindo três novos cenários da raiz fixa com core/API/HTTP/SQLite e cifra reais: conclusão, resposta503 após commit do claim e resposta503 após receipt. A emissão bp_ é descartável e ocorre somente no core de teste; hardware, kit e descoberta da raiz são falsos. Depois de encerrar a API própria, duas consultas locais preservam arquivos e bloqueio, com DNS/conexão/subprocessos proibidos. Não há emissão na instalação padrão nem prova de VM física.

A suíte completa Windows passou com 1.900 testes e 31 skips, incluindo os testes opt-in do executável diagnóstico anterior. A validação Linux afetada passou com 988 testes e 24 skips em snapshot conferido das fontes finais, no runtime isolado WSL/Python3.14.4. A revisão independente não encontrou bloqueadores nos leitores, inventário, vínculos e interops. Ruff, formato, lock offline, referências e regras do gitignore passaram. O wheel contém dez fontes exatas verificadas explicitamente, incluindo journal, raiz e PowerShell; os 13 arquivos do módulo de provisionamento também foram conferidos byte a byte. Interface, launcher e executável diagnóstico anterior permanecem sem mudança. Docker Desktop continua sem o pipe do daemon; reconstrução da imagem de verificação permanece pendente, sem alegar saúde ou atualização do serviço padrão.

A composição operacional passou em 21 testes de runtime, 39 de assets/hardware, 55 de backend/imagem e 17 da abertura exclusiva do ledger. Essas execuções incluem locks até o fechamento, replay, cancelamento, perda de resposta, kit real inválido e crash de processo próprio após reserva durável. Os 12 testes de interoperabilidade da aquisição usam core/API/HTTP/SQLite/cifra reais; hardware e template são falsos nos cenários de execução, enquanto um caso usa `LocalTemplate` real para recusar artefatos inválidos antes do claim. A descoberta da conta usa diretório descartável. Nenhum desses testes comprova execução de uma VM.

A suíte Windows completa passou em 1.984 testes e 31 skips antes da correção final do consumo nativo do bootstrap. Uma execução anterior revelou que duas remoções concorrentes no Windows podiam permitir selar duas inscrições; o consumo exclusivo corrigiu essa corrida. A suíte específica final da inscrição passou em 120 testes e um skip, incluindo dois processos concorrentes, reader aberto, mudanças de conteúdo/ACL/links, falha de remoção, prazo vencendo durante a guarda e crashes antes/depois da marcação da exclusão. A revisão independente conferiu ownership do handle, fechamento e validade imediatamente antes da remoção. São execuções distintas, sem soma de cobertura.

A validação final da composição e inscrição passou em 265 testes Windows afetados (quatro skips). A validação Linux expôs recuo do relógio civil no WSL; a causa foi reproduzida em fixture própria, e os carimbos locais foram corrigidos sem alterar prazos ou validadores. Os 235 testes Windows afetados do ledger/supervisor/aquisição passaram, incluindo 13 regressões novas; revisão independente sem bloqueadores. Nas fontes finais, 1.079 testes Linux afetados passaram (41 skips), e a imagem Docker verification passou em 1.394 testes (45 skips). Os 17 cenários HTTP reais de inscrição/aquisição passaram em execução Docker separada antes da correção dos carimbos; a suíte Linux final também inclui esses cenários. Wheel e imagem foram reconstruídos: 13 fontes explícitas exatas no wheel, 46 fontes/testes e módulos instalados conferidos na imagem. Ruff, formato, lock offline, diff, referências e regras do gitignore passaram. Interface, launcher e pacote diagnóstico não mudaram; não houve inscrição na raiz real, VM, habilitação de hipervisor ou alteração de rede. A saúde da instalação padrão não é inferida dessas validações.
