# Decisões humanas e autorizações

BEES-009 integra decisões persistentes ao executor de tarefas textuais. Quando uma regra exige **Perguntar**, o Bees prepara a geração, guarda uma pergunta e libera o worker sem enviar a geração ao provedor. Fechar a interface ou reiniciar o serviço conserva o pedido. Chat direto e CLI continuam aplicando políticas; não possuem este fluxo pontual de espera/retomada. Ferramentas, computadores e rotinas serão integrados quando seus executores existirem.

## Uso

Os cartões **Decisões da abelha** aparecem na conversa e no detalhe da tarefa. Cada cartão identifica tarefa, objetivo, resultado esperado, modelo, destino, ambiente, identidade, consequência e validade. A decisão vale por 15 minutos a partir da criação do pedido. O contexto preparado fica no Bees; credenciais e referências privadas não aparecem na pergunta.

| Escolha | Efeito |
| --- | --- |
| Permitir uma vez | Autoriza somente a geração preparada desta execução. Volta à fila automaticamente; a regra original continua exigindo decisão para outras ações. |
| Salvar autorização neste escopo | Salva uma regra para este agente, ação, destino, modelo, ambiente e identidade exatos. Cria uma exceção explícita somente às revisões das regras Perguntar apresentadas nesta decisão; bloqueios continuam valendo. Também coloca a geração aprovada na fila. |
| Sempre perguntar | Salva uma regra Perguntar neste escopo e mantém a tarefa esperando. Uma decisão posterior pode autorizar o mesmo pedido enquanto válido. |
| Bloquear | Salva uma negativa neste escopo e pausa a tarefa. Retomar não contorna essa regra. |

Escolher uma opção abre sua descrição de alcance; **Confirmar decisão** envia a escolha ao serviço. O resultado mostrado vem do servidor. Em erro ou resposta perdida, atualize e confira o estado: a interface não repete a decisão automaticamente. Uma decisão pontual aprovada pode ser revogada antes de ser usada, inclusive quando já expirou ou ficou obsoleta.

Regras salvas reaparecem em **Autonomia**. Na conversa, digite `/autonomia` ou use **Editar regras** para abrir o mesmo formulário de consulta, edição e revogação. O comando apenas abre o editor; cada mudança exige envio explícito do usuário. Respostas do modelo, memórias, sites e documentos não são comandos de autorização. Edição livre em linguagem natural ainda não é interpretada pelo serviço.

Uma permissão comum não substitui Perguntar ou Bloquear. A exceção criada por uma decisão humana é vinculada ao escopo aprovado e às regras observadas. Uma regra Perguntar nova ou editada exige nova decisão; uma negativa sempre vence. Editar qualquer campo da permissão salva desativa sua exceção, conservadoramente, e a interface informa esse efeito. Revogar a permissão impede sua reutilização; não desfaz gerações já iniciadas.

## Validade e execução

O pedido conserva `PreparedChat`, hash do snapshot, agente/revisão, execução, revisão de controle, redirecionamento, contrato da ação e hash das políticas. A validade é recalculada pelo servidor ao observar, decidir e despachar. Alterações de perfil, modelo, memória, histórico, alvo, controle ou política podem exigir outro pedido. O hash de políticas inclui todas as regras relevantes, portanto até uma edição não aplicável pode invalidar a autorização pontual.

Decidir mantém a mesma execução e o mesmo contexto preparado. **Retomar** é um controle diferente: pode criar outra execução, sem copiar os IDs de aprovação. Nenhum desses caminhos infere consentimento por texto de terceiros. Uma decisão expirada ou obsoleta não pode autorizar; revise as mudanças e use Retomar para preparar outro pedido quando necessário.

Depois do preflight Ollama, a guarda verifica novamente contexto, política, controle, lease e orçamento. O consumo da autorização, transição do journal de ação, início da chamada e contador são confirmados na mesma transação antes da rede. Se a transação falhar, nenhum consumo parcial fica salvo. Não há transação aberta durante a chamada externa.

Uma resposta perdida mantém `outcome_unknown`, sem retry automático e sem reutilizar a autorização consumida. Recuperação após morte do worker marca tanto a chamada quanto a ação como desconhecidas. Reconhecer esse risco e retomar não reaproveita o consentimento antigo. Revogação depois do despacho não cancela um efeito externo já aceito; o histórico conserva o consumo e o resultado conhecido ou desconhecido.

## API e persistência

- GET `/api/v1/agents/{id}/approvals?task_id=...&offset=0&limit=100`: listagem paginada, incluindo decisões anteriores e validade atual.
- GET `/{approval_id}`: pedido atualizado.
- POST `/{approval_id}/decision`: `client_request_id`, `expected_revision`, `decision` e `reason` opcional; `rule_name` opcional para regras salvas.
- PATCH `/{approval_id}`: `expected_revision`, `status: revoked`; UUID de envio opcional para replay de revogação.

Todos os endpoints exigem sessão humana; escritas também exigem origem e CSRF. A API não recebe snapshot, escopo, ator ou IDs de autoridade do cliente. UUID repetido com o mesmo conteúdo reconcilia a decisão atual; conteúdo diferente é conflito. CAS rejeita revisões antigas. Listagens não executam tarefas nem concedem acesso.

Usa tabelas `actions`, `approvals`, `policies` e eventos existentes, sem migração própria; BEES-009 foi entregue no schema 3. O schema 4 acrescenta contratos locais de ferramentas sem mudar este fluxo de aprovação textual. O vínculo de aprovação com regra pode ser criado uma única vez na decisão de autorização persistente e não pode ser substituído. Decisões, motivos, horários, ator e regras criadas conservam histórico em metadata privada, além dos eventos canônicos. Projeções públicas excluem snapshot preparado, hashes internos, referências de credencial e contexto completo.

## Validação e limites

Testes controlados cobrem precedência, escopos, expiração, revisão de parâmetros, memória e política, edição/revogação, concorrência/CAS/replay, decisão em outra abelha/execução, perda de resposta, mudança durante preflight e queda real de subprocesso. Nenhuma validação usa chave ou geração real de LLM.

O checkpoint textual não comprova isolamento de máquinas nem aprovação de efeitos de ferramentas. Esses aceites permanecem nas histórias de executores e ambientes. A listagem atual filtra registros no serviço; filtragem SQL por tarefa é uma otimização futura quando o histórico justificar. Referência OD-04 orientou a apresentação, com implementação própria de estado e segurança; nenhum código do Open Dots foi incorporado.

Checkpoint de 06/10/2026: 661 testes Python no Windows, 199 de frontend e 250 no alvo Linux aprovados, com 24/4 skips de plataforma. Ruff, ESLint, typecheck/build e imagens Docker passaram. O smoke Docker comprovou pedido preservado após reinício, autorização pontual consumida uma vez, regra exata reutilizada, CSRF e persistência após recriação. O servidor de protocolo descartável é reiniciado depois do serviço cuja namespace de rede compartilha.

Na interface descartável, `/autonomia` abriu o editor; uma regra Perguntar gerou um cartão na tarefa e na conversa. Reload preservou o pedido; Permitir uma vez concluiu a tarefa sem alterar a regra original. Textos conferidos em pt-BR/en/es; viewport de 390px sem overflow horizontal (client/scroll 375px). Evidências locais em `data/validation/bees-approval-used.png` e `bees-approval-mobile.png`, ignoradas pelo Git. A instalação padrão foi atualizada com identidade e cofre preservados; dados de teste permaneceram separados.
