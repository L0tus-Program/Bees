# Computador próprio da abelha

BEES-011 começa com pedidos persistentes e diagnóstico de instalação. **Este checkpoint não cria uma VM e não permite que a abelha use navegador, terminal ou apps.** O catálogo descreve o alvo Linux com Chromium e LibreOffice; não comprova imagem instalada, isolamento ou disponibilidade.

## Uso pela interface

Abra a aba **Computador** da abelha. Escolha nome e recursos, confira o aviso e registre o plano. A tela mostra que o computador aguarda preparação da instalação. O pedido sobrevive a recarregar a página, reiniciar a API e recriar containers com os mesmos volumes. Um pedido ainda não iniciado pode ser cancelado; seu histórico é preservado.

O perfil inicial pede 2 CPUs, 4 GiB de memória e 30 GiB de disco. Os limites de entrada são 1–4 CPUs, 2–8 GiB e 20–100 GiB. São limites do pedido, **não recursos alocados ou limites de uma VM em execução**. Nenhum arquivo, sessão de navegador ou credencial pessoal é disponibilizado.

Compose mantém o driver de host como `none`. Docker não fornece automaticamente acesso ao Hyper-V, KVM ou desktop do hospedeiro. Não monte socket Docker, discos pessoais, serviços administrativos ou credenciais de hipervisor na API/worker para contornar essa separação.

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

- Provisionador separado no host, pareado com identidade própria e escopo explícito; instalação guiada pela interface.
- Imagem Linux fixada por versão/hash confiável, armazenamento persistente, Chromium/LibreOffice e ponte autenticada no guest.
- Rede filtrada fora do guest, com bloqueios demonstrados para host, redes privadas, link-local e metadados; exceção exata para o plano de controle. [Filtros de libvirt](https://libvirt.org/formatnwfilter.html) são uma referência ainda sem implementação.
- Journal e fencing de operações reais, reconciliação de efeitos desconhecidos, limites medidos e controle visual autorizado.
- Testes com VM real: provisionar, reiniciar, preservar arquivos/sessões e comprovar restrições. Containers e doubles de teste não substituem essa evidência.

Nenhum componente do sistema é habilitado ou instalado automaticamente. Indisponibilidade não troca a execução para a máquina pessoal.

## Atualização e validação

Antes de migrar, pare API, worker e demais escritores; preserve dados e chave do cofre. O migrador cria backup coerente da base existente. O launcher Windows quiesce os serviços ao trocar a imagem; veja [persistência](persistence.md) e [containers](containers.md).

Testes de core/API verificam idempotência, CAS, FKs, rollback, isolamento entre abelhas, recuperação conservadora e entradas negativas. Testes de interface verificam aviso, recursos, estados e respostas incertas. `scripts/check-container.py` usa conta/volumes descartáveis para persistência após recriação, cancelamento e sessão/CSRF. Nenhum desses testes afirma provisionamento físico.

Checkpoint de 06/10/2026: 747 testes Python, 289 web e 364 Linux aprovados (24/4 skips de plataforma). Ruff, ESLint, typecheck e build aprovados. Compose descartável comprovou pedidos/replay/escopo/recriação/CAS/cancelamento. Navegador comprovou perfil de recursos, cadastro, reload e confirmação de cancelamento; pt-BR/en/es e viewport390 sem overflow horizontal (client/scroll375). Evidências locais ignoradas em `data/validation/bees-environments-desktop.png` e `bees-environments-mobile.png`. Instalação padrão atualizada pelo launcher ao schema5, com backup, identidade/cofre preservados e API/worker saudáveis.
