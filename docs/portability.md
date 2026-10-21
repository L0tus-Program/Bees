# Portabilidade lógica privada

A exportação **BEES-023.1 (#78)** representa os registros canônicos atuais e os
blobs aceitos pelo Bees, em formato público de estrutura e privado de conteúdo.
Ela contém conversas, instruções, memórias e arquivos pessoais. Quem obtiver o
pacote poderá ler esses dados; guarde-o como documento privado. O pacote não é
criptografado, não é um backup do SQLite e não substitui o backup de manutenção
anterior às migrações descrito em [persistência](persistence.md).

## Operação humana

Com a sessão humana autenticada, envie `POST /api/v1/portability/exports` com
`{"consent":"export_private_data"}`, a origem aceita e o cabeçalho CSRF da sessão.
A resposta contém somente `id`, `status: "ready"`, SHA-256, tamanho e quantidades.
O pacote pode ser obtido em
`GET /api/v1/portability/exports/{id}/download`, também sob sessão humana.
O identificador não substitui autenticação. O download usa `no-store`, não executa
modelos, ferramentas ou rotinas e não cria outra exportação. Não existe URL pública
ou acesso pelo credenciamento de host/provisionador.

A operação é síncrona e limitada. Não exporte continuamente; o snapshot longo
mantém uma leitura SQLite aberta e pode atrasar checkpoints WAL. Cada solicitação
cria um identificador independente, sem alterar revisões ou eventos de produto.
A interface autenticada oferece **Meus dados** no cabeçalho, inclusive sem abelha
selecionada. A [restauração BEES-023.2 (#79)](restoration.md) publica somente uma instalação
nova e inativa, com revisão humana de acesso e reconexão obrigatória.

## Exportar pela interface

O painel informa que a cópia inclui todas as abelhas da instalação e contém dados
privados sem criptografia. A criação exige consentimento próprio, inicialmente
desmarcado. Depois da confirmação do servidor, **Última cópia confirmada** mostra
tamanho, registros e arquivos; **Baixar cópia verificada** é uma ação separada.
Antes de solicitar o download, o navegador confere resposta autenticada, nome,
tipo, tamanho e SHA-256, com limite de 256 MiB e prazo total de dois minutos para
o download/verificação. O ZIP é entregue como blob inerte, sem abrir seu conteúdo.
O aviso de download solicitado não comprova que o navegador o salvou em disco.

A aba guarda somente a referência confirmada e os metadados necessários à
verificação em `sessionStorage`. Reabrir ou recarregar não cria cópia nem baixa
arquivo automaticamente. A referência permite consultar o mesmo ZIP após reiniciar
a API. Logout e sessão inválida limpam a referência local; o arquivo no servidor
continua privado e exige uma sessão humana válida para download.

O endpoint de criação não tem UUID de comando ou consulta por intenção. Uma resposta
perdida pode deixar um arquivo pronto sem confirmação na interface. O painel conserva
essa incerteza após reload e preserva a última referência confirmada, quando existe.
Criar outra cópia exige consentimento novo e produz outro arquivo independente;
não é replay da criação anterior. Se não consegue guardar a intenção local antes
do pedido, a interface não envia a criação. Se o armazenamento falha depois de uma
confirmação válida, a cópia permanece disponível em memória e o painel pede baixá-la
antes de fechar/recarregar a aba.

**Esquecer referência desta aba** só remove os metadados locais. Não apaga os ZIPs
do servidor ou os arquivos já baixados. A interface não oferece listagem geral de
cópias antigas, exclusão, restauração automática ou ativação de autoridade. Esses
limites não alteram as operações de operador documentadas abaixo.

A validação da interface usa contratos fechados, streaming limitado, adulteração,
timeout, armazenamento bloqueado, resposta perdida e reabertura. Chromium com API
real, em pt-BR/en/es e 390/1280 px, verificou download, foco/teclado, temas claro/escuro,
restart da API e sessão revogada. Também conferiu a limpeza após criação recusada
com 401 e carregamento inicial sem sessão válida. Quatro pacotes próprios incluíram duas abelhas e um
blob aceito, com nove downloads conferidos; as 35 tabelas canônicas permaneceram
idênticas, sem tarefas ou chamadas de modelo. É uma prova com dados de teste, sem
aceite de continuação por modelo/ambiente real.

## Formato `bees.logical`, versão 2

O arquivo é ZIP32, sem compressão, criptografia, comentário ou volumes adicionais.
Não há extração de arquivos durante a verificação. Os únicos membros permitidos são:

- `manifest.json`, contendo formato/versão, revisão do banco de origem, identificador,
  instante UTC, inventário de tabelas/blobs, quantidades, bytes e hashes SHA-256,
  exclusões, quantidade de campos sanitizados e necessidade de reconexão;
- `data/{tabela}.jsonl`, com uma linha JSON UTF-8 por registro, em ordem de chave
  primária; os campos `*_json` continuam strings JSON, preservando o contrato atual;
- `blobs/{uuid}`, somente para artefatos com estado `ready`, com bytes/tamanho/hash
  idênticos ao registro correspondente.

O inventário fechado de colunas, tipos escalares, nulidade, chaves primárias e vínculos está em
[`bees_core.portability`](../packages/core/src/bees_core/portability.py), nas constantes
`COLUMNS`, `INTEGER_COLUMNS`, `NULLABLE_COLUMNS`, `PRIMARY` e `LINKS`. Não há SQL executável no pacote. Tabelas ou colunas
canônicas desconhecidas interrompem a exportação até revisão explícita do formato;
nunca se anuncia uma exportação silenciosamente parcial. Uma tabela operacional
nova também precisa ser classificada explicitamente em `EXCLUDED`.

Incluem-se agentes e configurações sanitizadas; conversas e mensagens; tarefas,
runs, comandos e journal de chamadas/ações; memórias, inclusive registros históricos
e tombstones; políticas e decisões de aprovação; artefatos, linhagens e comandos;
plugins e grants declarativos; orçamentos e uso; parada global; atenção e preferências;
skills com todas as revisões e eventos; rotinas com definições, cursores, comandos e
ocorrências; pedidos de ambientes; eventos de domínio; histórico de migrações.

As chaves estrangeiras simples e compostas e a identidade das chaves primárias são
verificadas antes da publicação e ao reabrir o pacote. As definições imutáveis de
skills/rotinas preservam o texto original e seu `content_hash`. Fontes, datas,
revisões e estados `outcome_unknown`, consumidos ou revogados permanecem históricos.
Eventos polimórficos podem apontar para fatos operacionais excluídos: o evento
continua evidência, sem transformar a referência em autoridade operacional.

## Fronteira de credenciais e autorização

A exportação não abre o cofre, arquivos de chave, variáveis de ambiente, logs de
processo ou diretórios do provisionador. Exclui tabelas de identidade, senha,
bootstrap, sessões, limites de login, emparelhamento/credenciais/recebimentos de host,
provisionamento, autorizações operacionais, efeitos, bindings e rejeições de transporte.
`cache_entries`, leases e jobs de transporte também ficam fora: são locais ou
regeneráveis, não memória canônica portável.

Campos JSON estruturados de senha, segredo, referência de segredo, token,
credencial, cookies, autorização e sessão tornam-se `null`. O binding de execução
operacional da ação torna-se `null`. O pacote informa a necessidade de reconexão;
não resolve `env:`/`vault:` nem copia valores cifrados portáveis. Configurações de
modelo e snapshots continuam documentos históricos sanitizados. Seus hashes de
requisição anteriores são evidência do pedido original, não uma autorização para
reenviá-lo ou hash recalculado do documento sanitizado.

Chaves/valores de JSON e textos/blobs com padrões reconhecíveis de token `bp_`/`bh_`/`bx_`, chaves de API, Bearer,
chave privada ou atribuição explícita de senha/credencial fazem a operação falhar
com `portability_sensitive_content`. O serviço também recusa sanitizar uma definição
imutável de skill/rotina se isso quebrar seu hash. Não se reescrevem textos pessoais
ou blobs silenciosamente. Nenhum detector identifica toda palavra secreta arbitrária
que uma pessoa tenha colocado em uma conversa; a exportação privada continua sendo
conteúdo sensível e precisa de revisão humana antes de compartilhamento.

Políticas, decisões e grants são memória/configuração histórica. Sua presença no
pacote não concede consentimento no destino. A restauração exige staging,
rotinas pausadas, hosts/sessões desconectados e revisão humana de acesso; não pode
resetar `unknown`, reutilizar aprovações consumidas ou publicar execução automática.

## Coerência, limites e falhas

O serviço mantém uma única transação SQLite de leitura desde o inventário até a
cópia dos blobs. Artefatos aceitos são imutáveis, e cada blob é relido sob o hash e
tamanho desse snapshot. Conteúdo acrescentado concorrente aparece por inteiro em
uma exportação posterior. Blob ausente, conteúdo divergente ou FK inválida bloqueia
esta tentativa. Rascunhos, falhas e tombstones são preservados como registros; seus
arquivos não aceitos não entram como blobs `ready`.

Por padrão, há limites de 256 MiB por pacote, 10.000 membros, 200.000 registros e
32 níveis de JSON. Limites inferiores podem ser configurados por `Limits` no serviço.
A verificação limita o diretório central antes de o leitor ZIP alocar seus membros,
recusa compressão, duplicatas, links, extras, traversal, versões desconhecidas,
inventário incompleto, grafo inválido e tamanho/hash divergentes. Campos do manifesto,
contagens inteiras sem coerção de booleanos, UUIDs e datas UTC são validados explicitamente;
política de conteúdo desconhecida também é recusada.

A montagem ocorre em `exports/staging/{uuid}/package.part`, dentro do diretório de
dados da instalação. Diretórios novos usam modo `0700` e arquivos `0600` no POSIX;
links/junctions nos caminhos são recusados. No Windows os arquivos permanecem no
perfil/diretório privado da instalação e seguem suas ACLs, como o restante do estado.
Depois de fechar, sincronizar e reabrir/verificar o pacote inteiro, a publicação
renomeia-o para `exports/ready/{uuid}.zip`. O download só consulta `ready` e verifica
o mesmo conteúdo que devolve. Uma interrupção deixa staging privado, com erro seguro
quando capturável, sem disponibilizar pacote parcial.

Não existe limpeza automática por tempo nesta versão. Pacotes prontos e stagings
com falha ficam retidos para revisão/recuperação e devem ser removidos explicitamente
pelo operador, com os serviços quiescidos quando necessário. Não publique esses
diretórios no Git, em issues ou em anexos de diagnóstico. Não use o import de outro
produto para tratar este ZIP como backup completo ou instalação pronta.

## Compatibilidade e evidência

O escritor atual gera versão 2, com esquema de origem 18 ou posterior conhecido,
incluindo `skill_process_origins`. O leitor aceita dois inventários fechados: v1
com schema17 e v2 com schema18 ou posterior conhecido. O pacote v1 não recebe
origens inventadas; ao restaurá-lo a tabela nova fica vazia. Ambos exigem histórico
de migrações de checksums conhecido. Uma nova tabela/coluna exige classificação e
revisão do contrato; mudança incompatível no pacote exige outra versão do formato.
Nenhuma migração é aplicada ao estado ativo ao ler/verificar uma exportação.
Vínculos de origem de skill também conferem checkpoint, mensagem, hash do resultado
e contrato da revisão humana, mesmo quando um pacote adulterado recalcula seus hashes.

Os testes focados estão em
[`test_portability.py`](../packages/core/tests/test_portability.py) e
[`test_portability_api.py`](../apps/api/tests/test_portability_api.py): usam SQLite e
arquivos reais, conteúdo acentuado, revisões imutáveis, exclusão de credenciais,
escrita concorrente, interrupção antes da publicação, limite, blob ausente/corrompido,
pacotes adulterados e sessão/CSRF/download privado. Não demonstram a restauração da
#79, recuperação física de VM ou transferência do cofre entre contas.

A interface **Meus dados → Restaurar uma cópia** também permite enviar um ZIP a um
alias novo e inativo já configurado pelo operador e consultar seu relatório sem
reenviar. Veja [restauração e respostas perdidas](restoration.md#restaurar-pela-interface).
