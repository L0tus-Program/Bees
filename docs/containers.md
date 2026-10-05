# Docker Compose e primeiro acesso

## Uso no Windows

Instale e abra Docker Desktop com containers Linux, Engine 28 ou posterior e Compose 2.20 ou posterior. A validação local usou Engine 28.4.0/Compose 2.39.4 em x64. O checkout precisa estar em uma pasta local. Não é necessário instalar Python, Node ou configurar variáveis para usar esta distribuição.

Dê dois cliques em **Iniciar Bees.vbs**, na raiz do projeto. O launcher prepara a imagem, aguarda o serviço e abre [http://localhost:8080](http://localhost:8080). Na primeira instalação, o navegador já possui autorização temporária: informe nome e senha, configure/teste seu modelo e crie a primeira abelha. Credenciais, perfis e memórias são configurados na interface.

A preparação pode levar alguns minutos na primeira vez. Se falhar, o launcher mostra uma mensagem; detalhes de build/inicialização ficam em `logs/startup.log`. O Docker deve permanecer disponível durante o uso. Fechar o navegador não para a aplicação.

O launcher gera internamente um código de uso único, válido por 15 minutos, e abre um fragmento privado da URL. O frontend remove esse fragmento antes de renderizar e mantém o código apenas em memória. Nenhuma rota anônima fornece esse código. Se atualizar/fechar a página antes de criar a identidade, execute o launcher novamente; isso invalida a autorização anterior. Instalações já configuradas abrem o login.

O arquivo VBS usa Windows Script Host e PowerShell do Windows. Se a política da máquina desabilita esses componentes, a alternativa operacional é `scripts/start.ps1`; não há instalador assinado ou launcher gráfico Linux/macOS neste checkpoint. O setup gráfico completo para as distribuições suportadas continua em BEES-028.

## Operação por Compose

Quem administra a instalação também pode usar um comando único:

```sh
docker compose up --build --detach --wait
```

Esse comando entrega API e interface juntas em `localhost:8080`; não exige Vite separado. O primeiro acesso autorizado é aberto pelo launcher. O bootstrap CLI permanece uma opção do operador, sem se tornar requisito do usuário final.

```sh
docker compose ps
docker compose logs --tail 100 bees
docker compose restart bees
docker compose down
```

Para atualizar o checkout, pare a aplicação, preserve/guarde os volumes e execute novamente `docker compose up --build --detach --wait`. Uma única instância do serviço escreve nesses volumes. O healthcheck confirma banco pronto e frontend presente. A imagem usa lockfiles, imagens-base por digest e usuário `10001:10001`; não inclui dados, arquivos `.env`, planejamento privado ou dependências locais. O filesystem da imagem é somente leitura, com volumes graváveis e `/tmp` temporário.

O requisito Engine 28+ acompanha a correção de alcance de portas publicadas em localhost, descrita na [documentação de portas](https://docs.docker.com/engine/network/port-publishing/). O Compose publica somente `127.0.0.1:8080`, encaminhado à porta interna 8000. `BEES_HTTP_PORT` altera a porta externa e a origem permitida juntas; o launcher também aceita `-Port`. A instalação nativa em 8000 e esta instalação usam estados diferentes. O Compose não importa automaticamente SQLite ou credenciais DPAPI do Windows.

## Dados, cofre e recuperação

| Volume | Conteúdo |
| --- | --- |
| `bees-data` | SQLite, backups de migração, memórias e credenciais cifradas. |
| `bees-secrets` | Chave Fernet privada provisionada automaticamente. |
| `ollama-models` | Modelos de Ollama, somente quando o perfil opcional for usado. |

Os nomes físicos incluem o nome do projeto Compose. `docker compose down` preserva volumes. A opção `--volumes` apaga os volumes da instalação; use-a apenas para descarte deliberado. Não use volumes compartilhados entre projetos ou uma base em filesystem de rede.

O serviço cria a chave privada fora do volume de dados, com permissões restritas, e registra sua identidade para detectar perda/troca. Não grava a chave na imagem, ambiente ou logs. Arquivo inválido, permissões incorretas, links ou perda de chave com estado existente interrompem a inicialização. Recuperação exige a chave correspondente; o serviço não regenera para ocultar o problema. O processo autorizado pode acessar ambos os volumes: separação não protege contra comprometimento dessa conta.

Backup/restauração devem preservar **os dois volumes correspondentes**. Pare todos os serviços antes de copiar para obter um conjunto consistente, incluindo SQLite, WAL/SHM se presentes, cofre e marcador. Guarde a chave em arquivo/local privado separado do backup comum dos dados. Uma cópia apenas do SQLite ou de `bees-data` não recupera credenciais do cofre.

Exemplo operacional com uma pasta privada `backups` já criada no host, usando Python da própria imagem:

```sh
docker compose stop
docker compose run --rm --no-deps -v "./backups:/backup" --entrypoint python bees -c "import tarfile; t=tarfile.open('/backup/bees-data.tar.gz','w:gz'); t.add('/var/lib/bees',arcname='.'); t.close(); t=tarfile.open('/backup/bees-secrets.tar.gz','w:gz'); t.add('/run/bees-secrets',arcname='.'); t.close()"
docker compose start
```

Para restaurar em uma instalação nova, mantenha os serviços parados e os volumes de destino vazios. Monte a pasta privada no mesmo caminho e use `tarfile.open(...).extractall('/var/lib/bees', filter='data')` para o arquivo de dados e `extractall('/run/bees-secrets', filter='data')` para o arquivo de chave. Use somente um par de backups da mesma instalação; preserve permissões e proprietário `10001`. Em instalação existente, primeiro preserve o estado atual e prepare destinos vazios; não sobreponha bancos/cofres diferentes. A [persistência](persistence.md) descreve migrações e backup SQLite.

## Modelos remoto e local

Selecione o provedor na interface, informe a chave quando necessária e use **Buscar modelos** para escolher na lista. OpenAI, OpenRouter e Gemini têm endereços preconfigurados; outro servidor compatível pode ser conectado pelas opções personalizadas. Uma API remota continua usando HTTPS. O Compose não contrata fornecedor, envia dados ou baixa modelos por conta própria.

Ollama é opcional:

```sh
docker compose --profile local-model up --detach --wait
```

Esse serviço compartilha a rede interna da aplicação, escuta apenas `127.0.0.1:11434` nesse namespace e não publica a porta do modelo. Não baixa um modelo automaticamente. O operador pode instalar um modelo escolhido com `docker compose exec ollama ollama pull IDENTIFICADOR`; na interface, escolha Ollama, `http://127.0.0.1:11434` e esse identificador. A imagem opcional está fixada por digest; modelos e uso real ainda exigem validação própria.

Loopback dentro do container não é o host Windows/Linux. Um Ollama já instalado no host não é acessível por esse endereço. O adaptador atual recusa Ollama fora de loopback e HTTP remoto: não trocar silenciosamente para `host.docker.internal`, abrir o modelo à rede ou desabilitar TLS para contornar isso. Use o serviço opcional ou a distribuição nativa; integração gráfica com um backend já existente no host continua pendente.

## Limites e verificação

Esta distribuição local não inclui proxy/certificado HTTPS. Não publique a porta em `0.0.0.0` no host. Acesso remoto autenticado via túnel SSH mantém a URL local; publicação por proxy segue o modo nativo documentado em [primeiro acesso](onboarding.md). O modo container não confia em headers de proxy por padrão, e não basta definir uma URL pública para obter HTTPS.

Compose empacota o plano de controle. Não provisiona VM da abelha, conector pessoal, ferramentas ou scheduler. Esses componentes serão acrescentados quando suas histórias estiverem implementadas.

Validação reproduzível com modelo de protocolo controlado, sem chaves reais:

```sh
docker compose build
.venv/Scripts/python.exe scripts/check-container.py
docker build --target verification -t bees-verification:local .
docker run --rm --network none bees-verification:local
```

No Linux, use `.venv/bin/python` para o script. O aceite cria projeto/volumes próprios, verifica primeiro acesso, cofre, conversa, memória, Host/Origin/CSRF, reinício e recriação, e remove somente esse projeto de teste. `--keep` mantém o projeto para inspeção. O alvo `verification` roda os testes POSIX de chave/cofre e contratos da configuração/CLI; não faz parte da imagem usada pelo usuário. Os testes não demonstram geração por um modelo real nem execução autônoma.

Contratos de build, volumes e inicialização seguem as documentações oficiais de [uv em Docker](https://docs.astral.sh/uv/guides/integration/docker/) e [serviços do Compose](https://docs.docker.com/reference/compose-file/services/).

## Evidência deste checkpoint

Build real da imagem e Compose aprovados no Docker Desktop Linux/x64. O aceite controlado confirmou criação de identidade, chave cifrada, conversa, memória, Host/Origin/CSRF, reinício e recriação com estado preservado. Backup dos dois volumes com serviços parados foi executado com dados descartáveis. Os testes Linux selecionados aprovaram 90 casos, com quatro skips Windows; a suíte Windows aprovou 416 casos Python e 65 web, com 24 skips de plataforma. Ruff, ESLint, typecheck/build passaram. CI Docker foi configurada, mas não executada remotamente.

No navegador, o código automático desapareceu da URL e não apareceu em campo; identidade → configuração/teste de modelo → abelha → resposta controlada funcionaram pela interface. Reload após reinício e logout/login preservaram conversa. O launcher PowerShell foi executado contra o Docker real sem abrir navegador externo; a abertura autorizada equivalente foi conferida no navegador de teste. O invólucro VBS não foi acionado interativamente nesta validação. Geração com modelos reais, perfil Ollama com modelo instalado, launcher gráfico Linux/macOS e acesso HTTPS por proxy no modo container permanecem sem aceite operacional.
