# Primeiros passos — primeira versão

Este guia descreve os caminhos implementados para abrir o Bees, criar sua abelha
e começar uma conversa. A primeira compilação, o preparo do modelo e a resposta
dependem da máquina e da rede; não há prazo fixo. Os aceites de instalação limpa
e de cada plataforma continuam registrados em [aceite da v1](v1-acceptance.md).

## 1. Abrir a instalação

Tenha a pasta do projeto e o Docker instalado e em execução, com containers Linux.
A distribuição usa Engine 28+ e Compose 2.20+; não exige Python ou Node no host.

| Ambiente | Como iniciar | Participação do usuário/operador |
| --- | --- | --- |
| Windows | Dois cliques em **Iniciar Bees.vbs** na raiz do projeto | Docker Desktop aberto; o launcher prepara os serviços e abre o navegador. |
| macOS | Dois cliques em **Iniciar Bees.command** | O invólucro abre o Terminal e executa o launcher, sem precisar digitar comandos. Docker e Compose precisam estar disponíveis. |
| Linux com desktop | Executar `./scripts/start.sh` na raiz do projeto | Este caminho usa terminal. O operador pode instalar o atalho **Bees** com `./scripts/start.sh --install-shortcut`. |
| Servidor/VPS ou início manual | Operação por Compose e acesso configurado pelo responsável | CLI, TLS/proxy e autorização inicial são tarefas do operador; não fazem parte de um instalador gráfico universal. |

O launcher abre [localhost:8080](http://localhost:8080) por padrão. Na primeira
instalação, o navegador recebe autorização temporária para criar o acesso. Informe
nome e uma senha de 12 a 256 caracteres. Não compartilhe a URL de primeiro acesso.
Se a autorização expirar ou a página for fechada antes de configurar, execute o
launcher novamente. Uma instalação já configurada abre o login; não deve criar
outra identidade.

No Linux sem `xdg-open`, ou ao iniciar sem navegador, a abertura automática pode
não ocorrer. O operador usa o [bootstrap manual](containers.md#operação-por-compose)
no mesmo projeto Compose. Na execução manual com `docker compose up --build --detach --wait`, gerar o código exige `docker compose exec bees bees-auth bootstrap`.
Não exponha a porta HTTP interna nem libere acesso anônimo para contornar o setup.

Veja [distribuição Docker](containers.md) para requisitos, portas, logs e volumes.
Instalação nativa/desenvolvimento usa outro estado e segue o [README](../README.md#desenvolvimento-local).

## 2. Configurar modelo e abelha

Na criação da abelha, escolha uma conexão do catálogo ou um endpoint personalizado.

| Escolha | O que preparar |
| --- | --- |
| Provedor remoto do catálogo | Credencial de API quando exigida e um modelo disponível para a conta. Sugestões não comprovam acesso nem capacidades. |
| OpenAI compatível personalizado | URL base explícita, modelo e credencial quando necessária; confira o protocolo aceito pelo endpoint. |
| Ollama | Backend em execução no servidor Bees e modelo efetivamente local instalado ou preparado com sua confirmação. |

Credenciais vão no campo próprio, nunca no chat ou nas instruções. Histórico e
memórias ficam no Bees; o contexto selecionado é enviado ao endpoint escolhido
para gerar. Trocar de destino é uma decisão sua, não uma recuperação automática
após falha. Custos e capacidades dependem do modelo/provedor; não há garantia de
fatura pelo painel de consumo.

**Buscar modelos** e **Testar conexão · opcional** não são pré-requisitos para
salvar. São consultas de catálogo/disponibilidade, não geração de resposta nem
prova de visão ou ferramentas. É possível **Digitar outro modelo**. Ao salvar,
o Bees verifica a configuração e a disponibilidade da credencial/cofre; não
confirma geração remota. Veja [modelos e credenciais](providers.md#seleção-pela-interface).

Para o modo agente, escolha um modelo que aceite chamadas de ferramentas e revise
**Opções avançadas → Chamadas de ferramenta**. Não presuma suporte apenas pela
família ou pelo nome sugerido. Quando o endpoint exigir Responses, a seleção de
API fica nas opções avançadas; veja [protocolos e seleção de API](providers.md#seleção-de-api-e-gpt-56).
Defina nome, propósito e instruções e confirme a criação da abelha. A opção
**Começar de um modelo** pode preencher propósito e instruções; revise o conteúdo
antes de criar. Depois, **Perfil → Exportar como modelo** permite compartilhar
um `.bee.json` sem credenciais, memórias ou permissões.

### Preparar Ollama

O Bees não instala o serviço Ollama no host. Na distribuição Docker, o operador
pode iniciar o serviço opcional com `docker compose --profile local-model up -d`.
Os modelos desse serviço ficam no volume `ollama-models`. Fora desse perfil, o
responsável precisa instalar/iniciar o backend e escolher o endpoint apropriado.

No painel **Modelo local**, confira modelo, origem, tamanho aproximado, memória
e destino antes de confirmar o download. O preparo usa rede e disco do Ollama;
sugestões não garantem compatibilidade com a máquina. Acompanhe o progresso e,
quando terminar, use **Usar este modelo**. Antes de cada geração local, o Bees
ainda verifica o modelo e suas capacidades. Modelo local não é promessa de
privacidade total: o daemon precisa ser confiável e ferramentas podem usar rede.

Se o acompanhamento ficar incerto, use **Verificar de novo** e consulte o estado
antes de confirmar outro pedido. Cancelar informa o resultado recebido do Bees;
não garante desfazer um efeito remoto. Detalhes em [preparo local](providers.md#preparar-modelo-local-pela-interface).

## 3. Fazer a primeira conversa

Escreva uma pergunta curta e pressione **Enter**; **Shift+Enter** quebra linha.
Esse envio faz geração real e pode consumir recursos ou créditos do provedor.
Confirme que a resposta aparece na conversa. Se falhar, confira a causa mostrada
no chat/atividade e a configuração do modelo; não há troca automática de provedor.
Uma resposta perdida não deve ser tratada como autorização para repetir o pedido.

Não existe seletor obrigatório de modo Pesquisa/Conversa: a abelha decide como
atender ao pedido com as ferramentas habilitadas. Quando houver uma ação que
exija decisão, revise o cartão na conversa antes de permitir. Os passos ficam em
**Usou N ferramentas** e as entregas aparecem para download. A qualidade das
fontes e do conteúdo exige revisão; veja [agente](agent.md) e [pesquisa](research-corpus.md).

O ícone de engrenagem abre **Configurações da abelha**, onde ficam Perfil, Modelo,
Memórias, Skills, Rotinas e os demais recursos. O ícone de pasta abre os arquivos
da conversa; o de monitor abre o computador ao lado do chat quando disponível.

## 4. Voltar depois e preservar seus dados

Fechar o navegador não para os serviços. Abra novamente a instalação e faça login
quando solicitado. Para reiniciar ou atualizar os serviços, siga a
[operação por Compose](containers.md#operação-por-compose), preservando volumes
e interrompendo os escritores antes de manutenção. Uma tarefa em voo pode exigir
revisão de estado; veja [recuperação](recovery.md).

O menu **Meus dados** oferece exportação e restauração com consentimento próprio.
A cópia lógica contém dados privados e não leva a chave do cofre. Ela não substitui
o backup correspondente de dados e segredos. Consulte [portabilidade](portability.md)
e [dados/cofre no Docker](containers.md#dados-cofre-e-recuperação); não apague
volumes ou gere uma chave substituta para tentar reparar uma instalação.

| Situação | Próximo passo |
| --- | --- |
| Esqueceu a senha | O operador emite um código de recuperação no mesmo estado; na entrada, use **Esqueci a senha**. Veja [recuperação de senha](onboarding.md#esqueci-a-senha). Não há reset anônimo. |
| Cofre indisponível | Confira a chave/volume ou perfil original. Redefinir senha não recupera chaves perdidas; preserve dados e backups. |
| API ou modelo não responde | Confira serviço e endereço. Após envio sem confirmação, consulte o estado antes de repetir uma operação. |
| Precisa interromper novas ações | Use **Parar tudo** e consulte as operações já enviadas. Isso não confirma término remoto nem estorno; veja [parada global](safety.md). |

## 5. Próximos recursos

- [Computador da abelha](computer.md): desktop em container, pareamento, tela e controle humano. Hyper-V e provas físicas permanecem separados do caminho da v1.
- [Máquina pessoal](connector.md): conector opcional, pastas/recursos concedidos explicitamente e revogação.
- [Skills](skills.md) e [rotinas](routines-ui.md): procedimentos versionados, agendas e histórico; confirme permissões, destino e fuso.
- [Consumo](budgets.md) e [atenção](attention.md): uso informado/estimado/desconhecido, limites e preferências de aviso.
- [Canais](channels.md) e [acesso HTTPS](onboarding.md#celular-e-servidor-com-https): configuração própria do operador; acesso por celular não publica automaticamente a instalação.

## O que este guia comprova

O texto foi conferido com os caminhos e contratos implementados. Ele não substitui
uma execução de instalação limpa, o invólucro gráfico real em cada sistema,
proxy/certificado real ou geração com modelo/credencial reais. Esses aceites
continuam nas [issues e milestones](github-project.md) e na [matriz de regressão](regression-matrix.md).
