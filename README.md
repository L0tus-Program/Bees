# Bees

Assistentes pessoais com estado próprio, modelos substituíveis e dois ambientes de execução: computador da abelha e máquina pessoal autorizada.

**Estágio atual: fundação técnica.** A API de saúde, o painel de diagnóstico e a camada interna de persistência funcionam. Registros de agentes, conversas, tarefas, decisões e resultados sobrevivem ao reinício. Onboarding, modelos, execução de tarefas, políticas de autorização, VM e conector pessoal serão implementados nas próximas etapas. Ainda não há um assistente utilizável nem autenticação para publicação na internet.

## Requisitos

- Python 3.14; desenvolvimento validado com 3.14.4.
- Node.js 22.17 ou posterior compatível; npm 11.6.2 usado na validação.
- [uv](https://docs.astral.sh/uv/getting-started/installation/), validado com 0.7.21.
- Git e terminal. Docker, hipervisor e chave de modelo não são necessários para esta etapa.

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

Terminal 2, interface com atualização automática:

```sh
npm --prefix apps/web run dev
```

Abrir [http://127.0.0.1:5173](http://127.0.0.1:5173). Vite encaminha `/api` ao serviço em `127.0.0.1:8000`. O painel verifica uma conexão real e permite tentar novamente quando o serviço estiver indisponível.

## Interface compilada

```sh
npm --prefix apps/web run build
uv run --locked bees-api
```

Abrir [http://127.0.0.1:8000](http://127.0.0.1:8000). O mesmo processo serve API e arquivos estáticos de `apps/web/dist`. Sem build, a API continua disponível; a raiz informa a ausência da interface compilada. `vite preview` não é o servidor de produção.

## Configuração

[.env.example](.env.example) documenta as variáveis sem credenciais. O serviço não carrega `.env` automaticamente; defina variáveis no shell ou supervisor.

| Variável | Padrão | Uso |
| --- | --- | --- |
| `BEES_HOST` | `127.0.0.1` | Apenas loopback nesta fundação, enquanto não há autenticação. |
| `BEES_PORT` | `8000` | Porta HTTP; o proxy de desenvolvimento espera 8000. |
| `BEES_WEB_DIST` | `apps/web/dist` no checkout | Caminho opcional da interface compilada. |
| `BEES_DATA_DIR` | `data` no checkout | Pasta privada da base SQLite, em filesystem local. |
| `BEES_CACHE_TTL_SECONDS` | `86400` | TTL de cache derivado; estado canônico não expira. |
| `BEES_CACHE_PRUNE_LIMIT` | `1000` | Lote de limpeza de cache expirado no startup, entre 1 e 1000. |

SQLite é fornecido por APSW fixado nas dependências. O serviço verifica a versão efetiva na inicialização e recusa versões inseguras; o banco de domínio é criado/migrado antes de aceitar tráfego. Não troca DLL do Python global nem recorre ao `sqlite3` do sistema. [Persistência e migrações](docs/persistence.md) descrevem os contratos e a manutenção com serviço parado. Não há HTTP de escrita de registros nesta etapa.

## Servidor Linux / VPS

Instale os requisitos, copie o checkout, execute a instalação com lockfiles e compile a interface. Inicie `uv run --locked bees-api` no servidor; o serviço continua em loopback. Para inspecionar a fundação remotamente, use um túnel SSH autenticado na sua máquina:

```sh
ssh -N -L 8000:127.0.0.1:8000 usuario@seu-servidor
```

Abra `http://127.0.0.1:8000` localmente. Mantenha a porta HTTP fechada à internet. A instalação pública com identidade, sessão e HTTPS será entregue junto da autenticação; esta etapa não é um deployment de produção. O workflow verifica Windows e Linux quando executado; resultados locais desta rodada foram obtidos no Windows.

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

- [ADR 0001](docs/adr/0001-foundation.md): stack, isolamento, autenticação planejada, protocolos e limites.
- [Mapa da arquitetura](docs/architecture.md): componentes e contratos do desenho alvo.
- [Persistência](docs/persistence.md): transações, migrações, backup, retenção e limites atuais.

Próxima entrega implementável: adaptadores de modelo remoto/local (BEES-005), antes de concluir o onboarding (BEES-004) que depende deles. O computador próprio e o conector pessoal são requisitos do MVP completo.

O projeto pretende ser open source. A licença ainda será escolhida antes da publicação; não presumir direitos de redistribuição sem um arquivo de licença.
