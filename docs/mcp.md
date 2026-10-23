# Conectores MCP

Dê à abelha acesso a serviços que oferecem um servidor [MCP](https://modelcontextprotocol.io) (Model Context Protocol) por **Streamable HTTP**: e-mail, agenda, tarefas, repositórios, bases de conhecimento, bancos de dados.

## Configurar

1. Aba **Ferramentas** → **Conectores MCP** → informe um nome curto (`agenda`), o endereço HTTPS do servidor e, se exigido, o token (enviado como `Authorization: Bearer`, cifrado no cofre).
2. **Conectar e ler ferramentas** inicializa a sessão MCP e guarda o catálogo (até 128 ferramentas com esquema válido). Use **Atualizar ferramentas** quando o servidor mudar.
3. Conceda o pacote **Conectores MCP** (`bees.mcp`) em Ferramentas ou por **Ativar ferramentas recomendadas** no chat.

## Como a abelha usa

- `mcp_list_tools` (leitura, permitida): conectores e ferramentas disponíveis, com esquema de argumentos.
- `mcp_call` (efeito externo, **pede aprovação**): chama `server`/`tool` com `arguments`. Os argumentos são validados contra o esquema guardado antes de qualquer envio; ferramentas fora do catálogo são recusadas. “Sempre permitir” cria uma regra para essa ferramenta; regras de Autonomia podem ser restringidas por servidor e ferramenta.
- O resultado (texto, dados estruturados) é dado externo não confiável e não vira instrução.
- Falha de conexão antes do envio é falha sem efeito; perda da resposta depois do envio vira **resultado incerto** (não repetido automaticamente).

## Limites

Somente HTTPS (HTTP apenas em `localhost`), sem redirecionamentos. Servidores MCP locais por stdio não são executados pelo Bees; exponha-os por HTTP (por exemplo com um proxy MCP) se necessário. OAuth interativo não é suportado nesta versão: use token de acesso. Conectores não entram na exportação.
