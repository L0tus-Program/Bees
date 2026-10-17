# Gestão humana de agendas

A entrega parcial de BEES-021.3 (#71) expõe a gestão das definições do
[contrato temporal](routines.md). A seção **Rotinas** fica na navegação da abelha,
com idioma português, inglês ou espanhol. A escolha dessa seção e da abelha
continua usando o contexto de navegação existente.

O painel informa que o agendador automático ainda não está disponível. Salvar,
revisar ou preparar uma agenda futura não inicia tarefa, modelo, ferramenta,
mensagem externa ou execução manual. `active` é apresentado como **Elegível para
agendador futuro**, com a mesma explicação na confirmação. O próximo horário é
uma previsão canônica consultada da API, não uma promessa de execução do worker.

## Criar, revisar e controlar

A criação parte de um procedimento da biblioteca de Skills e da conversa da
abelha. O formulário oferece nome, instruções, versão fixa do procedimento,
entradas declaradas, frequência diária/semanal, dias da semana, horário local,
fuso IANA, tolerância e condição de aviso. O ambiente vem da versão da skill:
a interface não o troca silenciosamente. Entradas opcionais vazias são omitidas.
Não há campos de credenciais ou autorização de ferramenta.

Antes de gravar, uma revisão concreta mostra instruções, revisão da skill,
entradas, frequência/horário/fuso, ambiente, conversa de destino, aviso e regra
de atraso/sobreposição. O usuário confirma esse pedido. Criar ou editar salva
uma revisão pausada; editar também cancela ocorrências pendentes. Voltar da
revisão conserva os campos editados.

Pausar, preparar elegibilidade futura e cancelar também passam por confirmação
com o efeito explícito e o CAS da revisão exibida. Cancelamento é terminal.
A preparação da agenda informa que a skill precisa estar habilitada e que o
agendador continua indisponível; a API valida a habilitação e a revisão atual.
Rotinas legadas são identificadas como metadados sem execução: não recebem um
controle de elegibilidade direta. Uma revisão completa pode convertê-las para
o contrato novo e pausado.

Quando a definição fixa uma revisão antiga da skill, a edição consulta essa
revisão imutável, preservando seus campos e entradas. Escolher outra skill usa
explicitamente sua versão atual e seu ambiente declarado. O destino atual é a
conversa da abelha; uma conversa já fixada na definição também pode ser
conservada. A API continua responsável por validar o escopo da conversa.

## Conflitos e resposta incerta

Cada intenção humana recebe um UUID. Edição e status carregam também a revisão
esperada. Duplo clique é bloqueado durante a operação; um conflito CAS atualiza
a consulta e exige uma nova revisão humana, sem substituir o CAS para reenviar
a intenção silenciosamente.

Uma falha de rede, timeout ou resposta de sucesso inválida deixa o resultado
incerto. O painel bloqueia outras mutações e conserva a mesma intenção/UUID em
memória e, quando disponível, em `sessionStorage`, separado por abelha. Isso
permite recuperar a intenção depois de recarregar o painel na mesma sessão do
navegador. Esse armazenamento temporário contém a definição e as entradas do
pedido; não é exportado, não concede autoridade e não deve conter segredos.
Assinaturas de credenciais conhecidas são recusadas antes de enviar ou guardar
o pedido, além da validação do serviço.

**Consultar e verificar o mesmo pedido** faz primeiro um GET da rotina conhecida
ou da lista, para uma criação. Se essa consulta falhar, não repete a mutação.
Se a consulta funcionar, verifica a intenção original pelo mesmo endpoint e
UUID idempotente. Não deduz confirmação comparando apenas campos parecidos.
O backend devolve a resposta original se a intenção já foi aplicada. Depois da
confirmação, o painel limpa a recuperação e consulta o estado atual.

Se o navegador bloquear o armazenamento opcional, a recuperação permanece na
aba e o painel informa para mantê-la aberta até confirmar o pedido. Nenhum novo
UUID é criado para tentar resolver uma resposta incerta.

## Consultas e histórico

Listas de rotinas, skills, ocorrências e revisões têm páginas de 50 itens,
carregadas por ação do usuário. Consultas não materializam ocorrências nem
iniciam execução. O histórico apresenta estados, motivo de skip, janelas de
indisponibilidade e snapshots humanos; não cria resultados ou atenção fictícios.

O próximo horário mostra o instante UTC retornado pela API no fuso declarado.
Ocorrências históricas usam o fuso da revisão correspondente quando seu snapshot
está carregado; caso contrário mostram UTC explicitamente. A interface não
recalcula DST, o próximo horário ou a decisão de atraso. O formulário explica
que horários locais inexistentes são pulados e horários ambíguos usam somente
a primeira passagem.

O layout adapta os campos de frequência/horário para uma coluna em telas
estreitas. Campos têm labels, botões nativos, grupos com legendas, estados de
carregamento e erros com foco; revisões usam `details`/`summary`. Testes DOM
verificam estrutura para teclado e textos nos três idiomas. A revisão real em
Chromium também cobriu os três idiomas em viewports de 390 e 1280 pixels, com
API/SQLite próprios e sem chamadas de modelo. Foram conferidos campos por label,
Tab/Enter, foco nas revisões/histórico/confirmação, criação/edição/status pela
API real e ausência de overflow horizontal. A mesma sessão validou consulta de
entregas, foco nos detalhes e download de um arquivo de demonstração verificado.
Nenhum estado ou serviço existente foi usado para esses dados de teste.

## Validação e pendências

Os testes do transporte e do painel verificam contratos fechados, isolamento,
UUID/CAS, revisão antes da escrita, criação pausada, pausa/cancelamento,
elegibilidade futura sem execução, recuperação de resposta perdida com o mesmo
pedido após remount, GET obrigatório antes do retry, armazenamento bloqueado,
resposta inválida, conflito, histórico paginado, fuso da revisão, texto escapado,
traduções e controles nativos. TypeScript e ESLint validam os novos arquivos.

A #71 permanece **Entrega Parcial**. Não estão entregues o scheduler/claim e
execução da #70, execução manual, propostas de agenda pela conversa, projeção de
atenção/avisos de resultados reais nem o aceite de rotina com aprovação pendente.
Essas dependências exigem a execução real de revisão de skill (#67) e integração
com as políticas/entregas existentes. Os controles de definição não comprovam
esses comportamentos.
