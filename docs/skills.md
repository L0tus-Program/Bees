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

Essa biblioteca cobre BEES-020.1 (#65). Ela ainda não executa procedimentos em tarefas
nem converte automaticamente uma tarefa em skill: esses fluxos pertencem a
BEES-020.2/020.3 (#66/#67). Habilitar uma versão não inicia uma tarefa, altera memória,
concede uma ferramenta ou autoriza acesso ao computador. Declarações de ambiente e
ferramentas descrevem compatibilidade; as políticas e concessões precisam continuar
sendo avaliadas pelo executor de cada ação quando essa integração estiver disponível.

**Revisões** permite consultar todas as versões em páginas de até 50 registros e
exportar uma versão específica. O conteúdo, o hash, a data e a origem de cada revisão
permanecem consultáveis após reiniciar o serviço. A origem identifica uma edição
humana ou uma cópia importada; uma edição de uma cópia ganha origem humana, sem
alterar a origem da revisão importada anterior. A biblioteca não atribui ao usuário
uma validação com modelo ou ambiente real que ainda não aconteceu.

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
