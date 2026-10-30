# Decisões próprias para o recurso local de texto

BEES-009.1/.2 acrescenta aprovação humana à transformação de texto no processo POSIX próprio de BEES-010.1. O recurso oferece somente `text.normalize@1`, com `trim`, `upper` e `lower`. Não lê arquivos nem acessa rede, terminal, navegador ou aplicativos. O transporte real não representa uma VM ou sandbox para código arbitrário.

## Preparação e espera

Somente composição confiável cria `ResourceToolService` com uma `ResourceIdentity` local. O serviço exige tarefa com modo explícito `resource_tool_task`, instalação, extensão e concessão habilitadas e claim vigente. A API humana não prepara nem executa a ferramenta e não recebe um destino de execução do navegador.

Uma intenção `tool.intent` conserva, em metadata privada, argumentos validados, contrato, versão, agente/run, recurso/instalação/identidade, revisões da extensão/concessão/agente/controle e geração da parada global. O hash do snapshot e o digest das políticas são conferidos ao decidir e antes do efeito. A autorização de `model.generate` não permite esta operação; o canal textual não pode consultar ou decidir estas aprovações.

`transform_resource` pergunta por padrão. Quando precisa de revisão, a tarefa e o run entram em espera e liberam seus leases na transação que cria a aprovação. A autorização humana coloca a tarefa na fila; outro dono obtém um claim novo. O efeito concreto recebe um UUID derivado da intenção e o vínculo desse claim novo, sem alterar o vínculo imutável de uma ação antiga.

A espera expira em 15 minutos por padrão, com limite configurável de 1 a 3.600 segundos. Mudança de política, controle, contexto, concessão, versão, recurso ou geração de parada torna a decisão antiga inaplicável. Uma regra persistente inclui a combinação exata, inclusive hash da entrada, versões e revisões. A exceção dispensa somente as revisões de regras Perguntar apresentadas na decisão. Bloquear prevalece; alterar a regra remove a exceção anterior.

## Consumo e incerteza

Antes de enviar GO, uma única transação confirma a autorização da intenção, consome a aprovação, atualiza os limites e registra o início da ação concreta. O executor roda fora da transação. Rollback não conserva consumo ou contador parcial. A confirmação de `tool.intent` significa autorização utilizada; a conclusão do efeito pertence exclusivamente à ação `text.normalize`.

O limite é de 16 efeitos iniciados por tarefa, conservado em metadata canônica. Cada início também reserva conservadoramente seu prazo, de até 5 segundos, dentro do tempo ativo restante. A reserva não é devolvida nem representa faturamento do provedor; pausa, retomada e reinício não zeram os limites.

Perda de resposta conserva aprovação utilizada e efeito `outcome_unknown`, mesmo se existir recibo privado no executor. Replay consulta o registro conhecido; não repete o efeito. Revogar bloqueia usos futuros e revoga a regra criada pela aprovação, quando houver, sem desfazer um efeito iniciado. Cancelamento e retomada continuam sujeitos aos controles e à quarentena existentes.

## Canal humano e interface

Todas as rotas exigem sessão humana; mutações também exigem Origin/CSRF:

- GET `/api/v1/agents/{agent}/tool-approvals?task_id={task}&limit=50&offset=0`: lista sanitizada, sem entrada privada.
- GET `/api/v1/agents/{agent}/tool-approvals/{approval}`: revisão explícita, com texto privado limitado e hash verificável.
- POST `…/{approval}/decision`: UUID do pedido, revisão esperada e `allow_once`, `allow_rule`, `ask` ou `deny`.
- PATCH `…/{approval}`: UUID, revisão esperada e `status: revoked`.

Nenhuma escrita aceita argumentos, alvo, políticas ou autoridade do executor. CAS e UUID protegem concorrência e replay. A resposta inclui recibo com hash do comando exato e estado atual: um reenvio pode confirmar a decisão original e mostrar sua revogação posterior.

Na conversa e no detalhe da tarefa, o cartão começa sem conceder autorização. A pessoa abre a revisão somente leitura, confere texto, operação, recurso, identidade, consequências e alcance, escolhe a decisão e confirma. Alterar a proposta exige redirecionar/preparar outra intenção, sujeita a nova revisão. Não há rascunho de destinatário ou anexos neste adaptador.

Antes de enviar, a aba preserva somente comando, UUID e assinatura do alcance em `sessionStorage`, sem texto privado. Se o armazenamento falhar, não envia. Resposta perdida, inválida ou sessão expirada mantém o pedido pendente, sem retry automático, inclusive após reload. Verificar reenvia explicitamente o mesmo comando; consultar o estado é somente leitura. Descartar o pedido local exige consulta atual bem-sucedida e não desfaz decisões registradas. A interface verifica IDs, escopo, preview e recibo antes de anunciar sucesso.

Autonomia e `/autonomia` permitem consultar, editar efeito/nome/motivo e revogar regras exatas deste recurso. O formulário conserva o alcance completo, sem convertê-lo em permissão ampla. Edição remove a exceção das perguntas confirmadas. Outros adaptadores permanecem identificados como avançados.

## Persistência e limites

Não há nova migração: schema18 já representa ações, aprovações, regras e metadata privada. Exportações lógicas de operador conservam esses dados privados; projeções comuns, eventos, listagens e erros não incluem a entrada. Não coloque credenciais no texto a transformar.

O modo interno `resource_tool_task` deste checkpoint continua separado. BEES-010.2 acrescenta o [ciclo limitado em tarefas explícitas](tool-cycle.md), com coordenação na fila de produção e validação com servidor de modelo controlado. Não há recurso Windows, integração guest, VM ou adaptador de apps comprovado por esta entrega.

Os testes incluem mudança de autoridade e geração de parada, TTL, revogação, CAS concorrente, replay global de UUID humano, rollback no begin, limite durável, reinício e perda de resposta com contagem do efeito. Contratos HTTP rodam também no Windows; testes do processo POSIX são skips explícitos nessa plataforma.
