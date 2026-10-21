# Matriz de regressão e evidências de CI — BEES-026.1 (#90)

Esta matriz reutiliza os testes e o [workflow existente](../.github/workflows/ci.yml).
Ela liga fronteira, efeito protegido, cenário negativo, evidência e limite. Um job
verde valida apenas o seu SHA, configuração e plataforma; não demonstra hardware,
qualidade de modelo, autorização de publicação ou todos os aceites da história.

## Níveis de evidência

| Nível | O que comprova | O que não comprova |
| --- | --- | --- |
| Unitário/controlado | Contratos, reducers, extração, validação, estado React/DOM e transporte de provedor controlado. | Modelo real, navegador gráfico completo ou efeito físico. |
| Integração real da fronteira | SQLite/arquivos, subprocessos, HTTP loopback, TLS, API autenticada, processo do worker, container, PowerShell/DPAPI/ACL nativos quando executados na plataforma. O adaptador de hardware/provedor pode continuar controlado e deve ser nomeado. | Que uma VM foi criada, hipervisor habilitado ou provedor pago/local executou. |
| Demonstração física/operacional | Host e ambiente autorizados com recibos do efeito, instalação/build exatos e evidência própria. | Não pode ser inferida de fixture, job Windows, nome de teste ou UUID que parece VM. |

`TestClient` exerce HTTP/ASGI autenticado **no processo**; somente os cenários
nomeados como loopback abrem servidor/socket real. Fixtures de guest/VMMS e
`MockTransport` não mudam de nível só porque o processo supervisor é real.

## Fronteiras, histórias e cenários reaproveitados

Os caminhos abaixo são relativos à raiz do checkout. Os arquivos especificados
contêm os casos positivos e negativos existentes; não são novos executores de teste.

| Fronteira / histórias | Efeito e cenário negativo próprio | Evidência existente | Limite / próxima validação |
| --- | --- | --- | --- |
| Persistência / #3, #82–84 | Migração com backup; checksum adulterado, schema desconhecido, rollback, CAS, vínculo entre abelhas e escrita em transação de leitura recusados. | [test_database.py](../packages/core/tests/test_database.py), [test_execution_store.py](../packages/core/tests/test_execution_store.py). SQLite/FS reais, reabertura e concorrência. | Bases privadas descartáveis; não migrar instalação ativa para fazer QA. |
| Modelos / #5–9 | Contrato de usage/erro; conteúdo/credencial excluídos de logs; alteração de destino e resposta perdida não repetem geração automaticamente. | `packages/core/tests/test_provider_{contracts,adapters,wire,service,secrets,vault}.py`, `apps/api/tests/test_artifact_chat.py`. Adaptadores controlados; casos de wire usam HTTP próprio. | Uma rodada de CI não comprova modelo real remoto ou local. Próxima: modelo/endpoint próprio e evidência separada, sem publicar chave ou prompt privado. |
| Worker / #10–13, #81–84 | Claim concorrente, crash antes/depois do despacho, worker com fencing antigo, cancelamento, unknown e replay. | [test_tasks_execution.py](../packages/core/tests/test_tasks_execution.py), [test_execution_store.py](../packages/core/tests/test_execution_store.py), [test_task_control_consumption.py](../packages/core/tests/test_task_control_consumption.py). SQLite e cenários com processo/HTTP loopback reais. | Não repetir um efeito pago para reconciliar unknown. Não representa operação dentro de VM. |
| Políticas/aprovação / #14–21 | Parâmetros e snapshots alterados, grant antigo, deny após aprovação, expiração, revogação antes do efeito e decisão concorrente. | [test_approvals.py](../packages/core/tests/test_approvals.py), [test_execution_approvals.py](../packages/core/tests/test_execution_approvals.py), [test_execution_policies.py](../packages/core/tests/test_execution_policies.py). CAS/transações reais com transporte controlado. | Consentimento é vinculado à ação; um teste positivo não autoriza efeito externo de produção. |
| Ferramentas / #22–25 | Hash de argumento alterado, revisão/grant/política revogados, despacho concorrente, crash em `dispatch_started`, output inválido e replay sem segundo efeito. | [test_tools.py](../packages/core/tests/test_tools.py), [test_tool_execution.py](../packages/core/tests/test_tool_execution.py). Builtin textual real e recuperação do journal. | Builtin não demonstra navegador, app externo, quotas CPU/disco nem executor físico. |
| Host/guest e TLS / #26–31, #96–97 | Identidade TLS incorreta, credencial revogada, servidor perdido, sequência após restart. | [test_guest_interoperability.py](../scripts/tests/test_guest_interoperability.py), [test_provisioning_enrollment_interoperability.py](../scripts/tests/test_provisioning_enrollment_interoperability.py). Processos, TLS/HTTP e arquivos próprios reais; probes de máquina controlados. | Não comprova guest instalado nem VM pronta. Aceite físico segue separado. |
| Provisionamento / #26–31, #119, #124 | GO só após autoridade, stop/revogação enquanto filho aguarda, lease expirado após GO, recibo confirmado com resposta perdida e claim reapresentado sem segundo GO. | `scripts/tests/test_provisioning_{interoperability,http_interoperability,supervisor_interoperability,acquisition_interoperability}.py`; [test_provisioning_rejection.py](../apps/host/tests/test_provisioning_rejection.py). Processo, FS, SQLite e HTTP loopback reais; backend VMMS controlado. | A recusa definitiva de #124 não reclassifica incerteza antiga. Windows nativo e Hyper-V físico têm gates próprios; job Linux não os substitui. |
| Artefatos / #56–59 | Blob alterado/ausente, disco cheio, crash entre blob/COMMIT, versão concorrente, nomes maliciosos, resposta pós-publicação perdida, troca de conversa/destino e sessão expirada. | [test_artifacts.py](../packages/core/tests/test_artifacts.py), [test_artifact_uploads.py](../packages/core/tests/test_artifact_uploads.py), [test_task_deliverables.py](../packages/core/tests/test_task_deliverables.py), `apps/api/tests/test_artifacts_api.py`, `test_artifact_upload.py`, `test_artifact_chat.py`, `apps/web/src/features/artifacts/*.test.*`. SQLite/blobs/HTTP próprios reais e React/DOM. | Formatos suportados são texto UTF-8; conteúdo não é instrução/permissão. GUI autenticada anterior está documentada em [artifacts.md](artifacts.md#validação); não demonstra geração de modelo real. |
| Skills e rotinas / #17, #25, #64–71 | Revisão/entradas fixas, importação hostil, CAS/replay, DST, dispatch concorrente, crash pré/pós-commit, manual com recibo humano, proposta declarativa com fonte/hash, pausa/cancel/revoke/ask/deny e unknown sem retry. | [test_skills.py](../packages/core/tests/test_skills.py), [test_skill_tasks.py](../packages/core/tests/test_skill_tasks.py), [test_routines.py](../packages/core/tests/test_routines.py), [test_routine_scheduler.py](../packages/core/tests/test_routine_scheduler.py), [test_routine_manual.py](../packages/core/tests/test_routine_manual.py), [test_routine_proposals.py](../packages/core/tests/test_routine_proposals.py) e APIs/UI. SQLite, processos POSIX, CLI padrão e Chromium reais; provider HTTP controlado. | Scheduler/manual com aba fechada, reinício durante aprovação e recuperação de resposta perdida demonstrados no [recorte nativo](routines.md#evidência-e-limites). Proposta exige revisão humana e nasce pausada. Não demonstra LLM real, VM/apps/pessoal ou avaliação semântica de mudança relevante. |
| Consumo / #72–73, #128 | Duas reservas concorrentes, falta/negativo/incoerência de usage não viram zero, timeout conserva reserva, dupla liquidação e vínculos adulterados recusados. | [test_budgets.py](../packages/core/tests/test_budgets.py), [test_usage_integrity.py](../packages/core/tests/test_usage_integrity.py), `test_budgets_api.py`. SQLite real, subprocesso perdido após aceite e testes SQL diretos. | Teto local de tokens por abelha não é garantia de faturamento nem quota de ferramentas/CPU/disco. Migração 0016 recusa legado incoerente sem liberar reserva. |
| Parada global / #74 | Stop durante preflight, geração antiga após stop/resume, restart, resposta perdida e retomada que não reconhece unknown. | [test_global_stop.py](../packages/core/tests/test_global_stop.py), [test_task_control_consumption.py](../packages/core/tests/test_task_control_consumption.py), `test_global_stop_api.py`, `test_provisioning.py::test_global_stop_cuts_at_begin_and_fences_old_claim_after_resume`. | Operação já autorizada pode terminar e consumir. Geração em executor de ferramenta/rotina e ambos ambientes físicos têm aceites próprios. |
| Atenção/atividade / #75–76 | Rollback de projeção, aviso duplicado, decisão expirada, unknown lido/silenciado, snapshot regressivo, ID estrangeiro, payload extra e histórico >1.000 runs. | [test_attention.py](../packages/core/tests/test_attention.py), [test_activity.py](../packages/core/tests/test_activity.py), `apps/api/tests/test_{attention,activity}_api.py`; contratos/state/context e componentes web correspondentes. | Observação não libera reserva. Resumo idempotente por ocorrência e silêncio configurado existem; classificação semântica de mudança e preferências amplas por conversa continuam pendentes em #75. GUI/API real anterior em [activity.md](activity.md#validação-de-12102026). |
| Histórico / #85–87 | 302 entregas antigas, empate de datas, cursor errado, consulta abortada, troca de abelha/conversa e resposta atrasada; arquivo removido/parcial. | [test_delivery_history.py](../packages/core/tests/test_delivery_history.py), [test_delivery_history_api.py](../apps/api/tests/test_delivery_history_api.py), `apps/web/src/features/deliveries/*.test.*`. SQLite real e React/DOM. | Consulta e referência não fazem replay nem criam tarefa. Revisão Chromium/API real em pt-BR/en/es e 390/1280 px documentada no [contrato](delivery-history.md); não demonstra efeito de modelo/VM. |
| Portabilidade / #77–78 | Interrupção antes de publicar, blob ausente, pacote adulterado, schema/coluna inesperados, limites independentes e exclusão de estado privado/cofre. | [test_portability.py](../packages/core/tests/test_portability.py), [test_portability_api.py](../apps/api/tests/test_portability_api.py). Snapshot SQLite e FS reais. | Exportação comum não é backup operacional do cofre, restauração automática ou autorização para publicar dados. |
| Interface / #4, #76, #87, #91–92 | Respostas fora de ordem, abort/reload, CAS, intenção conservada após perda e troca de contexto; conteúdo hostil como texto. | `apps/web/src/api/*.test.ts`, `features/{activity,artifacts,deliveries}/*.test.*`, `features/safety/SafetyControl.test.tsx`. Vitest/React/DOM e contratos existentes. | GUI final desktop/móvel/idiomas/teclado e aceite de fluxo completo pertencem também a BEES-026.2/3; não deduzir imagem visual a partir de HTML estático. |
| Distribuição / #2, #89–93, #97–98 | Container non-root, chave gerenciada, autenticação/CSRF, restart com estado preservado e rejeição de ambiente não suportado. | [check-container.py](../scripts/check-container.py), [Dockerfile](../Dockerfile), [test_host_package.py](../scripts/tests/test_host_package.py), [test_frozen.py](../apps/host/tests/test_frozen.py). Docker real na CI; empacotamento inspecionado e opt-in do executável. | Teste frozen exige `.exe` construído e variável explícita; sucesso de build Docker não comprova instalação Windows, Hyper-V nem decisão de licença/release. |


### Aceites que permanecem físicos ou operacionais

| Histórias | Evidência que falta | Próxima validação e condição |
| --- | --- | --- |
| #29–31, #32–35 | Hyper-V físico, boot/guest exclusivo, rede filtrada/recursos/persistência e arquivo/comando/app no guest. | Janela humana autorizada em host compatível, recursos e imagem verificados, receipts e contagem de efeitos próprios. Fixtures de VMMS/guest não completam esse aceite. |
| #36–47 | Chromium/app do computador próprio, sessão, frames e takeover privado em fluxo real. | Ambiente próprio funcional e observação/controle humano; material sem credenciais e prova de persistência/revisão. |
| #48–55 | Executor pessoal real, grants efetivos de arquivos/navegador/app e revogação/handoff. | Conector e recursos pessoais de teste explicitamente autorizados; negativos no sistema real. |
| #91–92, #98–99 | Três fluxos completos, modelo local preparado, instalação limpa sem terminal e GUI final da revisão atual. | Distribuição suportada e modelo disponível; registro por versão, ambiente e fluxo, sem inferir aceite do build ou do pré-teste de catálogo. |
| #93 | Decisão de licença e revisão de dependências para release formal. | Decisão do mantenedor e inventário/revisão; não escolher licença unilateralmente nem publicar release pela conclusão de CI. |

## CI remota já confirmada por metadados

A etapa inicial **Validar guarda nativa de arquivos no Windows** carrega
`pytest_annotations`, assim como as suítes Windows seguintes.
O protocolo local também permite três códigos públicos fechados:
`provision_command_timeout`, `provision_command_invalid` e
`provision_command_unknown`. Somente uma exceção direta da classe exata
`ProvisionError`, com um único argumento exatamente nessa lista, recebe esse
código. Mensagens, subclasses, cadeias de causa e valores arbitrários não são
publicados. Isso não aumenta prazos, repete testes ou altera seu exit status;
nem reconstrói a causa de falhas históricas sem log acessível. Se um teste falhar,
o check recebe arquivo/função, linha e ordinal do caso; não recebe traceback,
parâmetros, caminhos privados ou conteúdo do erro. Os testes existentes em
[test_pytest_annotations.py](../scripts/tests/test_pytest_annotations.py) executam
uma falha real em subprocesso e verificam saída1 e sanitização. O plugin não muda
as guardas, os prazos, o ownership ou a seleção de casos, nem converte falha em sucesso.
Essa identificação não substitui o log privado completo ou comprova a causa da falha;
uma tentativa anterior sem o plugin continua sem esse diagnóstico retroativo.

Consulta read-only via `gh run view --json ...` confirmou o run
[38951214754](https://github.com/L0tus-Program/Bees/actions/runs/38951214754), evento
`pull_request`, head **`a0eb7a3c6076d5783b0df97cdd30e0d3ae39da1c`** da
[PR #131](https://github.com/L0tus-Program/Bees/pull/131). O run terminou com
`status=completed`, `conclusion=success`; a atualização final foi em
2026-10-16T19:13:55Z. Os quatro jobs foram recuperados individualmente:

| Job / evidência exata | Conclusão | Finalização UTC | Contagem de testes remota |
| --- | --- | --- | --- |
| [API / ubuntu-latest · 117142750439](https://github.com/L0tus-Program/Bees/actions/runs/38951214754/job/117142750439) | success | 19:00:48 | Não recuperada; logs bloqueados nesta sessão. |
| [API / windows-latest · 117142750299](https://github.com/L0tus-Program/Bees/actions/runs/38951214754/job/117142750299) | success | 19:13:54 | Não recuperada; inclui passo nativo separado e suíte geral, que não devem ser somados. |
| [Interface · 117142749992](https://github.com/L0tus-Program/Bees/actions/runs/38951214754/job/117142749992) | success | 18:57:16 | Não recuperada dos logs. |
| [Distribuição Docker / Linux · 117142750243](https://github.com/L0tus-Program/Bees/actions/runs/38951214754/job/117142750243) | success | 19:00:47 | Não recuperada dos logs; alvo verification seleciona parte dos testes Python. |

**Bloqueio de recuperação:** os metadados de run/jobs estão acessíveis, mas
`gh run view --job ... --log` recebe `Forbidden` no destino
`results-receiver.actions.githubusercontent.com`. Depois da adição do domínio ao
rascunho de rede, a tentativa específica pelo coletor continuou falhando com
`ci_log_fetch_failed`; rascunho salvo não comprova configuração aplicada ao runtime.
Nenhuma URL assinada, log bruto, credencial ou corpo de erro foi incluído no relatório.
Próxima validação: aplicar/publicar a configuração de rede, recuperar os logs do
mesmo run ou executar a próxima CI autorizada e guardar o relatório sanitizado.
Não solicitar PAT para contornar essa condição.

O SHA acima **não** inclui as alterações novas deste lote. O responsável pela
integração deve acrescentar run/head/jobs do próximo head publicado antes de
considerar a revisão atual validada remotamente.

### Atualização da integração — 19/10/2026

PRs #131–#139 foram integrados em `dev`, terminando em `2de1639`, cuja árvore é
idêntica ao head `d93c0598c73de4af63e3163fde9ef3f3ddec5646` do PR #139.
Todos os jobs dos runs [39355014126](https://github.com/L0tus-Program/Bees/actions/runs/39355014126)
e [39355072756](https://github.com/L0tus-Program/Bees/actions/runs/39355072756)
passaram, inclusive Windows. Isso substitui o diagnóstico Windows pendente anterior;
continuam sendo observações sobrepostas, sem soma.

O coletor ainda falha com `ci_log_fetch_failed`. O CLI pode retornar exit0 com stdout
vazio; o endpoint oficial de logs identificou `Forbidden` em
`productionresultssa12.blob.core.windows.net`. O requisito exato foi salvo no
rascunho de rede, sem afirmar aplicação/publicação. Nenhum relatório `verified`,
log bruto, URL assinada ou contagem remota foi produzido. CI do scheduler tem seu
próprio head/run; não herda o sucesso desses runs anteriores.

### Manual e proposta em conversa — 20/10/2026

O PR [#142](https://github.com/L0tus-Program/Bees/pull/142), head
`92218a30a57b5af7ac447da507ab5ddb6746e1bf`, foi integrado em `dev36e759e`
após Linux, Windows, interface, Docker e segurança passarem. O lote acrescenta
recibo humano manual, concorrência e crash real com rollback, recuperação após
resposta perdida e execução POSIX com reinício durante aprovação. Validação local:
2.864 Python passaram, 61 skips; 873 testes web em 60 arquivos.

O PR [#143](https://github.com/L0tus-Program/Bees/pull/143), head
`5aeb7bdfe5f9176dae6b895c45e9475e1e968fa9`, acrescenta sugestão textual,
fonte canônica fechada e confirmação humana para criar uma agenda pausada.
Validação local: 2.879 Python passaram, 61 skips; após a enumeração estrita final,
47 dirigidos passaram com sobreposição. Interface: 881 testes em 62 arquivos;
lint, tipos, build, Ruff e formato aprovados. Esses números não são contagens dos
jobs remotos. A CI deste head ainda precisa ser conferida individualmente.

Chromium pt-BR/en/es, 390/1280 e teclado verificou cada fluxo em instalação própria.
Manual: uma tarefa/ocorrência concluída, duas gerações HTTP controladas, um início
POSIX e um aviso. Proposta: uma geração HTTP controlada, edição humana, uma agenda
pausada e nenhuma tarefa/ocorrência/concessão nova. Ambas perderam uma resposta
após commit e recuperaram o pedido original sem POST no reload. Os detalhes e
limites estão em [rotinas](routines.md#evidência-e-limites); não há inferência de
LLM real, VM ou aceite humano de qualidade. O bloqueio de logs da CI permanece.

## Contagens locais e regra contra dupla contagem

| Observação independente | Números | Interpretação |
| --- | --- | --- |
| Validação local da PR #131, head `a0eb7a3`; JUnit privado `/tmp/bees-v1-python-final.xml`, início 2026-10-16T18:53:03Z; descrição da PR para web | Python: **2.317 casos = 2.256 passaram + 61 skips**, failures/errors=0. Web: **737 passaram**, conforme a descrição da PR. | Evidência local anterior ao run remoto; **não atribuir 2.256 ao job Linux remoto** sem recuperar o resumo daquele job. |
| Primeira rodada local deste lote, JUnit privado `/tmp/bees-independent-python.xml`, início 2026-10-16T19:25:04Z | **2.520 casos = 2.458 passaram + 61 skips + 1 falha**, errors=0. | Rodada com falha; **não é validação final verde**. A falha é `apps.host.tests.test_provisioning_root_plans::test_unaccepted_quarantine_has_no_journal_and_never_adopts_one[False-stopped]`, no trabalho de recusa/quarentena de #124. |
| Rodada local posterior deste lote, JUnit privado `/tmp/bees-independent-python-final.xml`, início 2026-10-17T19:45:14Z | **2.641 casos = 2.580 passaram + 61 skips**, failures/errors=0; interface: **786 passaram**. | Recusa/quarentena corrigida. O transporte de executor ainda em desenvolvimento ficou explicitamente excluído. Exportação final teve 80 testes focados e coletor de CI 35, incluindo casos acrescentados após a coleta global; são reruns parcialmente sobrepostos e não são somados ao total. CI do head final continua separada. |
| Coletor de evidência #90, execução focada própria | **35 passaram** em `scripts/tests/test_ci_report.py`. | Testes novos do coletor; não adicionar a 2.458/2.256 nem tratar como suíte final do produto. |
| Próximo head integrado | Pendente de nova execução e evidência por head/run/job. | O resultado inicial e testes focados não substituem essa execução. |

Nunca somar Linux + Windows + Docker: são observações sobrepostas de testes e
plataformas diferentes. O mesmo vale para push e pull_request do mesmo head,
reexecuções, subconjuntos nativos do Windows e reruns focados. A unidade de
rastreabilidade é **SHA + run + job + passo + comando/escopo**, não um “total de
testes acumulados”. Web e Python têm denominadores distintos e permanecem separados.

## Skips: razão e próxima validação

O inventário abaixo deriva dos **61 skips** do XML local inicial, não de um job
remoto. A soma das famílias é 61, cada caso aparece em apenas uma família. Não
interpretar skip como passagem. Os motivos literais completos permanecem no JUnit
privado e nos `skipif`/`pytest.skip` versionados.

| Família / quantidade local | Razão / testes | Próxima validação concreta |
| --- | --- | --- |
| Launcher · 8 | `test_launcher.py`: PowerShell Windows. | Job Windows com Python/PowerShell nativos; manter resultado por plataforma. |
| Executável frozen · 6 | `test_frozen.py`: Windows + `BEES_HOST_EXECUTABLE` opt-in ausente. | Construir o artefato conforme [host-package.md](host-package.md), apontar a variável para o `.exe` próprio e rodar os seis testes com HTTP/estado descartáveis. |
| Probe host/DLL · 2 | `test_probe.py`: probe Hyper-V somente leitura e DLL search nativos Windows. | Windows suportado; probe pode informar host indisponível e não demonstra criação de VM. |
| PowerShell/backend/ACL · 9 | Parser 5.1, protocolo/módulos PowerShell, ACL VMMS e guardas de arquivo nativos em `test_provisioning_backend.py`. | Passo nativo Windows existente e suíte Windows completa; hardware continua separado. |
| DACL de assets · 5 | `test_provisioning_assets.py`: DACL Windows literal. | Windows/NTFS com fixtures próprias e testes negativos de ACL. |
| Enrollment nativo · 12 | DPAPI/Fernet, sharing, disposition, handle exclusivo, ownership CRT e crash em close: `test_provisioning_enrollment.py`. | Windows sob identidade de teste; reabrir estado/handles e provar negativa sem usar credenciais pessoais. |
| Caminhos/volumes · 8 | `test_provisioning_root_store.py`: sintaxe Windows e tipo de volume. | Windows com paths/volumes de teste; não concluir compatibilidade de volume não exercitado. |
| Cofre DPAPI · 2 | `test_state.py` e `test_provider_vault.py`: DPAPI da conta Windows. | Conta Windows de teste e reabertura; não substituir por cifra Linux e marcar como DPAPI real. |
| Rename/junction/handle da raiz · 4 | `test_artifacts.py` / `test_provider_vault.py`: semântica Windows. | Windows com filesystem e privilégios necessários; se symlink não for permitido, registrar motivo e repetir em conta de teste habilitada. |
| Bootstrap TokenOwner · 5 | `scripts/tests/test_ci_tests.py`: token nativo Windows. | `scripts/run-ci-tests.py` no job Windows, comprovando dono das fixtures/filhos e restauração também em falha/interrupção. |

No Windows também existem casos POSIX que devem ser pulados por motivo explícito
(permissões/chave externa Linux); sua próxima validação é o job Linux/container.
Skips condicionais por ausência de privilégio de symlink exigem repetição em ambiente
adequado. **Hyper-V/VM real, modelo local e ambientes físicos não exercitados são
bloqueios de aceite separados**, não estão escondidos nessa contagem de 61.

## Recuperação e exportação de evidências

Nenhum dos scripts existentes consolidava run/jobs/logs. O novo
[scripts/ci-report.py](../scripts/ci-report.py) apenas consulta e consolida a CI;
continua reutilizando `check.sh`, `check.ps1`, `run-ci-tests.py` e
`check-container.py` para os testes. Não altera workflow, não dispara run e não
publica logs. Exemplo, com saída privada nova:

```sh
.venv/bin/python scripts/ci-report.py \
  --repo L0tus-Program/Bees \
  --run 38951214754 \
  --expected-sha a0eb7a3c6076d5783b0df97cdd30e0d3ae39da1c \
  --output /tmp/bees-ci-38951214754.json
```

O coletor exige os quatro jobs/etapas previstos no workflow, status final,
SHA/run/job/URL consistentes e logs não vazios com resumo reconhecido no passo
correto. Retém somente IDs, URLs públicas, conclusão, timestamps, hash/tamanho do
log e contagens separadas por passo; não exporta stdout/stderr, nomes de testes,
prompts, bootstrap, cofre ou dados pessoais. Alterar nomes/etapas do workflow exige
revisar o contrato do coletor; campo inesperado/ausente e resumo ambíguo falham.

Uma falha de `gh`, JSON truncado, job incompleto/falho, log ausente/truncado antes
do resumo ou erro de exportação retorna código diferente de zero e um erro fechado.
No bloqueio observado, o retorno é:

```json
{"status":"error","code":"ci_log_fetch_failed"}
```

Isso **não** produz `jobs: []`, contagens zero ou relatório “verified”. A saída só
é publicada completa e não sobrescreve evidência anterior; falha de escrita/link
mantém o destino anterior e remove o staging próprio. Testes de
[test_ci_report.py](../scripts/tests/test_ci_report.py) exercitam negativos de
metadados/run/jobs/steps, timeout, log indisponível/inválido/duplicado, resumo com
falha, exportação recusada e ausência de vazamento de conteúdo privado.

Arquivos temporários/JUnit/logs não devem ser commitados ou enviados como artefato
sem revisão. Mesmo metadados sanitizados só devem ser publicados quando necessários
à revisão. Um coletor aprovado não concede permissão para merge, release ou uso de
ambiente físico.
