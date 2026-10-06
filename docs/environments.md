# Computador próprio da abelha

BEES-011 começa com pedidos persistentes e diagnóstico de instalação. **Este checkpoint não cria uma VM e não permite que a abelha use navegador, terminal ou apps.** O catálogo descreve o alvo Linux com Chromium e LibreOffice; não comprova imagem instalada, isolamento ou disponibilidade.

## Uso pela interface

Abra a aba **Computador** da abelha. Escolha nome e recursos, confira o aviso e registre o plano. A tela mostra que o computador aguarda preparação da instalação. O pedido sobrevive a recarregar a página, reiniciar a API e recriar containers com os mesmos volumes. Um pedido ainda não iniciado pode ser cancelado; seu histórico é preservado.

O perfil inicial pede 2 CPUs, 4 GiB de memória e 30 GiB de disco. Os limites de entrada são 1–4 CPUs, 2–8 GiB e 20–100 GiB. São limites do pedido, **não recursos alocados ou limites de uma VM em execução**. Nenhum arquivo, sessão de navegador ou credencial pessoal é disponibilizado.

Sem host pareado com diagnóstico recente, o driver aparece como `none`. Docker não fornece automaticamente acesso ao Hyper-V, KVM ou desktop do hospedeiro. Não monte socket Docker, discos pessoais, serviços administrativos ou credenciais de hipervisor na API/worker para contornar essa separação.

## Vínculo de diagnóstico do host

Com Bees aberto e acesso configurado, dê dois cliques em **Conectar host Bees.vbs**. O launcher mostra um código público de comparação. Na aba **Computador**, confira esse código, marque a confirmação e autorize o diagnóstico. Revogue o vínculo pela mesma interface. Essa autorização permite somente enviar booleanos de disponibilidade da virtualização; não cria VM nem concede acesso pessoal.

O helper `apps/host` roda fora do Docker, na conta atual e sem administrador. O [pacote Windows](host-package.md) dispensa Python/`.venv` para o usuário e usa o mesmo atalho; um checkout sem pacote pode usar `.venv` como alternativa de desenvolvimento. Distribuições Docker sem helper continuam com conversa/tarefas funcionando; o launcher informa sua ausência. Instalação completa guiada, outras plataformas, recuperação e autostart após boot continuam em BEES-028. Não exigir que o usuário configure credenciais no terminal como fluxo de produto.

O operador emite um convite pela CLI interna do container, somente com identidade já configurada e schema atual. O convite de alta entropia dura 5 minutos; a confirmação humana do pareamento dura 15 minutos. O arquivo temporário tem ACL exclusiva da conta atual e é removido antes da rede. O helper grava sua credencial própria com DPAPI CurrentUser antes da troca. Linux exige chave Fernet externa explícita. Nem ticket nem credencial entram em URL, argumentos, logs ou formulário; SQLite armazena hashes. O código é derivado independentemente pelo helper, vinculado à instalação, convite e credencial.

A migração `0006_hosts.sql` registra instalação, convites, vínculos, comandos e receipts de diagnóstico. Há um host ativo por instalação. Uma credencial de host não autentica rotas humanas; cookies de usuário não substituem a credencial do helper. Confirmar/revogar exige sessão/Origin/CSRF, UUID e revisão humana. Relatórios têm sequência/revisão próprias para não invalidar a edição humana.

Depois da confirmação, o helper envia diagnóstico somente leitura a cada 15 segundos. Sem relatório recente por 60 segundos, a UI mostra offline e descarta a disponibilidade. Replay não renova esse prazo. Revogação bloqueia novos relatórios, inclusive replay, e encerra o helper. Resultado incerto é reconciliado por consulta e receipt persistido; nunca causa pareamento automático. Reabrir explicitamente o launcher permite solicitar novo vínculo após expiração/revogação na mesma instalação; não troca de instalação silenciosamente.

Os receipts de diagnóstico ficam preservados, até cerca de 5760 por dia com um helper continuamente ativo. A retenção precisa de política própria antes de operação prolongada; limpeza futura não poderá renovar disponibilidade pelo replay de um relatório antigo. Não são entradas de cache.

Os canais próprios são `POST /api/v1/host-link/runtime/exchange` (convite obrigatório), `GET .../session` e `POST .../report` (bearer próprio). A interface usa `GET /api/v1/environments/hosts` e `POST .../{id}/confirm` ou `.../{id}/revoke`. Host/Origin/JSON e limites globais permanecem aplicados. O helper só aceita origem HTTP loopback local; não segue redirects, proxies do ambiente ou comandos do servidor. Não recebe `host_jobs` de criação, shell, caminhos ou segredos de modelos.

## Estado e consistência

A migração `0005_environments.sql` acrescenta `environments` e `host_jobs`. Cada pedido tem UUID de comando e um único job durável em `awaiting_host`. Repetir o mesmo UUID e conteúdo retorna o estado atual; alterar o conteúdo com o mesmo UUID é conflito. Não há consumidor de provisionamento neste checkpoint.

Cancelar exige revisão atual e UUID próprio. Pedido e job mudam atomicamente para `cancelled`; replay não cria outro efeito. Os vínculos são verificados por abelha. A projeção pública exclui metadados internos, hashes de pedido, dono de execução e evidência de recuperação. `usable` permanece falso.

O contrato interno de recuperação aceita somente despacho de um dono interrompido com referência de evidência. Marca pedido e job como `outcome_unknown`, sem retry automático. Não existe rota para simular despacho, recuperar journals ou declarar um computador pronto. Consulta e catálogo não alteram o estado.

## API autenticada

Todas as rotas exigem sessão; mutações também exigem Origin e CSRF válidos.

| Rota | Resultado |
| --- | --- |
| `GET /api/v1/environments/catalog` | Templates planejados, limites e diagnóstico da instalação. |
| `GET /api/v1/environments/host` | Diagnóstico seguro; `provisionable: false`. |
| `GET /api/v1/agents/{id}/environments` | Pedidos paginados da abelha. |
| `POST /api/v1/agents/{id}/environments` | Pedido com nome, template, recursos e UUID. |
| `GET /api/v1/agents/{id}/environments/{environment_id}` | Estado atual e identificação/status da operação. |
| `POST /api/v1/agents/{id}/environments/{environment_id}/cancel` | Cancelamento com `expected_revision` e `client_request_id`. |

Corpos não aceitam driver, URL de imagem, caminho, shell, token ou identidade de host. Esses dados pertencem à configuração confiável da instalação, fora do conteúdo do modelo.

## Diagnóstico e provisionamento restante

O core oferece preflight somente leitura para configurações confiáveis Hyper-V ou libvirt. Usa argumentos estáticos sem shell, timeout e projeção em booleanos, sem devolver saída bruta. Hyper-V verifica plataforma, módulo e serviço de gerenciamento; libvirt verifica plataforma, acesso a `/dev/kvm` e conexão ao serviço. A presença desses itens ainda não comprova permissões, imagem, rede segura ou ponte de execução; mesmo um diagnóstico positivo mantém `provisionable: false`.

Habilitar Hyper-V exige decisão explícita do operador. Windows 11 Pro é uma edição suportada, mas o hipervisor ativo pode ocultar requisitos no diagnóstico de hardware; veja os [requisitos oficiais](https://learn.microsoft.com/en-us/windows-server/virtualization/hyper-v/host-hardware-requirements). A rede precisa considerar o NAT existente de Docker/WSL; [WinNAT](https://learn.microsoft.com/en-us/windows-server/virtualization/hyper-v/setup-nat-network) não constitui por si uma política de isolamento.

Para concluir BEES-011 faltam:

- Provisionador de VM separado no host com concessão específica, independente do vínculo de diagnóstico já entregue; instalação empacotada/guiada.
- Imagem Linux fixada por versão/hash confiável, armazenamento persistente, Chromium/LibreOffice e ponte autenticada no guest.
- Rede filtrada fora do guest, com bloqueios demonstrados para host, redes privadas, link-local e metadados; exceção exata para o plano de controle. [Filtros de libvirt](https://libvirt.org/formatnwfilter.html) são uma referência ainda sem implementação.
- Journal e fencing de operações reais, reconciliação de efeitos desconhecidos, limites medidos e controle visual autorizado.
- Testes com VM real: provisionar, reiniciar, preservar arquivos/sessões e comprovar restrições. Containers e doubles de teste não substituem essa evidência.

Nenhum componente do sistema é habilitado ou instalado automaticamente. Indisponibilidade não troca a execução para a máquina pessoal.

## Atualização e validação

Antes de migrar, pare API, worker e demais escritores; preserve dados e chave do cofre. O migrador cria backup coerente da base existente. O launcher Windows quiesce os serviços ao trocar a imagem; veja [persistência](persistence.md) e [containers](containers.md).

Testes de core/API verificam idempotência, CAS, FKs, rollback, isolamento entre abelhas, recuperação conservadora e entradas negativas. Testes de interface verificam aviso, recursos, estados e respostas incertas. `scripts/check-container.py` usa conta/volumes descartáveis para persistência após recriação, cancelamento e sessão/CSRF. Nenhum desses testes afirma provisionamento físico.

Checkpoint de 06/10/2026: 747 testes Python, 289 web e 364 Linux aprovados (24/4 skips de plataforma). Ruff, ESLint, typecheck e build aprovados. Compose descartável comprovou pedidos/replay/escopo/recriação/CAS/cancelamento. Navegador comprovou perfil de recursos, cadastro, reload e confirmação de cancelamento; pt-BR/en/es e viewport390 sem overflow horizontal (client/scroll375). Evidências locais ignoradas em `data/validation/bees-environments-desktop.png` e `bees-environments-mobile.png`. Instalação padrão atualizada pelo launcher ao schema5, com backup, identidade/cofre preservados e API/worker saudáveis.

Checkpoint de vínculo de diagnóstico, 06/10/2026: suíte completa com 828 testes Python aprovados e 26 skips; após os ajustes finais, 56 testes afetados aprovados com 2 skips. Interface: 330 testes, ESLint, typecheck e build aprovados. Linux: 449 testes aprovados com 6 skips; Ruff e formato aprovados. Integração nativa Windows comprovou DPAPI/ACL, troca, confirmação, diagnóstico real e encerramento após revogação. Launcher PowerShell 5.1 com Docker descartável comprovou criação, remoção do bootstrap e reuso da identidade sem duplicar helper. Navegador comprovou comparação do código, confirmação, persistência após reload, cancelamento e confirmação da revogação, idiomas pt-BR/en/es e viewport390 sem overflow (client/scroll375). Evidências ignoradas: `data/validation/bees-host-pairing-desktop.png` e `bees-host-pairing-mobile.png`. O invólucro VBS não foi acionado. A instalação padrão foi atualizada ao schema6 com backup e identidade/cofre preservados; nenhum host de teste foi vinculado nela. O diagnóstico real encontrou módulo/serviço Hyper-V indisponíveis; VM e isolamento continuam sem aceite.
