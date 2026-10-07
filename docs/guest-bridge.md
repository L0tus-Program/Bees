# Ponte de diagnóstico do guest

Checkpoint BEES-011. O componente conecta as bibliotecas do helper e do guest com autenticação mútua e diagnóstico restrito. Ainda não provisiona uma VM, não executa ferramentas e não está ligado à aplicação padrão.

A [ADR 0002](adr/0002-guest-transport.md) registra a escolha do transporte. O [kit Linux](guest-image.md) permanece separado, sem certificados, credenciais ou ativação da ponte.

## Componentes

| Componente | Responsabilidade atual |
| --- | --- |
| `apps/host/src/bees_host/guest_bridge` | Identidade privada por VM, servidor TLS, handshake, sessão e leitura de diagnóstico. |
| `apps/guest/src/bees_guest` | Cliente Linux, validação do relay, journal local e coleta somente leitura. |
| API/core/worker | Mantêm as capacidades entregues anteriormente. Não recebem um executor de VM neste checkpoint. |

O guest suporta Python 3.13/3.14, somente biblioteca padrão. Não pertence ao workspace de dependências do plano de controle. O helper continua em Python 3.14 e usa suas dependências existentes. `pythonpath` na configuração do pytest permite testar o guest sem instalá-lo no serviço.

## Identidade e transporte

A configuração é local e privada, emitida por operador confiável. Não há endpoint de cadastramento, emissão de CA ou atualização por frames remotos. Ela vincula UUIDs de instalação, ambiente, job, VM e guest, geração positiva e pin SHA256 DER do peer.

O diretório do helper contém `identity.json`, `ca.crt`, `server.crt` e `server.key`; o do guest contém `identity.json`, `ca.crt`, `client.crt` e `client.key`. Host e guest usam campos de pin distintos: `guest_cert_sha256` e `relay_cert_sha256`, respectivamente. No guest, arquivos/diretório precisam pertencer a root, com modos privados, sem links; os ancestrais devem pertencer a root sem escrita por outros. O helper exige arquivos privados da conta atual e DACL protegida no Windows. Nenhuma dessas chaves é a chave do cofre Bees ou uma API key de modelo.

TLS exige versão 1.3, CA válida, certificado do peer esperado e SAN URI de vínculo exato. A identidade é de instalação/VM, não de hostname DNS. O guest desativa verificação de hostname DNS mantendo verificação da CA e do pin/vínculo. Os certificados devem ter extensões compatíveis com verificação X.509 estrita.

Na produção prevista, o guest conecta apenas ao CID do host, porta `2761`, via `AF_VSOCK`; o helper aceita `AF_HYPERV` com VMID exato. O GUID de serviço local é `00000ac9-facb-11e6-bd58-64006a7986d3`. O registro do serviço é apenas consultado; não é criado automaticamente. Ausência de suporte/registro encerra a operação, sem alternativa TCP.

O VMID físico deve ser verificado antes de TLS/JSON. O formato de endereços e a associação da porta efêmera do guest ao ServiceId remoto ainda precisam ser comprovados no bus Hyper-V real. Fixtures de socket não demonstram essa associação. [Microsoft Hyper-V sockets](https://learn.microsoft.com/en-us/windows-server/virtualization/hyper-v/make-integration-service).

## Protocolo restrito

Frames JSON têm prefixo de tamanho de quatro bytes, limite de 16 KiB, prazo de leitura e campos fechados. IDs precisam ser canônicos; chaves duplicadas, constantes não JSON, tipos incorretos, vínculo divergente e campos extras são recusados.

O servidor envia `challenge`; o guest responde `hello`; o servidor confirma `accepted`. Uma sessão possui nonce próprio. Cada `health_request` acrescenta ID e nonce de operação; a resposta precisa conservar vínculo, guest, sessão e correlação da requisição.

Diagnóstico só contém quatro booleanos:

| Campo | O que observa |
| --- | --- |
| `desktop_session` | Processo com nome XFCE e UID da conta da abelha; observação heurística. |
| `chromium` | Executável esperado presente e com propriedade/permissões verificadas. |
| `writer` | Executável esperado presente e com propriedade/permissões verificadas. |
| `workspace` | Diretório esperado com UID/GID da abelha e permissões verificadas. |

A coleta não inicia Chromium, Writer, shell ou desktop. Presença de executável não comprova funcionamento, sandbox ou autorização. Uma resposta positiva não altera `usable`/`provisionable` e não cria concessão.

## Journal, replay e interrupção

`diagnostic.sqlite3` é um journal local separado do banco canônico. Usa SQLite stdlib, DELETE/FULL e transações curtas. Guarda vínculo, sessões, sequência e receipts de diagnóstico; não recebe texto de tarefas, comandos, credenciais de modelos ou resultados de ferramentas.

O provisionador futuro deverá inicializar o journal explicitamente uma única vez. `Journal.initialize` grava primeiro um marcador privado com ID/vínculo e cria a base; a conexão normal e a CLI apenas abrem estado existente. Ausência da base ou do marcador, instalação parcial ou combinação incompatível são recusadas. Remover apenas a base não provoca criação automática nem zera as sequências.

Preparar uma requisição ocorre antes da coleta. Repetir ID e conteúdo conhecidos devolve a observação original marcada `cached`; mudar o conteúdo é conflito. Coleta iniciada sem confirmação fica `unknown`, sem nova coleta automática. Uma nova sessão impede publicação pela sessão antiga. Abrir estado incompatível ou de outro vínculo falha; não há recuperação que apague o journal para liberar replay. Sessões e receipts têm limites; a manutenção/rotação orientada ainda precisa de integração própria.

No helper, `SessionFence` deve ser compartilhado pelas conexões do mesmo vínculo, sob um único dono. Nova sessão fecha a anterior; revogar a fence fecha a conexão. Essa revogação local em memória não substitui a revogação persistente do core. Instâncias/processos independentes não compartilham a fence; o supervisor futuro precisa garantir exclusividade antes de publicar estado. Uma resposta `cached` não recebe instante de observação novo e nunca renova disponibilidade. Erro de correlação, sequência, autenticação ou transporte encerra a sessão, sem retry interno.

## Desenvolvimento e operação futura

Os testes usam identidades efêmeras de laboratório e TLS sobre loopback. O teste cruzado executa as duas implementações para detectar diferenças de protocolo; a guarda root/Linux do journal é substituída somente na fixture portátil e verificada separadamente no Linux.

Execute pela `.venv` do checkout:

```powershell
.venv/Scripts/python.exe -m pytest apps/host/tests/test_guest_bridge_tls.py apps/guest/tests scripts/tests/test_guest_interoperability.py
```

A CLI interna `bees-guest` exige Linux e diretório privado explícito; `--check` verifica configuração sem conectar. Não é um fluxo de setup do usuário final. Nenhum serviço/autostart ou pacote de produção é instalado por ela. A integração gráfica será feita quando houver provisionador e concessões próprias.

Para concluir BEES-011 ainda faltam provisionamento/boot real, bootstrap independente, revogação/rotação persistentes, rede filtrada fora do guest e desktop/visualização autorizados. Ferramentas exigirão snapshot, autorização online e journal/fencing canônicos; uma credencial de diagnóstico não autoriza execução.

## Evidência do checkpoint — 07/10/2026

- Suíte completa Windows: 1.102 testes aprovados, 27 skips ambientais, incluindo os opt-ins do helper empacotado anterior. Ruff e formato aprovados.
- Suíte afetada Linux: 241 testes aprovados, 9 skips Windows; host/guest e interoperabilidade executados em container próprio sem rede e com limites de CPU/RAM.
- Python 3.13.5 do kit instalado: smoke somente stdlib com arquivos root privados reais, TLS1.3 mútuo, diagnóstico de apps, DELETE/FULL, replay, crash de subprocesso, fencing e perda/corrupção de estado. Todos passaram. O campo `vm_ready` permaneceu falso.
- 358 testes da interface aprovados; nenhuma interface foi alterada neste checkpoint.
- Wheel/sdist do guest construídos e inventário conferido sem dependências de runtime ou dados privados. Isso não instala nem ativa a ponte no kit ou no helper distribuído.
- Revisão independente sem bloqueadores para este componente de diagnóstico. Logs locais ignorados em `data/validation/guest-bridge-*`; containers de laboratório removidos.

Não houve registro de serviço Hyper-V, alteração de rede, provisionamento de VM ou chamada de modelo. A instalação padrão não foi migrada/reconfigurada por este checkpoint; schema7 e catálogo planejado continuam sendo os contratos ativos.
