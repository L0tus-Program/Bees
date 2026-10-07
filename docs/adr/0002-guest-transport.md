# ADR 0002 — Transporte privado do computador da abelha

- Data: 2026-10-06.
- Estado: aceita para o componente de diagnóstico; integração à VM ainda pendente.
- Escopo: BEES-011, complementando a ADR 0001.

## Contexto

O helper nativo precisa comunicar-se com uma VM Linux sem conceder acesso à rede pessoal. O vínculo de diagnóstico do host já existe, mas não autoriza provisionar uma VM ou executar ferramentas. API e worker não devem receber sockets administrativos ou discos do hospedeiro.

## Decisão

No Windows, preparar um transporte Hyper-V sockets entre helper e guest. O guest inicia a conexão com `AF_VSOCK`, CID do host e porta fixa; o helper escuta com `AF_HYPERV`, VMID exato e GUID de serviço correspondente. Identidade declarada em JSON não substitui a identificação física do peer. Endereços wildcard, broadcast, parent e loopback são recusados pelo contrato.

Hyper-V sockets transportam um fluxo sem depender de TCP/IP. O guest Linux precisa de suporte do kernel a VSOCK/Hyper-V sockets; o Windows requer registro do serviço. A documentação da Microsoft descreve essa configuração e o mapeamento de porta Linux para GUID. [Microsoft](https://learn.microsoft.com/en-us/windows-server/virtualization/hyper-v/make-integration-service), [socket Python](https://docs.python.org/3.14/library/socket.html).

A proposta de provisionamento é uma VM sem NIC, compartilhamentos pessoais ou clipboard. O transporte entregue neste checkpoint não configura a VM, não registra o serviço no Windows e não demonstra isolamento de rede. O acesso web futuro dependerá de um proxy separado, com política de destinos aplicada fora do guest; esse proxy ainda não existe.

Sobre o fluxo, usar TLS 1.3 com autenticação mútua, CA privada, certificados por vínculo e pin do certificado esperado. A identidade confiável inclui instalação, ambiente, job, VM, geração e guest. Certificados e chaves vêm de configuração local privada do operador; frames remotos não cadastram nem alteram identidades. [ssl Python](https://docs.python.org/3.13/library/ssl.html).

O protocolo inicial permite apenas handshake e diagnóstico booleano de desktop, Chromium, Writer e workspace. Frames têm tamanho e prazo limitados; requisições incluem sessão, ID e nonce próprios. Replay conhecido conserva a observação original e fica marcado como cache. Resultado desconhecido não dispara uma nova coleta automática. Nenhum status recebido concede `usable`, `provisionable` ou permissão de ferramenta.

## Fronteiras

- `apps/guest`: pacote separado, biblioteca padrão Python 3.13 do Debian, sem importação de API, core, host, modelos ou banco canônico.
- `apps/host/.../guest_bridge`: componente interno isolado do helper Python 3.14; não altera a CLI ou o vínculo de diagnóstico existente.
- Domínio, políticas, concessões, tarefas e journal de efeitos permanecem no core. O journal local do guest cobre sessões e diagnósticos, não substitui `Action`/`ExecutionClaim`.

O guest usa SQLite da biblioteca padrão com journal DELETE/FULL para metadados locais do transporte, sem WAL. Isso não muda a escolha de APSW, a verificação de versão ou as migrações do banco canônico descritas na ADR 0001. Estado perdido ou incompatível não é recriado para contornar fencing.

## Alternativas

| Opção | Avaliação |
| --- | --- |
| TCP/WebSocket na rede do guest | Continua sendo opção para outro driver. No Windows exigiria configurar e comprovar regras de rede adicionais antes de expor o canal. |
| Hyper-V sockets sem TLS | Identidade do transporte não elimina autenticação da instalação, geração e configuração privada; manter TLS e vínculo exato. |
| Biblioteca do core dentro do guest | Levaria dependências e autoridade desnecessárias para a fronteira de execução. Manter protocolo pequeno e testes de interoperabilidade. |
| Container como computador isolado | Útil para validar instalação e protocolo, mas não demonstra a fronteira de VM escolhida na ADR 0001. |

## Validação e limites

Testes de TLS sobre TCP loopback demonstram autenticação e interoperabilidade do protocolo, não o bus Hyper-V. Factories de sockets nos testes demonstram validações de argumentos, não identidade física de uma VM real. O componente não oferece fallback TCP em produção.

Antes de concluir BEES-011: implementar provisionador, bootstrap/rotação/revogação integrados ao core, rede filtrada, sessão desktop e visualização autorizada; demonstrar em VM real persistência, recursos, perda de conexão e tentativas de acesso ao host/rede privada. Integrações de ferramenta ainda exigem autorização online, snapshot próprio e fencing de tarefas.

Habilitar componentes do Windows, registrar o serviço e criar a VM requerem uma ação concreta revisável e autorização própria. A entrega de software não executa essas alterações do sistema.
