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

- Escreva commits e descrições em português, com problema, comportamento resultante e validação. Relacione o ID de planejamento e a issue (`Refs #numero`); use `Closes #numero` somente quando o aceite completo estiver comprovado.
- Salve um commit a cada checkpoint relevante e faça push normal da branch. Evite force-push e reescrita de histórico compartilhado.
- Abra uma PR com o recorte, testes executados, evidências e limitações. Uma subtarefa pode terminar enquanto a história ampla permanece aberta.
- Atualize checklists, evidências, dependências e estado no Project. Bloqueios precisam de motivo concreto e condição de desbloqueio. Não marque **Done** com base apenas em sucesso simulado.
- Não publique arquivos de execução, cofre, bootstrap, dumps, diretórios privados ou os documentos locais ignorados de planejamento. Issues e documentação versionada precisam ser suficientes para outro contribuidor entender o trabalho.

A licença do projeto ainda precisa de decisão do mantenedor e revisão de dependências antes da primeira release. Essa pendência é acompanhada em BEES-026; não acrescente uma licença escolhida unilateralmente.
