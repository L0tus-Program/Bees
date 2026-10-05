# Perfis e memória

O perfil define nome, propósito, instruções permanentes e uso de memória de cada abelha. A interface permite editar esses campos e trocar a conexão do modelo, preservando a identidade e a conversa. Alterações usam revisão: uma tela desatualizada precisa recarregar antes de salvar, evitando sobrescrever outra edição.

## Configuração pela interface

No painel da abelha, abra Perfil, Modelo ou Memórias. O propósito e as instruções são enviados como contexto permanente. O recurso operacional configurável nesta etapa é o uso de memória; conversa por texto está disponível com um modelo configurado. Ferramentas, computadores e tarefas autônomas continuam em preparação e não recebem acesso por uma edição de perfil.

Trocar modelo usa preparação local da configuração, vinculada à sessão e à abelha; busca e teste de catálogo são opcionais. Escolha uma sugestão ou digite outro identificador em qualquer provedor. Para reaproveitar uma credencial salva, selecione isso explicitamente; o servidor só permite reutilizá-la no mesmo protocolo e endpoint. Alterar o endereço exige outra escolha de credencial, sem transportar a chave anterior automaticamente. A nova conexão receberá o contexto selecionado das próximas mensagens.

Uma abelha antiga sem conversa ativa recebe sua primeira conversa ao configurar o modelo. Históricos existentes não são recriados. Substituir ou remover uma referência de credencial da conexão não revoga a chave no fornecedor; referências antigas do cofre podem ser compartilhadas ou estar em uso por uma chamada já iniciada e não são apagadas automaticamente.

A [distribuição Compose](containers.md) inclui launcher Windows com primeiro acesso autorizado no navegador e cofre preparado automaticamente. O modo nativo mantém bootstrap/cofre como alternativas operacionais no [guia de acesso](onboarding.md). Setup gráfico nas demais distribuições e integração de ambientes continuam em implementação.

## Escopos e fontes

- **Pessoal:** preferência/informação usada pelas abelhas da identidade individual.
- **Abelha:** pertence apenas à abelha selecionada.
- **Tarefa:** pertence a uma tarefa daquela abelha. Só entra no contexto quando a chamada identifica explicitamente a tarefa e sua conversa.

A interface permite registrar, corrigir e apagar memórias. Escopo e vínculos de uma memória existente são imutáveis; para transferir uma informação, crie outra memória com o escopo desejado. Não há extração automática de fatos pelo modelo nesta etapa.

Preferências descrevem escolhas do usuário. Fatos exigem referência de fonte e data de observação; podem ser um link ou uma anotação de origem. A data fica em UTC no estado e aparece no fuso do navegador. Esses campos registram proveniência, sem verificar automaticamente a verdade ou a atualidade do conteúdo. A seleção preserva a informação com sua fonte/data.

Memória é dado de referência, separado das instruções permanentes. Guardar uma frase que pede acesso não concede acesso. O serviço só recupera registros ativos declarados pelo usuário; dados externos futuros precisam de um fluxo próprio de confirmação. Não inserir senhas ou chaves em memórias: o conteúdo selecionado pode ser enviado ao provedor escolhido.

## Seleção e janela da conversa

O Bees conserva todo o histórico no banco, mas envia uma janela recente ao modelo. O alvo é até 24 mensagens e 24 mil caracteres de mensagens serializadas; uma unidade indivisível com chamadas/resultados de ferramentas pode usar até 65 mensagens. Blocos são mantidos inteiros. Uma entrada/bloco mais recente que excede o limite de caracteres é recusado antes da gravação e da rede; não se corta texto para fingir que a mensagem foi enviada inteira.

Memórias são selecionadas deterministicamente por termos normalizados da consulta, especificidade do escopo e atualização. Preferências são elegíveis; fatos precisam de termos em comum. Não há serviço de embeddings, índice hospedado obrigatório ou chamada adicional de modelo. O envio usa até 20 memórias e 4 mil caracteres incluindo o envelope de contexto; entradas não são separadas de suas fontes para caber. O seletor interno aceita outros limites, mas a conversa usa esses limites fixos.

Essa estratégia não garante recuperação semântica perfeita nem lembrança de todo o histórico antigo. Fatos sem termos em comum e entradas fora do orçamento podem ficar ausentes. O histórico completo continua disponível no estado; resumo automático e busca semântica avançada não fazem parte deste checkpoint.

Desativar memória no perfil impede inserir memórias nas próximas chamadas. Não remove informações já citadas no histórico recente. As mensagens geradas registram metadados dos IDs/revisões de memória usados e contagens de contexto, sem duplicar o texto das memórias no ledger.

Nenhuma transação permanece aberta durante a rede. Se perfil, conversa ou memórias selecionadas mudam enquanto o provedor responde, a resposta antiga não é anexada. A mensagem enviada permanece no histórico e pode ter havido processamento/cobrança; a aplicação não repete automaticamente.

## Exclusão e limites

Apagar remove o registro canônico da memória e cria um evento com ID, revisão e escopo, sem copiar conteúdo ou fonte. A operação e o evento são confirmados juntos e exigem a revisão atual. A exclusão vale para as próximas seleções de contexto e persiste após reinício.

Isso não apaga backups, páginas livres/WAL do SQLite, informações já citadas nas conversas ou dados já enviados ao fornecedor. Não é uma promessa de apagamento forense. Retenção/exportação e ferramentas de manutenção seguem histórias próprias; [persistência](persistence.md) descreve o estado atual.

Memórias de tarefas já possuem contratos e isolamento, mas a execução de tarefas e a seleção de tarefa no chat operacional serão integradas com o executor. Criar um registro de tarefa para testes não comprova execução em segundo plano.

## Validação do checkpoint

Em Windows, `scripts/check.ps1` aprovou 390 testes Python e 46 testes web, com cinco skips de casos específicos de plataforma/symlink. Ruff, ESLint, typecheck e build passaram. API/core verificam persistência após reabertura, isolamento de tarefas, exclusão, procedência e conflitos durante chamadas de modelo. Linux/CI não foram executados localmente.

A interface foi conferida com identidade e provedor controlados descartáveis: edição de perfil, preferência pessoal, fato com fonte/data, correção de memória, inclusão/desativação do contexto, troca de modelo preservando a conversa e reload. A confirmação de exclusão pessoal explica o alcance para todas as abelhas; cancelamento foi validado no navegador e exclusão efetiva na API/core. pt-BR/en/es e largura mobile de 390 px foram conferidos sem overflow horizontal. Essa validação não comprova modelos reais nem execução de tarefas.
