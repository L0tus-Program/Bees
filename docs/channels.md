# Canais: Telegram e Discord

Converse com uma abelha pelo celular. O canal espelha a **conversa principal** da abelha: suas mensagens entram nela e as respostas da abelha — inclusive resultados do modo agente e de rotinas com destino nessa conversa — são enviadas ao app. Não exige URL pública: o worker usa polling.

## Conectar

Na aba **Canais** da abelha:

- **Telegram:** fale com [@BotFather](https://t.me/BotFather), envie `/newbot` e cole o token. Depois de **Conectar**, abra o link mostrado (ou envie `/start CÓDIGO` ao bot).
- **Discord:** no [Developer Portal](https://discord.com/developers/applications) crie um aplicativo → **Bot**, ative **Message Content Intent**, copie o token e convide o bot para um servidor seu (permissões: ver canal, ler histórico, enviar mensagens). Informe o **ID do canal** (modo desenvolvedor → copiar ID) e envie o código nesse canal.

O código tem 8 caracteres, vale 30 minutos e uma vez. Só quem o enviou conversa com a abelha; mensagens de outras pessoas são ignoradas sem resposta. **Gerar novo código** permite trocar o dispositivo pareado.

## Uso

Comandos no chat:

| Comando | Efeito |
| --- | --- |
| `/modo conversa` | Respostas diretas do modelo |
| `/modo agente` | A abelha usa ferramentas (web, computador, máquina pessoal) |
| `/modo pesquisa` | Pesquisa com fontes; relatório em **Entregas** |
| `/status` | Mostra o modo |
| `/ajuda` | Lista os comandos |

No modo agente, ações sensíveis geram um aviso no app (“A abelha precisa da sua decisão…”); a decisão é tomada somente na interface do Bees (`BEES_PUBLIC_URL` aparece no aviso quando configurado). Mensagens que não são texto recebem uma explicação.

## Garantias e limites

- Token do bot cifrado no cofre; o banco guarda só a referência e o hash do código. **Desconectar** apaga o token do cofre.
- Cada mensagem recebida é registrada antes de qualquer efeito (`channel_events`): reinício ou reentrega não gera nova resposta paga.
- Saída é “pelo menos uma vez”: uma queda entre enviar e registrar pode repetir a última mensagem, nunca uma geração.
- Telegram: somente chats privados com o bot. Um token só pode ter um leitor (webhook ou outro programa causam `channel_conflict`).
- Discord: um canal por vínculo, polling a cada 3 s; mensagens de bots são ignoradas e menções são desativadas nas respostas.
- Canais não entram na exportação (contêm referência de segredo); após restaurar, conecte de novo.
- O worker (`bees-worker`) precisa estar ativo. WhatsApp exige webhook público e conta Meta Business; não está incluído nesta versão.

API: `GET/POST /api/v1/agents/{agent}/channels`, `POST .../{id}/update` (`mode`, `paused`), `.../{id}/code`, `.../{id}/revoke`, todos com `expected_revision`.
