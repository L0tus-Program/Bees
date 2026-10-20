# Biblioteca de procedimentos

Na aba **Skills** de uma abelha, salve um procedimento com objetivo, entradas,
passos, recursos necessários, entrega esperada e critério de conclusão. A biblioteca
pertence à instalação e à abelha selecionada; trocar o modelo não transfere nem
reescreve as versões. Não inclua senhas, chaves ou outros segredos no conteúdo.

## Revisão e habilitação

**Novo procedimento** salva a versão 1 desabilitada. **Criar nova versão** conserva
as versões anteriores e também desabilita a nova versão. Para habilitar, escolha
**Revisar e habilitar**, confira o procedimento completo e confirme **Habilitar esta
versão**. Desabilitar conserva o histórico.

Essa biblioteca cobre BEES-020.1 (#65), a conversão humana de BEES-020.2 (#66) e a
reexecução limitada de BEES-020.3 (#67), descrita abaixo. Habilitar uma versão não
inicia uma tarefa, altera memória, concede uma ferramenta ou autoriza acesso ao
computador. Declarações de ambiente e ferramentas descrevem compatibilidade;
políticas e concessões continuam sendo avaliadas antes de cada efeito.

**Revisões** permite consultar todas as versões em páginas de até 50 registros e
exportar uma versão específica. O conteúdo, o hash, a data e a origem de cada revisão
permanecem consultáveis após reiniciar o serviço. A origem identifica uma edição
humana ou uma cópia importada; uma edição de uma cópia ganha origem humana, sem
alterar a origem da revisão importada anterior. A biblioteca não atribui ao usuário
uma validação com modelo ou ambiente real que ainda não aconteceu.

## Reexecutar em tarefa nova

**Reexecutar** apresenta a revisão habilitada, passos, entradas e critérios antes
de **Criar tarefa com estas entradas**. A tarefa nova conserva definição, versão,
hash e entradas; não copia uma resposta anterior como nova entrega. Seu resultado
e as decisões aparecem abaixo do formulário e nas tarefas da conversa. No detalhe,
**Consultar passos e entradas desta tarefa** lê explicitamente o snapshot original,
inclusive depois de editar ou desabilitar a skill. As listas não expõem as entradas.

O recorte disponível usa o [ciclo limitado existente](tool-cycle.md): procedimentos
`none` sem ferramentas ou `own` com somente `text.normalize`. Até procedimentos
sem ferramenta exigem atualmente o runtime preparado desse ciclo. São necessários
modelo com capacidade de ferramentas, worker compatível ativo, recurso POSIX
preparado pelo operador e instalação/grant atuais. Apps, navegador, VM e máquina
pessoal permanecem indisponíveis nesse recorte; não há troca automática de ambiente.

Entradas precisam corresponder aos nomes obrigatórios/permitidos da revisão;
cada valor tem até 2.048 caracteres e o pedido até 8.192 bytes UTF-8. O limite é
quatro gerações, dezesseis propostas de ferramenta e 120 segundos. A geração pode
consumir o modelo configurado. Declarar passos não comprova que o resultado atende
aos critérios; confira a entrega e as evidências da ferramenta.

Revisão/entradas não mudam por redirecionamento. Editar/desabilitar a skill bloqueia
novas tarefas e os próximos despachos das tarefas existentes, conservando o snapshot.
Revogar grant ou desabilitar a instalação também bloqueia o efeito. Antes da geração
e do efeito, a transação curta revalida os vínculos; nenhum await ocorre sob o writer.
O snapshot de skill também integra o alcance de regras salvas pela decisão humana:
outra versão ou entrada não reaproveita essa decisão. Regras amplas explicitamente
criadas pela pessoa continuam seguindo a política atual; a skill não cria nenhuma.

Criação usa `POST /api/v1/agents/{agent_id}/skill-tasks` com `client_request_id`,
`skill_id`, `skill_version`, `inputs`, `max_calls` e `max_active_seconds` opcionais.
Sessão, Host/Origin/CSRF são obrigatórios. UUID idêntico/corpo idêntico retorna a
mesma tarefa mesmo após indisponibilidade do worker ou desabilitação da skill;
outro corpo, abelha ou canal textual/de ferramenta gera conflito. O recibo contém
UUID e hash do pedido normalizado. Não há parâmetros HTTP para conceder recurso,
fornecer uma definição alternativa ou satisfazer aprovação.

A aba preserva o pedido antes de enviar. Perda de resposta, inclusive após commit,
reload ou 2xx inválido, exige **Verificar o mesmo pedido** com o UUID original;
não há POST automático. Sem armazenamento da aba, o envio não começa. Descartar
recuperação não cancela uma tarefa já criada. Entradas privadas ficam no pedido
da aba até confirmação; não inclua credenciais. Fechar a aba pode perder esse apoio.

`GET /api/v1/agents/{agent_id}/tasks/{task_id}/skill-context` lê o snapshot privado
sob sessão e escopo da abelha, sem ativação, tarefa ou efeito. A interface verifica
o hash completo contra o vínculo recebido no detalhe. Persistência usa os metadados
da tarefa existente; não há nova migração. Restart/espera/unknown reutilizam o
journal, orçamento e decisões separados de geração e ferramenta do ciclo comum.

Testes SQLite/provider controlado/processo POSIX cobrem duas entradas com resultados
distintos, restart, criação concorrente, replay, entradas inválidas, edição,
desabilitação/revoke antes do efeito e regra salva de outro snapshot. Isso não
substitui a escolha de ferramenta por LLM real ou o aceite humano da qualidade.
O [scheduler e a execução manual de rotinas](routines.md) usam esse mesmo
ciclo no recorte disponível, com ativação programada explícita.

## Criar a partir de um processo

**Criar a partir de um processo** busca resultados concluídos desta abelha no
histórico, inclusive os antigos. Artefatos e resultados parciais não são candidatos.
Abra o resultado original para conferir o processo e escreva o template reutilizável:
entradas, passos, recursos, entrega e critério de conclusão. O editor começa vazio;
o resultado privado não é copiado automaticamente. Generalize dados pessoais e
remova segredos antes de salvar. Instruções de fontes externas são dados não
confiáveis, sem autoridade para conceder acesso.

Registre de uma a dez URLs presentes no resultado, o que você conferiu e os limites
ou pendências. Confirme a revisão humana e salve a versão desabilitada. O servidor
confere o vínculo exato abelha/tarefa/execução/mensagem e o SHA-256 UTF-8 do resultado.
Exige uma execução concluída, mensagem assistant indicada pelo checkpoint e fontes
presentes nesse texto. Isso identifica a origem; não verifica a verdade da fonte,
acessa suas URLs nem atribui uma avaliação de qualidade ao modelo. A pesquisa real
e sua validação continuam nos aceites de BEES-019.

Em **Revisões → Consultar origem do processo**, veja os IDs/hash, fontes, conferência
e limites. A origem fica vinculada à versão criada, conservada após reload/restart
e edições posteriores. Uma nova edição não herda essa confirmação: a versão anterior
mantém a origem e a nova versão tem origem humana comum. A habilitação exige a
revisão atual pelo fluxo separado já descrito. Nenhum grant, aprovação, regra,
tarefa, geração ou efeito é criado pela conversão.

A interface preserva o UUID e o conteúdo do pedido em sessionStorage antes de enviar.
Se a resposta se perde, inclusive após reload, **Verificar o mesmo pedido** reconcilia
a intenção original; não há reenvio automático nem outro UUID. Uma resposta 2xx
inválida também continua incerta. Sem armazenamento de aba disponível, o envio
não começa. O armazenamento contém o template e as referências revisados, sem o
resultado original. Não compartilhe dados locais da aba como diagnóstico público.

A migração `0018_skill_process_origins.sql` acrescenta a origem imutável e protege
o vínculo também com FKs/gatilhos. Antes do upgrade, pare os escritores e preserve
o backup. Nenhuma skill anterior recebe uma origem inventada ou é habilitada.
Exportação lógica v2 e restauração inativa conservam a origem e o resultado; o
documento isolado `bees.skill/v1` continua copiando somente o procedimento, sem
afirmar que outra instalação validou o processo local.

## Importação e exportação

**Exportar versão** baixa um documento declarativo JSON `bees.skill/v1`, com escopo
`agent`. Ele contém somente o procedimento; não inclui IDs internos, habilitação,
credenciais, memória, concessões ou autorização de instalação. Importar cria uma
cópia independente e desabilitada para a abelha selecionada. O conteúdo é apresentado
para revisão antes de confirmar a gravação. A importação não executa instruções.

Exemplo de formato:

```json
{
  "format": "bees.skill/v1",
  "scope": "agent",
  "definition": {
    "name": "Resumo com referências",
    "purpose": "Resumir o material fornecido preservando as referências.",
    "inputs": [
      {"name": "material", "description": "Material a resumir.", "required": true}
    ],
    "steps": [
      {"title": "Ler", "instruction": "Identificar afirmações e suas referências."},
      {"title": "Resumir", "instruction": "Escrever o resumo sem inventar fontes."}
    ],
    "resources": {"environment": "none", "tools": []},
    "output": "Resumo editável com referências.",
    "completion": "Cada afirmação relevante pode ser conferida no material."
  }
}
```

O contrato aceita até 20 entradas com identificadores únicos, 30 passos, 20
identificadores únicos de ferramenta e ambientes `none`, `own` ou `personal`.
Nome/título têm até 100 caracteres; descrição/instrução/saída/conclusão têm até 2.048.
A definição tem até 32.000 bytes UTF-8 e o documento até 32 KiB. Campos extras, como
`code`, `script`, `command`, `grants` ou `secret_ref`, são recusados. Padrões conhecidos
de tokens e chaves privadas também são recusados; essa defesa não detecta todos os
segredos possíveis e não substitui a revisão do conteúdo. Texto e instruções são
mostrados como texto escapado, sem HTML executável.

## Concorrência, rede e persistência

Toda mutação exige sessão humana, Host/Origin/CSRF, UUID próprio do pedido e, para
edição/habilitação, a revisão de estado observada. A habilitação exige também a versão
exata que foi revisada. Escritores concorrentes não sobrescrevem silenciosamente um
ao outro: um recebe conflito e precisa carregar/revisar o estado atual.

O mesmo UUID com os mesmos dados retorna o resultado original sem repetir a escrita.
Reutilizar o UUID com outro alvo, abelha ou conteúdo resulta em conflito. O resultado
de replay é histórico: a interface recarrega a lista para apresentar o estado atual.
Repetir uma habilitação antiga não reabilita uma versão editada ou desabilitada depois.
Quando a resposta se perde, a interface bloqueia novas mutações até **Verificar o mesmo
pedido**, reutilizando o UUID original. Não há repetição automática, chamada ao modelo
ou custo de provedor nesse caminho.

A migração `0014_skills.sql` cria `skills`, `skill_revisions`, `skill_commands` e
`skill_events`. Revisões, pedidos e eventos são imutáveis. A ligação à abelha não pode
ser transferida, e a atualização do conteúdo limpa a habilitação na mesma transação.
O journal de comandos e o evento humano são confirmados junto do conteúdo. Leituras
não alteram estado. Siga a manutenção quiescida com backup descrita em
[persistência](persistence.md) antes de migrar uma instalação existente.

As rotas ficam em `/api/v1/agents/{agent_id}/skills`: criação/listagem, `/import`,
`/{skill_id}`, `/{skill_id}/revisions`, `/{skill_id}/revisions/{version}/export` e
`/{skill_id}/enabled`. As listagens aceitam `limit` de 1 a 100 e `offset` não negativo;
o frontend usa páginas de 50. Nenhuma rota dispara execução.

`POST /from-process` recebe `client_request_id`, `definition`, `task_id`, `run_id`,
`message_id`, `result_sha256` e `review` (`sources`, `review_note`, `pending`,
`confirmed=true` estrito). `GET /{skill_id}/revisions/{version}/process` retorna a
origem ou `null` para uma versão existente sem conversão registrada. Escritas seguem
sessão, Host/Origin/CSRF e replay UUID do restante da biblioteca.
