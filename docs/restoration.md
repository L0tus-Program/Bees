# Restaurar em uma instalação inativa

BEES-023.2 (#79) restaura um pacote [lógico v1 ou v2](portability.md) em um diretório
novo escolhido pelo operador. Não sobrescreve o serviço atual, não faz merge de
identidades e não inicia processos no destino. A seleção do destino pertence à
configuração confiável; o ZIP e a API nunca fornecem um caminho para executar ou
escrever livremente no sistema.

## Configurar e confirmar o destino

O operador pode iniciar o serviço com um alias e um caminho absoluto de destino:

```sh
.venv/bin/bees-api --restore-destination recuperada=/caminho/privado/nova-instalacao
```

No Windows, use o caminho absoluto do perfil do operador, por exemplo
`--restore-destination "recuperada=C:\Users\Pessoa\BeesRecuperado"`. O diretório
final deve inexistir. Aceitam-se até dez destinos distintos; duplicatas,
sobreposição entre destinos ou com a instalação ativa são recusadas. Não aponte
para uma pasta pública, sincronizada ou de outra pessoa. O argumento não muda as
regras de bind, TLS, sessão ou CSRF. Sem configuração, a API retorna
`restoration_not_configured`, sem escolher uma pasta automaticamente.

Com sessão humana, Host/Origin aceitos e CSRF:

- `GET /api/v1/restoration/destinations` lista apenas aliases e disponibilidade;
- `POST /api/v1/restoration/destinations/{alias}/restore` recebe o ZIP verificado,
  `Content-Type: application/zip` e o cabeçalho explícito
  `X-Bees-Restore-Consent: restore_inactive_installation`;
- `GET /api/v1/restoration/destinations/{alias}/report` consulta o relatório fechado
  sem criar outra restauração, ativar processos ou conceder acesso.

A autenticação precede a recepção do corpo. O upload tem limite total do verificador
(por padrão 256 MiB), prazo total de 30 segundos e uma vaga por serviço. Comprimento
inválido, mídia incorreta, origem hostil, falta de consentimento e excesso são
recusados. Os demais endpoints conservam o limite global de JSON. A interface autenticada **Meus dados → Restaurar uma cópia** usa estes endpoints. A composição Python
`RestoreService(verifier, {alias: Path(...)})` já oferecem a operação explícita.

O retorno de sucesso significa **instalação inativa publicada**, não recuperação
operacional concluída. Se a resposta se perder, consulte o relatório do mesmo alias.
Um novo POST nunca substitui o diretório já publicado. Não escolha outro destino
apenas para contornar um resultado incerto.

## Restaurar pela interface

Em **Meus dados → Restaurar uma cópia**, consulte os destinos configurados, escolha
um alias e consulte seu relatório. Um relatório existente mostra a instalação
inativa publicada; um destino ocupado não é substituído. Sem relatório e com destino
disponível, selecione o ZIP (não vazio, até 256 MiB) e marque o consentimento explícito
antes de enviar. O navegador envia o arquivo completo com sessão e CSRF, sem abrir
seu conteúdo; o servidor conserva a validação integral e os controles acima.

Durante o envio, seleção e comandos ficam bloqueados. A referência do alias é
conferida e guardada na aba antes do POST; armazenamento indisponível bloqueia o
pedido. A interface não guarda o ZIP, conteúdo, credenciais ou CSRF nesse registro.
Uma resposta perdida conserva o alias e bloqueia outro envio, inclusive após fechar
o painel ou recarregar. Consulte o relatório **do mesmo alias**. Um 404 não prova
que a tentativa falhou e não libera o bloqueio de um envio incerto. Se o relatório
continuar indisponível, o operador precisa conferir o destino e os processos;
não use outro alias para contornar a incerteza. Uma recusa conhecida anterior à
publicação exige nova consulta e consentimento antes de uma tentativa corrigida.

Sair ou perder a sessão limpa a referência privada da aba, sem cancelar uma operação
já recebida nem apagar o destino. Depois de entrar novamente, consulte o relatório
do alias usado antes de decidir qualquer envio. Reabrir/recarregar nunca envia o
ZIP automaticamente. O relatório apresenta totais, transformações de segurança,
IDs/hash e os nove pontos de revisão humana; sua presença não configura identidade,
reconecta modelo/host, retoma trabalho nem conclui o aceite operacional.

## O que é preservado e o que precisa de revisão

IDs, textos, fontes, datas, versões, hashes e decisões históricas seguem o pacote.
O ZIP original validado fica em `source.zip`, com hash no relatório; não é um dump
do cofre nem do estado privado do provisionador. Não houve limpeza do histórico.
As revisões do pacote formam o baseline da instalação nova: as transformações de
segurança abaixo não fingem comandos executados no serviço de origem. O relatório
registra tabela, ID, motivo e estados de origem/destino sem copiar texto pessoal ou
caminhos privados. O pacote original conserva a evidência anterior às transformações.

| Registro atual no destino | Regra de restauração |
| --- | --- |
| Identidade, senha, sessões, bootstrap, hosts e provisionador | Não são importados. Nova configuração e reconexão humanas são necessárias. |
| Agente e configuração de modelo corrente | Agente ativo fica pausado; configuração corrente vazia exige reconexão explícita. Snapshots históricos sanitizados permanecem. |
| Plugins, grants e skills | Desabilitados, sem habilitação por importação. Definições e revisões permanecem íntegras. |
| Política allow ativa | Revogada para revisão. Deny e decisões históricas não viram concessão. |
| Aprovação pendente/aprovada não consumida | Revogada. Consumidas, negadas e revogadas conservam evidência e estado. |
| Tarefa/execução não terminal | Pausada; intenção de cancelamento permanece cancelamento. Não entra na fila ao retomar somente a parada global. |
| Ação/chamada preparada | Cancelada; não é um novo despacho. |
| Ação/chamada já despachada | `outcome_unknown`; resultado desconhecido anterior continua desconhecido e não é repetido. |
| Reserva ainda aberta | `unknown`, conservando a reserva/custo; não se libera orçamento por restaurar. |
| Rotina e cursor | Rotina ativa fica pausada e próximo horário é retirado. Revisões/definições são preservadas; ocorrência pendente cancela, em execução vira unknown. |
| Ambiente em provisionamento | `outcome_unknown`; nenhum host/credencial/conector é reconectado. |
| Parada global | O destino nasce parado, com uma transição local válida. Comandos de origem permanecem históricos, sem reexecutar uma retomada. |

Antes de usar o destino, a pessoa deve configurar a nova identidade, reconectar o
provedor e revisar acesso, tarefas, rotinas e resultados desconhecidos. Habilitar uma
skill, conceder uma ferramenta ou retomar uma tarefa continua exigindo os controles
humanos próprios. Retomar globalmente não reabilita grants ou rotinas, nem repete uma
ação. A recuperação de um resultado físico desconhecido pertence ao seu executor;
importar o core não restaura nem apaga o journal privado do host.

## Staging, validação e publicação

O serviço verifica os mesmos bytes que depois interpreta: formato/versão, inventário,
tipos, limites, IDs, FKs lógicas, hashes, blobs e exclusão de segredos do contrato
v1/v2. Não extrai nomes arbitrários do ZIP. Cria um staging irmão privado e uma base
nova com as migrações conhecidas. A carga tem ordem explícita para os triggers e
ocorre em uma transação; não desativa FKs ou gatilhos. Linhagens com predecessor
removido reconstroem a transição legítima dentro dessa transação inédita, preservando
o tombstone final. Não se repara o banco ativo para aceitar conteúdo inválido.

Depois de carregar, o serviço verifica integridade/FKs, reabre o banco, relê cada
blob, confere ausência de autoridade operacional e sincroniza arquivos/diretórios.
A publicação do diretório inteiro é atômica e não substitui destino existente,
inclusive numa corrida com diretório vazio. Linux usa `renameat2(NOREPLACE)`;
Windows usa rename que recusa substituição. Outro POSIX sem a primitiva exigida
falha fechado, sem fallback que possa sobrescrever uma instalação.

Falha ou interrupção anterior à publicação conserva somente staging privado e um
código de erro quando capturável. Não há limpeza automática; staging, pacote original
e relatórios podem conter dados privados e devem permanecer fora do Git e de anexos
públicos. Relatórios corrompidos ou com evidência original divergente são recusados.
A evidência não é atestação contra um administrador que controle a conta e os arquivos.

## Verificação

[Testes do core](../packages/core/tests/test_restoration.py) exercitam SQLite/blobs
reais, concorrência, interrupção antes da publicação, destino ocupado, corrupção,
linhagem, unknown, decisões e ausência de trabalho retomável automaticamente.
[Testes HTTP](../apps/api/tests/test_restoration_api.py) verificam sessão/CSRF antes do
corpo, consentimento, limites de upload, timeout total, exclusão mútua e leitura do
relatório. API/worker em processos separados também conferiram um pacote próprio:
IDs/mensagem/blob preservados, destino pausado/sem identidade, configuração vazia,
skills desabilitadas e parada global, sem chamada de modelo ou efeito de hardware.
A continuação com modelos remoto/local e ambientes reais permanece na #80.
