# Contratos de ferramentas e extensões locais

BEES-010 estabelece contratos próprios do Bees para ferramentas, sem formato fechado de um provedor. O checkpoint inicial oferece uma ferramenta pura de transformação de texto e gestão de extensões declarativas. Não instala código Python de terceiros no processo que guarda banco e credenciais. Navegador, arquivos, shell, apps, MCP e executores remotos dependem de seus adaptadores e ambientes próprios.

## Gestão pela interface

Na aba **Ferramentas** da abelha, instale uma extensão do catálogo local. Uma instalação começa desabilitada. Habilitação na instalação e concessão de cada ferramenta à abelha são controles separados; conceder uso não remove regras Perguntar ou Bloquear. Desabilitar a instalação afeta todas as abelhas; revogar a concessão afeta apenas a abelha selecionada. Resultados de mudanças aparecem após confirmação do serviço.

Manifestos JSON são uma opção avançada. Eles descrevem ferramentas conhecidas pelo registro confiável; não carregam módulos, executáveis, URLs ou credenciais. Instalações têm manifesto imutável e revisão. Uma versão diferente recebe instalação própria e não herda acesso da anterior. Os contratos publicados incluem versão, entradas/saídas, capacidade e executor suportado.

O catálogo atual referencia `builtin:text.normalize@1`. A operação transforma somente o texto fornecido em memória; não lê arquivos nem usa rede. Gerenciar o contrato não ativa chamadas automáticas pelo modelo: chat e tarefas ainda seguem seu fluxo textual. A implementação de loop de ferramentas e a integração com ambientes reais serão comprovadas nos checkpoints desses executores.

## Autoridade e execução

O serviço separa os argumentos da ferramenta dos dados de autoridade: agente, ambiente, identidade, destino e vínculo de execução. Argumentos externos não escolhem permissões nem modificam políticas. O registro confiável define executor, capacidade e modelos de validação; um manifesto declarativo não pode ampliar esses contratos.

Antes de preparar uma ação, o serviço verifica instalação, ferramenta e concessão. Antes do despacho, relê suas revisões e a política atual. Uma intenção alterada ou um contrato desabilitado não usa a autorização anterior. Perguntar mantém a chamada sem execução; não existe aprovação implícita por texto ou concessão. Aprovações BEES-009 continuam limitadas às tarefas de geração textual até a integração com executores reais.

Cada chamada conserva correlação e journal de ação. Replay de resultado confirmado devolve o estado conhecido; uma chamada iniciada de resultado incerto não é repetida. Falha de saída ou interrupção após início conserva a incerteza. Uma interrupção de processo deixa `dispatch_started`; o supervisor só marca `outcome_unknown` após interromper o dono antigo e registrar uma referência opaca de evidência. Consulta ou replay não marca como encerrada uma ação ainda em voo.

Eventos, erros e projeções de gestão não exportam texto processado, credenciais ou exceções arbitrárias do executor. Entradas não são persistidas neste builtin: o dispatcher confere o hash ao receber novamente os argumentos. O resultado textual fica privado no registro de ação, sem endpoint público de chamada/resultado. Não inclua credenciais nos argumentos.

## API de gestão

Todas as rotas exigem sessão humana; mutações também exigem Origin e CSRF. Não há endpoint de execução de ferramentas exposto ao modelo.

- GET `/api/v1/plugins/catalog`: manifestos do catálogo local.
- GET `/api/v1/plugins?offset=0&limit=100`: instalações paginadas.
- POST `/api/v1/plugins`: manifesto e `client_request_id` UUID. Mesmo envio reconcilia a instalação; UUID com conteúdo diferente gera conflito.
- PATCH `/api/v1/plugins/{id}`: `expected_revision` e `enabled`.
- GET `/api/v1/agents/{id}/tools?offset=0&limit=100`: contratos e concessões da abelha.
- PUT `/api/v1/agents/{id}/tools/{plugin_id}/{tool_name}`: `expected_revision` (zero para criar), `enabled`.

Habilitação e concessão usam CAS; após conflito ou resposta perdida, consulte novamente o estado antes de mudar outra coisa. Identidade/ator não são campos aceitos nessas escritas.

## Persistência e operação

Schema 4 adiciona instalações e concessões canônicas. A migração exige API e worker parados e conserva os dados anteriores; inicialização cria backup antes de migrar. O launcher Windows interrompe a versão anterior ao detectar imagem nova. Consulte [persistência](persistence.md) e [containers](containers.md); não substitua banco ou volumes para atualizar.

Referência OD-02: registro explícito e fronteira validação → política → despacho. A implementação é própria; nenhum código do Open Dots foi incorporado. Esta fundação não prova isolamento de máquinas, execução de código arbitrário ou uso de ferramentas por um LLM real. O builtin interno não fornece transporte remoto, lease/fencing de executor ou agenda. Ao integrar os executores, os controles de tarefas deverão abranger também `Action.outcome_unknown`, além de `ModelCall`, e exigir reconhecimento/reconciliação antes de outra tentativa com efeitos externos.

## Validação do checkpoint

Em 06/10/2026: 707 testes Python no Windows, 242 de frontend e 324 no alvo Linux aprovados; 24/4 skips de plataforma. Ruff, ESLint, typecheck e build passaram. Casos novos incluem entradas/saídas inválidas, autoridade injetada, grants/CAS, mudança de política e concessão entre preparo e despacho, concorrência, replay, queda real de subprocesso e atualização 3→4 com backup. Servidores, contas e textos são próprios de teste; nenhum modelo real foi usado.

Smoke Docker comprovou instalação desabilitada, CSRF, habilitação, concessão exclusiva à abelha, desabilitação e persistência após reinício/recriação. Na interface descartável: habilitação sem concessão, concessão confirmada e preservada após reload, revogação e importação de manifesto mantendo instalação desabilitada/sem acesso. Textos conferidos em pt-BR/en/es; viewport390 sem overflow (client/scroll375px), inclusive contrato expandido. Evidências locais `data/validation/bees-tools-desktop.png` e `bees-tools-mobile.png`, ignoradas pelo Git. A instalação padrão foi migrada ao schema4 pelo launcher com serviços quiescidos, identidade/cofre preservados e API/worker saudáveis; nenhum dado de teste foi inserido nela.
