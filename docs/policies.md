# Autonomia e políticas

BEES-008 entrega avaliação determinística de regras e sua aplicação nas gerações de chat e de tarefas textuais. Regras são estado canônico do Bees, com revisão e auditoria, independentes do fornecedor. BEES-009 acrescenta [decisões pontuais de tarefas](approvals.md) e regras persistentes pela conversa. Ferramentas e acesso a computadores continuam nas histórias dos executores e ambientes reais.

## Uso pela interface

Abra **Autonomia** na abelha e crie uma regra para gerar respostas. Escolha a conexão/modelo atuais ou todas as gerações da abelha e um efeito:

| Efeito | Comportamento |
| --- | --- |
| Permitir | Executa dentro do escopo se nenhuma regra restritiva também se aplicar. |
| Perguntar | Impede a geração e deixa a tarefa aguardando decisão. O cartão da tarefa permite uma decisão pontual ou regra de escopo explícito; também é possível editar a regra em Autonomia. |
| Bloquear | Impede a geração e pausa a tarefa, sem consumir uma chamada. |

Sem regra restritiva, gerar texto com a conexão selecionada é permitido. Isso não autoriza ferramentas ou acesso ao computador. A regra por conexão/modelo permanece vinculada ao destino e identificador exatos; trocar o provedor não transfere a regra silenciosamente. Para bloquear gerações também após mudanças de modelo, escolha todas as gerações da abelha.

Regras globais e da abelha são avaliadas juntas. **Qualquer negativa aplicável vence; perguntar vence uma permissão comum.** Uma regra salva por decisão humana pode excluir apenas as revisões de Perguntar autorizadas naquele escopo; [operação e limites](approvals.md). Uma permissão específica não anula uma negativa geral. Edite ou revogue a restrição para abrir uma exceção. Campos vazios abrangem qualquer valor, sem regex, glob ou interpretação especial de `*`.

Editar e revogar exige a revisão atual. A interface não repete mutações automaticamente; se a resposta se perder, atualize os dados antes de tentar novamente. Criações têm ID idempotente e alterações usam CAS. A listagem tem paginação; regras de páginas posteriores continuam aplicadas pelo executor e podem ser consultadas e revogadas.

Alterar uma regra não executa automaticamente uma tarefa parada: use **Retomar** depois de revisar a regra. Retomar não equivale a autorizar; o executor avalia novamente as políticas atuais. Uma regra revogada permanece no histórico e sua retirada pode restaurar a permissão padrão de gerar texto. A interface também permite revogar regras próprias de escopos avançados ou legados que não podem ser editados neste formulário. Regras globais são somente leitura nesta interface.

## Fronteira de execução

`PolicyService` usa um registro explícito de ferramentas/ações, criado por código confiável. O registro operacional inicial contém apenas `model.generate`. Ação desconhecida, parâmetros incompatíveis, agente inativo ou regra inválida produzem negativa. Novos adaptadores devem declarar contrato, versão, efeito padrão e requisitos de autoridade; registrar um nome não comprova capacidade nem isolamento.

O executor monta agente, ambiente, recurso/destino e identidade fora dos argumentos do modelo. Parâmetros da ação são validados por contrato antes da avaliação. O escopo aceita igualdades exatas por ferramenta, ação, ambiente, recurso, identidade e valores completos dos parâmetros, respeitando tipos. Não é um filtro de caminho nem uma garantia de confinamento de shell/desktop.

Nas gerações atuais, o serviço obtém o agente e sua configuração canônicos, ambiente `control_plane`, destino `endpoint` e identidade individual `bees_user`. O modelo e hash do pedido normalizado vinculam os parâmetros concretos à avaliação. Segredos não fazem parte do pedido nem da política; o hash não inclui seu valor. Mensagens, memórias, documentos e respostas não são canais de edição de autorização.

O worker consulta as regras antes do despacho e novamente no adaptador, imediatamente antes da tentativa de geração. Ollama mantém seu preflight local obrigatório: uma restrição, pausa, cancelamento, redirecionamento ou mudança de contexto durante essa consulta impede a geração antiga. O journal passa de `prepared` a `dispatch_started` somente após essa última validação, junto do contador e da evidência da decisão. Transações terminam antes da rede.

Revisões/hash das regras ficam vinculados à decisão para auditoria; não são uma autorização reutilizável. Todas as próximas ações consultam regras atuais, inclusive redirecionamentos. Uma edição após iniciar o despacho não pode desfazer um efeito no provedor. Resposta perdida continua `outcome_unknown`, sem repetição automática, mesmo que a política tenha mudado durante a chamada. A garantia de autorização lineariza no registro do despacho: há uma janela inevitável entre commit local e efeito externo; não há promessa de transação distribuída.

Gerações pelo chat comum e pela CLI `bees-model chat` passam pela mesma política. Drivers de protocolo isolados são APIs internas, sem identidade Bees: aplicações que os usem diretamente devem compor sua própria fronteira de autorização. O mesmo se aplica a repositórios de persistência; não são endpoints de execução públicos.

## API e persistência

GET `/api/v1/agents/{id}/policies?offset=0&limit=100` retorna regras globais e da abelha, `has_more` e `next_offset`. POST cria uma regra com `client_request_id`; PATCH `/{policy_id}` altera campos com `expected_revision`. Escritas exigem sessão humana, origem e CSRF; o cliente não fornece agente/autor/origem de autoridade no corpo. Não há endpoint exposto a documentos/plugins/modelos para conceder acesso.

Campos de criação: `name`, `effect` (`allow`, `ask`, `deny`), `scope`, `reason`. Escopo: `tool_name`, `action`, `environment_id`, `resource`, `identity` opcionais e `parameters` com igualdades. Atualização também aceita `status` (`active`, `revoked`). A API não devolve metadata interna de idempotência ou erros brutos de validação.

Usa tabelas `policies` e eventos existentes, sem nova migração. Fonte e autor das mutações do serviço são fixos `user`; o executor não grava concessões. Avaliação percorre todas as regras relevantes, sem aplicar o limite de uma página de UI. Regras legadas inválidas falham de forma restritiva, mas podem ser revogadas; reativar exige um escopo válido.

## Validação e limites

Testes usam SQLite e HTTP controlado, sem chave ou geração real de LLM. Cobrem precedência, escopos completos, parâmetros alterados, autoridade injetada, regras depois de mil registros, CAS/replay concorrente, auditoria/reinício, revogação entre avaliação e efeito, preflight Ollama, controles durante preflight, efeitos aceitos com resposta perdida e redirecionamento após mudança de política. Casos de UI/API incluem sessão/CSRF/origem e consulta/edição de páginas posteriores.

O checkpoint de 05/10/2026 aprovou 612 casos Python no Windows, 161 de frontend e 201 no alvo Linux, com 24/4 skips de plataforma. Ruff, ESLint, typecheck/build e imagens Docker passaram. O Compose descartável comprovou espera/bloqueio sem geração, revogação seguida de retomada, regras/resultado/cofre após restart/recreate e proteção CSRF. A interface comprovou cadastro, recuperação após reload, edição e revogação confirmada, usando somente conta e servidor de protocolo de teste.

BEES-008 permanece em andamento para comprovar a aplicação online no executor de ferramentas/ambientes reais. BEES-009 entrega perguntas concretas para tarefas, aprovação única vinculada a ação/snapshot/revisão e edição pela conversa; Retomar continua sem conceder essa autorização. Rotinas ainda não executam e usarão a mesma avaliação antes de cada efeito quando implementadas. Aceite com modelos reais permanece separado em BEES-005.
