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

As rotas recebem somente DTOs do core. Não há rota de emissão, bootstrap, shell, recuperação, reconhecimento ou execução de hardware. O serviço registra a autoridade e os receipts; não executa PowerShell nem acessa o hipervisor. O executor continua precisando de integração operacional própria.

`HTTPAuthority` recebe de composição confiável uma origem local, credencial privada e os UUIDs esperados de instalação/host/provisionador. Não recebe esses dados do modelo. Aceita somente loopback; `localhost` é resolvido uma vez, exige endereços loopback e fixa o IP discado, conservando Host/Origin/SNI. HTTPS usa validação padrão de certificado. Não usa proxy do ambiente, redirecionamento ou fallback de origem.

Respostas exigem JSON UTF-8 fechado, até 16 KiB, sem compressão, chaves duplicadas, valores não finitos ou campos extras. O adaptador confere identidade, hash/pins do plano, dono, geração, revisão, estado e UUID do efeito. Não repete automaticamente requisições nem gera novos UUIDs para contornar perda de resposta. Os identificadores de intent e publicação vêm do journal antes da chamada.

Os métodos são síncronos; cada chamada usa uma conexão assíncrona própria sob prazo total de cinco segundos, incluindo cabeçalhos e corpo. O cancelamento fecha a conexão, sem thread de watchdog pendente. Uma chamada dentro de loop assíncrono ativo é recusada. Esse prazo protege o transporte HTTP; não amplia o orçamento do comando nativo nem demonstra renovação supervisionada durante VHDX real.

## Autoridade persistente do core

A migração `0008_provisioning.sql` acrescenta planos imutáveis, autorizações, credenciais próprias, gerações por host, claims exclusivos, intents/receipts e comandos idempotentes. Esses registros são canônicos, não cache. A migração exige escritores parados e backup, conforme [persistência](persistence.md).

A credencial `bp_` do provisionador é distinta de `bh_`, usada apenas para diagnóstico. Sua emissão é operação privada do operador, sem rota pública neste checkpoint. SQLite guarda somente o hash; o segredo retornado uma vez não entra em plano, eventos, contexto do modelo ou argumentos de processo.

O claim reserva o host por 60 segundos, com geração monotônica e dono próprio. Renovação exige contexto atual, não revive lease vencida e tem duração total máxima de cinco minutos. A aprovação só é consumida atomicamente no primeiro intent de efeito, junto da transição do pedido/job; reservar um claim não a consome.

Cada operação exige o resultado confirmado da anterior, mesmo vínculo de claim/dono/geração e nova reavaliação de credencial, host, diagnóstico recente, contexto e autorização. A sequência fechada é `create_vhd`, `create_vm`, `configure_vm`, `remove_nic`, `attach_iso`, `verify`. Nenhum replay recebe permissão para repetir o efeito.

O VMID físico vem de `New-VM`, que não aceita um parâmetro `-Id`. O identificador passa a ser obrigatório e constante nos receipts seguintes. A verificação final registra recursos, VM parada, ausência de rede e ISO esperado. Mesmo esse receipt mantém o ambiente em `provisioning`, sem declarar desktop pronto. [Contrato oficial de New-VM](https://learn.microsoft.com/en-us/powershell/module/hyper-v/new-vm?view=windowsserver2025-ps).

Lease vencida sem intent pode ser abortada. Havendo despacho sem conclusão, o estado passa a `outcome_unknown`, bloqueando novos efeitos na instalação individual. Revogar o host e parear outro UUID não contorna essa quarentena. Reconhecer o risco não comprova encerramento do executor anterior e não libera o bloqueio. Reconciliação/recuperação são internas; não há rota de retry, exclusão do journal ou declaração arbitrária de VM pronta.

## Componente nativo fechado

`apps/host/src/bees_host/provisioning` reúne contratos, verificação local do kit, journal, runner e script PowerShell fixo. Não recebe texto livre para executar. O PowerShell usa caminho absoluto do sistema, ambiente mínimo e janela oculta; o processo não recebe credenciais por argumentos/stdin. Ausência de privilégios ou componentes encerra a operação. Não há UAC, instalação de Hyper-V ou alteração de registro/rede automática.

O kit precisa estar previamente copiado em área privada do operador, com hashes/tamanhos fixados. O componente não baixa imagens. O hardware usa um layout derivado do plano. Dados privados de controle exigem ACL da conta atual; diretórios/discos usados pelo VMMS precisam de ACL própria compatível com SYSTEM, Administrators e o acesso específico da VM. Essa fronteira ainda exige prova com Hyper-V real.

O journal local é separado do banco do core, com inicialização explícita, marcador de identidade, DELETE/FULL e lock nativo. Perda, corrupção ou estado parcial falham restritivamente; abrir estado ausente não o recria. PID e instante de criação do filho são gravados antes de enviar `GO`; o filho aguarda esse sinal antes do efeito. Há nova guarda do core imediatamente antes de `GO` e da publicação do receipt.

As guardas Windows verificam contenção de caminho, reparse points, hardlinks e leitura/escrita por identidades estrangeiras antes das mutações. A consulta nativa de arquivo usa P/Invoke fixo em memória, sem compilador, DLL temporária ou escrita no diretório do sistema. Os testes executam essas guardas somente em arquivos descartáveis; não carregam Hyper-V nem comprovam a ACL criada pelo VMMS em uma VM real.

Uma interrupção mantém evidências e impede repetição. A reconciliação consulta inventário gerenciado sem limpar `unknown`; antes dela, o componente precisa comprovar que o processo anterior não continua em execução. PID reutilizado não é tratado como o mesmo processo. A supervisão e recuperação operacional completa ainda precisam de integração.

O orçamento atual de comando é 30 segundos, exigindo pelo menos 35 segundos de lease disponível antes de `GO`. Criar um VHDX fixo de 20–100 GiB pode exceder esse tempo. Antes de executar hardware real, é necessário medir a duração e integrar renovação supervisionada; timeout conserva `unknown`, sem tentar criar o disco/VM novamente.

`New-VM` cria um adaptador mesmo sem `SwitchName`. O script verifica que ele não está conectado e o remove no escopo da VM criada antes da verificação final. Nenhuma operação chama `Start-VM`. [Comportamento oficial de New-VM](https://learn.microsoft.com/en-us/powershell/module/hyper-v/new-vm?view=windowsserver2025-ps).

## Aceite restante

- Composição operacional do adaptador HTTP, emissão privada/guiada, supervisor e pacote atualizado; o helper empacotado anterior continua diagnóstico.
- Verificação do script/ACL/VMID e falhas com Hyper-V real, depois de autorização concreta para os componentes necessários do Windows.
- Instalação humana do Debian/kit, integração da ponte no guest e persistência após reinício.
- Rede filtrada e provas de isolamento externas ao guest antes de habilitar conectividade.
- Desktop, transporte Hyper-V sockets físico e integração das ferramentas/limites reais.

Testes cruzados em `scripts/tests/test_provisioning_interoperability.py` usam core, runner, SQLite e locks reais, com backend de hardware falso: sequência completa, revogação enquanto o filho aguarda `GO`, perda da resposta após confirmação canônica e expiração de lease depois do efeito. Nenhum teste dessa fixture cria VM ou demonstra isolamento.

## Evidência do checkpoint — 07/10/2026

A suíte completa Windows passou com 1.207 testes e 27 skips; a validação Linux de core, host, guest e APIs afetadas passou com 936 testes e 17 skips. Depois da guarda adicional de leitura por SID estrangeiro, os 60 testes de provisionamento/interoperabilidade passaram no Windows, e 55 no Linux com cinco skips exclusivos do Windows. Esses números descrevem execuções separadas, não devem ser somados como cobertura única.

A interface passou em 404 testes, lint, tipos e build. Uma instalação descartável comprovou revisão humana, autorização, persistência após reload, revogação, traduções e viewport de 390 px sem overflow. A imagem Docker passou no fluxo de primeiro acesso, cofre, conversa, worker separado, políticas, ferramentas, pedidos de computador e recriação com persistência. O wheel do host inclui o script PowerShell exato; o executável de diagnóstico anterior não foi substituído.

Ruff, formatação, lock de dependências e diff foram conferidos. Os testes não usaram credenciais reais de modelo, não criaram VM, não habilitaram Hyper-V e não alteraram redes do hospedeiro. A aprovação na interface é evidência de autorização persistente, não de execução física.

A instalação local foi atualizada pelo launcher, com API/worker parados durante a migração 7→8 e backup automático. API e worker ficaram saudáveis; integridade e chaves estrangeiras passaram, preservando identidade, abelha, conversa, mensagens e cofre existentes. Nenhuma identidade de teste foi criada na instalação padrão.

O checkpoint seguinte acrescentou o transporte HTTP: 1.309 testes passaram na suíte Windows completa (27 skips), e 1.037 na validação Linux de core, host, guest, APIs afetadas e interoperabilidade (18 skips). Inclui 72 testes do adaptador, 19 da API runtime e dez de HTTP real: Uvicorn em subprocesso próprio, execução do runner com hardware falso, revogação antes de GO, falha HTTP após receipt já confirmado, perda do serviço, replay/renovação/dono/unknown e slowdrip nos cabeçalhos/corpo. As guardas de UUID exato, estado/revisão monotônicos e prazo total foram revisadas independentemente. Imagens e wheel foram reconstruídos, com fontes do transporte/script exatas; não foi ativado um executável de provisionamento.
