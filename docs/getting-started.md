# Primeiros passos — primeira versão

Este guia leva da instalação ao uso diário em cerca de 15 minutos, sem terminal (exceto onde indicado). Para detalhes, siga os links de cada passo.

## 1. Instalar e abrir

- **Windows:** instale o Docker Desktop (containers Linux), abra-o e dê dois cliques em **Iniciar Bees.vbs** na pasta do projeto.
- **Linux/macOS/servidor:** com Docker Engine 28+ e Compose 2.20+, rode `docker compose up --build --detach --wait` e abra [localhost:8080](http://localhost:8080). Para o primeiro acesso, gere o código com `docker compose exec bees bees-auth bootstrap`.

Na primeira vez, informe seu nome e uma senha (12+ caracteres). Esqueceu a senha depois? Veja [redefinição](onboarding.md#esqueci-a-senha).

## 2. Escolher o modelo

Ao criar a primeira abelha, escolha o provedor:

| Opção | Quando usar | O que informar |
| --- | --- | --- |
| OpenAI, Anthropic, Gemini, xAI (Grok), Groq, OpenRouter | Melhor qualidade, pago por uso | Chave de API (guardada cifrada no cofre local) |
| Ollama (local) | Privacidade total, sem custo por uso | Nada: o painel **Modelo local** mostra modelos instalados e baixa sugeridos com sua confirmação |
| OpenAI compatível | Outros serviços/servidores próprios | Endereço e, se houver, chave |

Para o modo agente, marque **Chamadas de ferramenta** nas opções avançadas (modelos atuais da OpenAI, Anthropic, Gemini, xAI e Qwen 3/Llama 3.2 no Ollama suportam). Clique **Testar** antes de salvar. Detalhes em [provedores](providers.md).

Para o Ollama na instalação Docker: `docker compose --profile local-model up -d`, depois use o painel **Modelo local** para baixar, por exemplo, *Qwen 3 4B* (~2,6 GB, 8 GB de RAM).

## 3. Conversar e delegar

- **Conversa:** escreva normalmente. A abelha lembra preferências salvas em **Memórias** e no perfil.
- **Agente com ferramentas:** no chat, clique **Ativar ferramentas recomendadas** uma vez e escolha o modo **Agente com ferramentas**. A abelha pesquisa na web, lê páginas/PDFs, salva memórias, publica arquivos e usa computadores. Ações sensíveis aparecem como cartões de decisão: **Permitir uma vez**, **Sempre permitir esta ferramenta** ou **Recusar**. Veja [agente](agent.md).
- **Pesquisa com fontes:** escolha o modo **Pesquisa com fontes**. A resposta cita as fontes lidas e gera `pesquisa.md` e `pesquisa-fontes.json` em **Entregas**, com links não verificados destacados.
- **Tarefas em segundo plano:** acompanhe em **Conversa → Tarefas**; resultados e arquivos ficam em **Entregas** mesmo após fechar o navegador.

## 4. Dar um computador à abelha

Na aba **Computador**: **Parear runtime** → **Criar computador**. Em segundos há um desktop Linux exclusivo com Chromium, terminal e LibreOffice. Use **Ver tela** para acompanhar e **Assumir controle** para fazer logins; a abelha não age no navegador enquanto você estiver no controle. Veja [computador da abelha](computer.md).

## 5. Conectar sua máquina (opcional)

Em **Computador → Máquina pessoal**, gere um convite e rode o conector na sua máquina com as pastas que quer compartilhar. Você escolhe por abelha: leitura/escrita, terminal e navegador; revogue a qualquer momento. Veja [conector pessoal](connector.md).

## 6. Automatizar

- **Skills:** salve um procedimento que funcionou (na aba **Skills**, a partir de uma entrega ou do zero) e execute de novo com outras entradas. Skills que declaram ferramentas do agente rodam no modo agente.
- **Rotinas:** na aba **Rotinas**, agende uma skill (diária, semanal, por intervalo) com destino em uma conversa. Exemplo: "Resumo diário das notícias de IA às 8h". Rotinas em modo agente usam as mesmas permissões e aprovações.
- **Avisos:** a aba **Atenção** define quando você é avisado (decisões pendentes, falhas, conclusões).

## 7. Segurança e controle

- **Parar tudo:** o botão de parada global no topo interrompe novos despachos de todas as abelhas.
- **Autonomia:** a aba **Autonomia** define regras por ferramenta/escopo; a aba **Ferramentas** revoga acessos.
- **Consumo:** a aba **Consumo e atividade** mostra tokens e limites por abelha e tarefa.
- **Exportar dados:** o menu **Dados** exporta e restaura tudo sem segredos. Faça backup também do volume `bees-secrets` (chave do cofre).

## Comparação rápida com assistentes hospedados

| Necessidade | No Bees |
| --- | --- |
| Assistente com memória e várias "personas" | Várias abelhas, cada uma com perfil, memória, modelo e permissões próprios |
| Pesquisa na web com citações | Modo **Pesquisa**, fontes verificadas por leitura |
| Agente que usa um computador | Computador próprio da abelha, com tela ao vivo e controle humano |
| Tarefas recorrentes | Skills + rotinas agendadas com entrega na conversa |
| Privacidade/escolha de modelo | Modelos remotos ou locais, troca a qualquer momento; dados ficam na sua máquina |

Limites desta versão estão em [matriz de regressão](regression-matrix.md) e em cada guia.
