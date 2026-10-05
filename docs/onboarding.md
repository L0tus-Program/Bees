# Primeiro acesso e segurança

Na distribuição Docker Windows, abra **Iniciar Bees.vbs** para autorizar o primeiro acesso e configurar tudo no navegador. [Containers](containers.md) descreve launcher, cofre gerenciado e volumes. Os comandos de bootstrap deste guia continuam como alternativa de desenvolvimento/operação.

O Bees possui uma identidade individual com nome e senha. Várias abelhas e sessões usam essa identidade; equipes e tenants ainda não existem. A interface oferece conversa de texto persistida. Perfis e memórias podem ser editados pela interface, conforme [guia de memória](memory.md). Ambientes de computador, execução de ferramentas e tarefas em segundo plano continuam em preparação.

## Instalação local

Instale e compile conforme o [README](../README.md). Inicie `uv run --locked bees-api`; em outro terminal execute `uv run --locked bees-auth bootstrap`. Se usa uma pasta personalizada, passe o mesmo `--data-dir` aos dois comandos ou configure `BEES_DATA_DIR`.

Abra `http://127.0.0.1:8000`, cole o código e escolha nome e senha. O código dura 15 minutos, é de uso único e só aparece no terminal do operador; gerar outro invalida o anterior. Após criar a identidade, o comando recusa novos bootstraps. A senha tem de 12 a 256 caracteres e não é normalizada. Não há recuperação/troca de senha por interface nesta etapa; preserve a senha e os backups privados.

Selecione API compatível ou Ollama, configure endpoint/modelo, informe a chave quando necessária e teste a conexão. Depois escolha nome, propósito e instruções da abelha. O diagnóstico verifica acesso e modelo anunciado; enviar uma mensagem verifica a geração. Ollama exige um modelo instalado previamente pelo operador.

Testes de conexão expiram em cinco minutos, pertencem à sessão e à configuração exata. Alterações exigem novo teste. Reiniciar o serviço invalida testes não usados; abelhas e criações já confirmadas permanecem no banco. A interface pode recuperar o estado após um resultado incerto, sem repetir chamadas automaticamente.

## Sessões e acesso à API

Senhas usam Argon2id. A sessão utiliza token opaco em cookie `HttpOnly`, `SameSite=Strict`, válido por 12 horas absolutas. Login pode abrir outra sessão na mesma identidade; logout revoga a sessão corrente no banco, inclusive após reinício. Não encerra sessões de outros dispositivos nem cancela uma chamada de modelo já autorizada.

Pedidos de escrita exigem origem permitida, JSON e o nonce CSRF da sessão. A API valida Host, Origin e contexto de navegação também no modo local. Tokens de sessão, códigos de bootstrap e valores de chave não são devolvidos nos erros. Há limite persistente de cinco tentativas de login e dez de configuração por cinco minutos; o limite é global à instalação. Nova tentativa após a janela permite recuperação sem reiniciar a API.

`GET /api/v1/health` e `GET /api/v1/state/status` expõem somente metadados de disponibilidade. Status de autenticação informa se a instalação foi configurada; nome e nonce só aparecem para uma sessão válida. Onboarding, criação e conversa exigem autenticação. A CLI tem acesso direto ao estado pelo usuário do sistema e não é uma API para usuários não confiáveis.

## Cofre de modelos

No Windows, o cofre usa DPAPI CurrentUser da conta que executa o serviço, sem chave Fernet adicional. No Linux, provisione uma chave Fernet externa por `BEES_VAULT_KEY` no shell ou supervisor. Gere uma vez com `cryptography.fernet.Fernet.generate_key()` e guarde em um gestor de segredos ou arquivo privado fora do diretório de dados; não imprima em logs compartilhados. API e CLI precisam da mesma chave para ler referências existentes.

A aplicação não gera uma chave simétrica ao lado dos blobs. Sem chave válida no Linux, a API inicia e exibe cofre indisponível; modelos locais sem chave e referências `env:VAR` continuam possíveis. A interface bloqueia salvar uma chave diretamente quando o cofre estiver indisponível.

Cada credencial recebe referência `vault:UUID`. Arquivos cifrados ficam em `BEES_DATA_DIR/vault`; a base contém apenas a referência. Referências `env:VAR` continuam suportadas, sem carregamento automático de `.env`. A chave é enviada ao endpoint escolhido pelo operador, no cabeçalho de autenticação; nunca deve ser inserida no chat, em instruções ou em variáveis `VITE_*`.

Diretórios POSIX usam permissões restritas. No Windows, escolha uma pasta com ACL privada. Criptografia não protege contra comprometimento da conta/processo do serviço. Preserve cofre e acesso à chave/perfil original junto dos backups; [persistência](persistence.md) descreve os limites de restauração.

## Celular e servidor com HTTPS

Para acessar a mesma identidade por outro dispositivo, use a interface compilada atrás de um proxy HTTPS no mesmo host. Configure `BEES_PUBLIC_URL` com a origem exata, por exemplo `https://bees.seu-dominio.example`. O serviço continua ligado a `127.0.0.1`; o proxy fornece certificado, preserva o Host público e define `X-Forwarded-Proto: https`, removendo valores enviados pelo cliente. Uvicorn só confia nos cabeçalhos encaminhados pelo endereço loopback explicitamente configurado.

O modo público permite somente essa origem, usa cookie `Secure` e recusa API sensível quando o transporte reconhecido não é HTTPS. Não usar subcaminho no domínio nem liberar a porta HTTP interna à rede. TLS, DNS, firewall e supervisor são provisionados pelo operador; esta implementação não os instala. O servidor Vite é apenas para desenvolvimento local.

Exemplo para um proxy Nginx já instalado com certificado válido:

```nginx
location / {
    proxy_pass http://127.0.0.1:8000;
    proxy_set_header Host $http_host;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header X-Forwarded-For $remote_addr;
}
```

Inicie a API com `uv run --locked bees-api --public-url https://bees.seu-dominio.example`. Abra o mesmo domínio no computador e no celular e entre com a mesma senha. Sessões são independentes; agentes e histórico são compartilhados. Configuração/testes do aplicativo não substituem validação do proxy/certificado no ambiente real.

## Diagnóstico e limites

- Código recusado: confira pasta de dados, validade e se outro código foi emitido. Gere um novo apenas antes da configuração inicial.
- Origem/Host recusado: confira URL e porta; no servidor confira `BEES_PUBLIC_URL`, Host preservado e proxy TLS local.
- Sessão expirada: entre novamente. A tela não reenvia automaticamente a mensagem que falhou.
- Cofre indisponível: confira conta Windows original ou chave externa Linux, permissões e integridade dos arquivos.
- Modelo indisponível: confira endpoint, identificador, credenciais e capacidades; não há troca automática de provedor.

Pedidos têm limite de corpo de 64 KiB e prazo de recebimento. Chamadas de modelo têm limites dos drivers, duas simultâneas por processo e uma por conversa na API web. Estes controles não implementam orçamento financeiro nem cancelamento de processamento remoto. Histórico é persistido; a API retorna as últimas mil mensagens; a tela mostra as últimas duzentas e indica quando há anteriores.

Referências de projeto: [OWASP — sessões](https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html), [OWASP — CSRF](https://cheatsheetseries.owasp.org/cheatsheets/Cross-Site_Request_Forgery_Prevention_Cheat_Sheet.html), [Argon2](https://argon2-cffi.readthedocs.io/en/stable/api.html), [DPAPI](https://learn.microsoft.com/en-us/windows/desktop/api/Dpapi/nf-dpapi-cryptprotectdata), [Fernet](https://cryptography.io/en/stable/fernet/).

## Evidência do checkpoint inicial

Em 05/10/2026, no Windows: 322 testes Python e 26 testes web aprovados, Ruff/ESLint e build aprovados. Cinco casos condicionais ficaram sem execução por depender de POSIX ou privilégio de symlink; DPAPI real, hardlinks e junctions foram exercitados. Testes cobrem autenticação, revogação, CSRF/Host/Origin/HTTPS, persistência após reinício, criação idempotente, falhas de gravação e concorrência.

A interface foi exercitada com uma instalação descartável: primeiro acesso, diagnóstico com chave fictícia, criação, conversa, reload, logout/login, pt-BR/en/es e viewport de 390 px sem overflow horizontal. Respostas vieram de transporte controlado; não representam validação de modelo real. Linux/CI e proxy/certificado HTTPS real não foram executados neste checkpoint.
