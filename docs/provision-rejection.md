# Recusas definitivas do provisionador local

Uma parada global ou uma autorização alterada pode impedir `claim` ou `begin`
antes de o core criar o intent. O provisionador distingue essa recusa de uma
resposta perdida: somente HTTP 409 com envelope fechado
`{"error":{"code":"provisioning_global_stop","dispatch_allowed":false,"cached":false}}`
e código conhecido permite parar sem marcar o resultado como desconhecido.
O core persiste o UUID recusado antes de responder; repetir esse UUID continua
recusado após retomar a instalação. Um replay de sucesso nunca autoriza GO.

O adaptador valida status, tamanho, tipo de conteúdo, JSON sem chaves duplicadas,
campos exatos e tipos booleanos estritos. Um 409 comum, código desconhecido,
resposta inválida, 5xx, timeout ou perda da resposta preserva a quarentena. Não há
retry automático. Um replay de recusa (`cached:true`) também não fornece uma nova
prova para liberar uma operação local que já estava incerta.

No claim recusado, o ledger conserva ticket, UUID e owner e termina a aquisição
como `stopped`, sem claim nem journal. No begin recusado, o journal conserva o
UUID da etapa como `rejected`, sem effect ID, PID, resultado ou launcher. Etapas
anteriores confirmadas permanecem registradas. O supervisor termina `stopped`,
sem enviar `/unknown`. Uma nova tentativa precisa de outro plano/UUID e de nova
autorização humana; o mesmo plano/claim não pode ser adotado ou retomado.

## Formatos e migração explícita

Novas raízes usam ledger versão 3 e novos journals usam versão 2. A identidade
JSON continua no formato 1; `PRAGMA user_version` identifica o esquema SQLite.
Abrir, executar inventário ou verificar a raiz nunca migra arquivos.

Com serviço quiescido e backup privado verificado, a composição confiável pode
abrir o ledger v2, adquirir seu lock e chamar `migrate_for_rejections()`. A migração
só aceita histórico concluído e nenhuma requisição pendente. `stopped` e `unknown`
legados continuam bloqueados e impedem a migração; não são reinterpretados como
recusas comprovadas. Ledgers v1 primeiro exigem a migração explícita para aquisição.

Um journal v1 pode chamar `migrate_for_rejections()` sob seu lock somente sem
etapas pendentes. A transação reconstrói a tabela, conserva UUIDs/recibos e grava
versão 2 atomicamente. Etapas `unknown` não são reconciliadas por essa operação.
Execução em formatos antigos recusa antes de novo pedido de efeito e pede a
migração explícita. Nenhum comando de migração é exposto ao modelo.

Na versão 3, `stopped` significa término sem novo efeito incerto e permite outro
plano; `prepared`, `accepted`, `running`, `unknown` e pedidos pendentes mantêm o
bloqueio. Índices e consultas consideram o mesmo conjunto. O inventário da raiz
aceita operações em corrida `stopped` somente com etapa terminal `rejected` e
etapas anteriores `confirmed`, sem adotar arquivos órfãos ou apagar evidência.

## Evidência e limites

`apps/host/tests/test_provisioning_rejection.py` cobre envelopes adversos,
recusas de claim/begin, replay local, persistência e migrações explícitas.
`scripts/tests/test_provisioning_acquisition_interoperability.py` executa API
real em processo separado, core, SQLite, raiz, ledger e journals reais: parada
antes da aquisição e entre duas etapas, recibo anterior preservado e UUID
recusado depois de retomar. O backend de hardware é falso nessas interops.
Esses testes comprovam o contrato HTTP e a recuperação do fluxo local; não
comprovam instalação física de VM ou funcionamento de Hyper-V.
