# Contribuir com o Bees

O planejamento está nas [issues](https://github.com/L0tus-Program/Bees/issues) e no [Project](https://github.com/users/L0tus-Program/projects/3). O [mapa de histórias e marcos](docs/github-project.md) explica escopo, dependências e estados. O Project mantém a visibilidade escolhida pelo proprietário; as issues e este mapa estão disponíveis no repositório.

## Antes de implementar

1. Leia a história e a subtarefa escolhida, seus critérios de aceite e as dependências. Consulte os contratos e as ADRs vinculados antes de alterar fronteiras.
2. Confirme no código o que já está entregue. Componentes internos, mocks e testes de protocolo não demonstram uma VM pronta ou o aceite com modelos reais.
3. Registre na issue o recorte que será implementado e atualize o Project para **In Progress**. Novos trabalhos usam uma branch `codex/descricao-curta`; não execute tarefas externas ou mudanças do sistema somente porque aparecem no plano.
4. Preserve dados, credenciais e alterações de outras pessoas. Se precisar mudar o escopo, documente a decisão na issue antes de substituir seus critérios.

## Desenvolvimento e verificação

Instale as dependências pelos lockfiles e use a `.venv` e os `node_modules` do checkout. Os comandos e requisitos de instalação estão no [README](README.md). As verificações existentes são `scripts/check.ps1` no Windows e `scripts/check.sh` no Linux; elas incluem testes Python, análise estática e testes/build da interface.

Execute as verificações apropriadas antes de cada commit. Em alteração somente de documentação, confira links, IDs, estrutura e consistência com o código, sem criar testes artificiais. Mudanças de autorização, retomada ou efeito externo exigem negativos, concorrência, falhas controladas e evidência do executor real quando esse for o aceite. Use apenas contas, arquivos, processos e instalações de teste. Nunca coloque credenciais no contexto do modelo, logs ou exportações comuns.

Migrações exigem serviço quiescido e backup. Confira [persistência](docs/persistence.md), [políticas](docs/policies.md), [aprovações](docs/approvals.md) e [provisionamento](docs/provisioning.md) conforme a área. Habilitar hipervisores, elevar privilégios, alterar redes, enviar mensagens reais ou publicar releases exige autorização própria; um planejamento de issue não a substitui.

## Checkpoints e revisão

- Escreva commits e descrições em português, com problema, comportamento resultante e validação. Relacione o ID de planejamento e a issue (`Refs #numero`); use `Closes #numero` somente depois da revisão e do aceite completo.
- Salve um commit a cada checkpoint relevante e faça push normal da branch. Evite force-push e reescrita de histórico compartilhado.
- Abra uma PR para a branch de integração `dev` com o recorte, testes executados, evidências e limitações. A PR é integrada na `dev` com merge commit; a `main` recebe a `dev` somente por decisão do mantenedor. Merge não conclui o aceite nem fecha a issue automaticamente. Uma subtarefa pode terminar enquanto a história ampla permanece aberta.
- Ao terminar a implementação de uma issue, mova-a para **Review** e mantenha-a aberta para revisão. Se somente parte do escopo foi entregue, use **Entrega Parcial**, registrando o recorte comprovado e as pendências. **In Progress** identifica implementação ativa. **Done** e fechamento ficam para depois da revisão e do aceite completo; não decorrem automaticamente de commit, merge ou CI verde.
- Atualize checklists, evidências, dependências e estado no Project. Bloqueios precisam de motivo concreto e condição de desbloqueio. Testes simulados não substituem aceites com ambientes ou modelos reais.
- Não publique arquivos de execução, cofre, bootstrap, dumps, diretórios privados ou os documentos locais ignorados de planejamento. Issues e documentação versionada precisam ser suficientes para outro contribuidor entender o trabalho.

A licença do projeto ainda precisa de decisão do mantenedor e revisão de dependências antes da primeira release. Essa pendência é acompanhada em BEES-026; não acrescente uma licença escolhida unilateralmente.
