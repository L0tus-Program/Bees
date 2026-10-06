# Bees

Assistentes pessoais com estado próprio, modelos substituíveis e dois ambientes de execução: computador da abelha e máquina pessoal autorizada.

**Estágio atual: assistente de texto com memória, tarefas em segundo plano e regras de autonomia.** Escolha um provedor e modelo na interface, crie/edite uma abelha, registre preferências e converse com histórico persistente e contexto selecionado. Listas prontas e digitação manual estão disponíveis; busca e teste de catálogo são opcionais. Credenciais podem ficar no cofre local ou ser referenciadas por ambiente. Tarefas textuais possuem execução independente, controles e resultados persistentes. O painel Autonomia permite configurar regras antes das gerações de chat/tarefas; veja [políticas](docs/policies.md). Aprovações pontuais, ferramentas, VM e conector pessoal ainda serão implementados. Testes usam servidores controlados; geração com modelos reais continua pendente.

## Abrir com Docker

No Windows, mantenha Docker Desktop aberto com containers Linux e dê dois cliques em **Iniciar Bees.vbs**. O launcher sobe API/interface e executor de tarefas, prepara o cofre e abre o navegador com autorização para criar seu acesso. Configure nome, senha, modelo e primeira abelha na interface, sem gerar tokens ou chaves no terminal. Não precisa instalar Python/Node no host.

Operadores também podem subir com `docker compose up --build --detach --wait`. Interface em [localhost:8080](http://localhost:8080); dados e chave ficam em volumes separados e sobrevivem a reinícios. [Distribuição e operação](docs/containers.md) explica primeiro acesso, volumes, backup, Ollama opcional e limites. A instalação nativa abaixo usa outro estado; não há importação automática.

## Requisitos de desenvolvimento

- Python 3.14; desenvolvimento validado com 3.14.4.
- Node.js 22.17 ou posterior compatível; npm 11.6.2 usado na validação.
- [uv](https://docs.astral.sh/uv/getting-started/installation/), validado com 0.7.21.
- Git e terminal. Para testes da fundação, Docker, hipervisor e chave não são necessários. Para conversar, configure uma API remota acessível ou Ollama com um modelo local instalado.

Os comandos abaixo partem da raiz do checkout. Python usa a `.venv` da raiz; frontend usa `apps/web/node_modules`. `uv.lock` e `apps/web/package-lock.json` controlam as dependências. Não instalar pacotes Python globais para executar o Bees.

## Desenvolvimento local

Instalar:

```sh
uv sync --locked
npm --prefix apps/web ci
```

Terminal 1, serviço:

```sh
uv run --locked bees-api
```

Terminal 2, executor de tarefas (com a API já iniciada):

```sh
uv run --locked bees-worker
```

Terminal 3, interface com atualização automática:

```sh
npm --prefix apps/web run dev
```

Abrir [http://127.0.0.1:5173](http://127.0.0.1:5173). Vite encaminha `/api` ao serviço em `127.0.0.1:8000`. A interface usa a API local e permite tentar novamente quando o serviço estiver indisponível.

## Interface compilada

```sh
npm --prefix apps/web run build
uv run --locked bees-api
```

Abrir [http://127.0.0.1:8000](http://127.0.0.1:8000). Inicie também `uv run --locked bees-worker` para consumir tarefas. O processo da API serve API e arquivos estáticos de `apps/web/dist`. Sem build, a API continua disponível; a raiz informa a ausência da interface compilada. `vite preview` não é o servidor de produção.

## Primeiro acesso no desenvolvimento

Na distribuição Docker Windows, use o launcher descrito acima. O fluxo abaixo é opcional para desenvolvimento/operação.

Com a API iniciada, gere o código de configuração em outro terminal:

```sh
uv run --locked bees-auth bootstrap
```

Cole o código na interface, informe seu nome e crie uma senha de 12 a 256 caracteres. O código dura 15 minutos e só funciona uma vez; gerar outro invalida o anterior. Guarde a senha. Depois selecione o provedor, escolha um modelo na lista pronta ou digite outro e informe sua chave quando necessária. Crie a abelha; **Buscar modelos** e **Testar conexão** são opcionais. OpenAI, OpenRouter, Gemini e Ollama têm endereços preconfigurados; conexões personalizadas permitem outros servidores. Sugestões não comprovam acesso da conta nem instalação; Ollama precisa de um modelo local instalado. Depois, use Perfil, Modelo e Memórias para ajustar a abelha. [Perfis e memória](docs/memory.md) descreve escopos, seleção de contexto e exclusão.

[Primeiro acesso e segurança](docs/onboarding.md) explica sessões, cofre, acesso remoto e diagnóstico. Computadores das abelhas e máquina pessoal aparecem como recursos em preparação.

## Configuração

[.env.example](.env.example) documenta as variáveis sem credenciais. O serviço não carrega `.env` automaticamente; defina variáveis no shell ou supervisor.

| Variável | Padrão | Uso |
| --- | --- | --- |
| `BEES_HOST` | `127.0.0.1` | Bind local; `0.0.0.0` somente em modo container explícito, com publicação loopback no host. |
| `BEES_DEPLOYMENT_MODE` | `local` | `container` exige chave em arquivo privado separado e não confia em headers de proxy. |
| `BEES_BROWSER_PORT` | porta do serviço | Porta externa do navegador; Compose configura 8080 e mantém 8000 interna. |
| `BEES_VAULT_KEY_FILE` | ausente | Arquivo privado gerenciado do container, fora do volume dos dados. |
| `BEES_PORT` | `8000` | Porta HTTP; o proxy de desenvolvimento espera 8000. |
| `BEES_WEB_DIST` | `apps/web/dist` no checkout | Caminho opcional da interface compilada. |
| `BEES_DATA_DIR` | `data` no checkout | Pasta privada da base SQLite, em filesystem local. |
| `BEES_CACHE_TTL_SECONDS` | `86400` | TTL de cache derivado; estado canônico não expira. |
| `BEES_CACHE_PRUNE_LIMIT` | `1000` | Lote de limpeza de cache expirado no startup, entre 1 e 1000. |
| `BEES_PUBLIC_URL` | ausente | Origem HTTPS exata para acesso remoto por proxy local. |
| `BEES_VAULT_KEY` | ausente | Chave Fernet externa para cofre no Linux; Windows usa DPAPI do usuário atual. |

SQLite é fornecido por APSW fixado nas dependências. O serviço verifica a versão efetiva na inicialização e recusa versões inseguras; o banco de domínio é criado/migrado antes de aceitar tráfego. Não troca DLL do Python global nem recorre ao `sqlite3` do sistema. [Persistência e migrações](docs/persistence.md) descrevem os contratos e a manutenção com serviço parado. Identidade, criação de abelhas e conversa possuem endpoints autenticados; tarefas textuais usam um worker separado. Ferramentas continuam pendentes; veja [tarefas](docs/tasks.md).

## Servidor Linux / VPS

Instale os requisitos, copie o checkout, execute a instalação com lockfiles e compile a interface. Inicie `uv run --locked bees-api` no servidor; o serviço continua em loopback. Para inspecionar a fundação remotamente, use um túnel SSH autenticado na sua máquina:

```sh
ssh -N -L 8000:127.0.0.1:8000 usuario@seu-servidor
```

Abra `http://127.0.0.1:8000` localmente. Mantenha a porta HTTP fechada à internet. Para acesso HTTPS direto, siga a configuração de proxy e origem em [onboarding](docs/onboarding.md). Configurar a aplicação não provisiona domínio, certificado ou proxy. O workflow verifica Windows e Linux quando executado; resultados locais desta rodada foram obtidos no Windows.

A futura VM da abelha exige KVM/libvirt no Linux ou Hyper-V suportado no Windows. Uma VPS sem virtualização pode hospedar o serviço e conectar uma VM em outro host do usuário. A fundação não provisiona nenhuma dessas máquinas.

## Verificação

Windows PowerShell:

```powershell
./scripts/check.ps1
```

Linux:

```sh
sh scripts/check.sh
```

Os scripts instalam dependências pelos lockfiles, executam análise/formatação Python, testes de domínio, banco e API, análise/testes web e build TypeScript/Vite. A [CI](.github/workflows/ci.yml) usa os mesmos comandos. Os testes não chamam modelos nem operam contas/apps reais.

## Arquitetura e continuidade

- [ADR 0001](docs/adr/0001-foundation.md): stack, isolamento, fronteiras de autenticação, protocolos e limites.
- [Mapa da arquitetura](docs/architecture.md): componentes e contratos do desenho alvo.
- [Persistência](docs/persistence.md): transações, migrações, backup, retenção e limites atuais.
- [Tarefas](docs/tasks.md): delegação, controles, limites, journal e executor independente.
- [Perfis e memória](docs/memory.md): edição, proveniência, isolamento, contexto e exclusão.
- [Docker e instalação](docs/containers.md): launcher, distribuição, volumes e recuperação.
- [Primeiro acesso e segurança](docs/onboarding.md): identidade, sessões, cofre e proxy HTTPS.
- [Modelos e CLI](docs/providers.md): configuração, credenciais, conversa, troca de backend e diagnóstico.

Onboarding (BEES-004) integrado aos drivers de BEES-005; comprovação operacional com modelos reais remoto/local ainda pendente. Perfis e memória de BEES-006 integram esses contratos. Compose e launcher Windows entregues; primeiro acesso dessa distribuição é autorizado automaticamente no navegador. Setup gráfico nas demais distribuições e integração dos ambientes futuros continuam em BEES-028. Computador próprio e conector pessoal são requisitos do MVP completo.

O projeto pretende ser open source. A licença ainda será escolhida antes da publicação; não presumir direitos de redistribuição sem um arquivo de licença.
