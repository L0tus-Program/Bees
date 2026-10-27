# Aceite da primeira versão

## Escopo confirmado em 26/10/2026

O mantenedor solicitou revisar as entregas já integradas, completar a v1 e fechar
as issues cujos critérios forem comprovados. Escolheu **Apache-2.0** para o Bees.
As evoluções FUT-001–FUT-012 (#100–#111) permanecem no backlog e não são condições
de aceite da v1. Isso não autoriza publicar pacotes, imagens ou release nem
integrar `dev` em `main`.

O mantenedor também confirmou **v1 com container; Hyper-V no backlog**. Os
pré-requisitos/provas específicos de VM (#27–#31 e #96) permanecem abertos como
trabalho posterior. O computador em container ainda exige suas provas próprias
sob #26; a decisão não comprova seu isolamento nem conclui seu aceite.

O estado global ainda é **em verificação**. Encerrar uma subtarefa não encerra
automaticamente sua história nem substitui um requisito de ambiente real por mock.
As ADRs [0003](adr/0003-agent-tools.md) e [0004](adr/0004-computer-runtime.md)
registram o ciclo do agente e o runtime em container; provas físicas de VM devem
ser identificadas separadamente, sem atribuir isolamento de VM ao container.

## Base revisada

`dev` em `c7645251cacf1ee258db7b15aed885ce17149124` (integrações até #170):

- CI remota [40234313747](https://github.com/L0tus-Program/Bees/actions/runs/40234313747)
  concluída com sucesso.
- Regressão local completa: 3.020 testes Python passaram, 61 skips de plataforma;
  1.049 testes web em 87 arquivos passaram. Ruff, formatação, lint, tipos e build
  passaram. Essas contagens pertencem à base; mudanças posteriores têm seus
  resultados registrados no PR correspondente, sem somar execuções sobrepostas.
- Prova local real com Ollama0.40.6 CPU/Qwen3:1.7b: conversa com marcador exato,
  delegação `create_text_task`, replay sem nova geração, tarefa delegada com
  resultado exato e `publish_file` com bytes conferidos. Reabertura preservou o
  histórico; fila ociosa não redisparou. Caso final: cinco gerações locais e
  quinze pedidos HTTP ao daemon configurado, sem fornecedor alternativo/pago.
  Casos diagnósticos anteriores não entram nessa contagem. Memória foi enviada
  como referência, e não como autoridade. Isso não comprova qualidade ampla,
  pesquisa externa completa nem execução de VM.

## Recortes revisados e aceitos

| Issue | Recorte concluído | Evidência conferida |
| --- | --- | --- |
| #7 | Conversa e delegação locais | Prova real acima e negativos de adapters, wire, execução e delegação |
| #61 | Corpus e rubrica de pesquisa | Corpus versionado, fontes problemáticas e testes de pesquisa |
| #65 | Contratos/revisões de skills | Imutabilidade, origem, CAS, replay, restart e importação fechada |
| #66 | Rascunho de skill a partir de processo | Vínculo do resultado/fontes, revisão humana, export/restore e negativos |
| #67 | Execução de skill com revisão/entradas atuais | Duas entradas com arquivos exatos distintos no modelo local e negativos de autoridade |
| #69 | Agenda/ocorrências | DST, unicidade, histórico e limitação de atrasos |
| #70 | Scheduler e worker | Processo real sem frontend, restart/replay e negativos de concorrência, revogação e unknown |
| #25 | Ferramentas compartilhadas nas rotinas | Dispatcher/journal comuns e bloqueio de versão/recurso/grant obsoletos |
| #76 | Consumo/atividade na interface | SDK fechado, confirmação canônica, reload, idiomas e prova de API/UI registrada |
| #78 | Exportação lógica | Snapshot banco/blob, inventário, hashes, sanitização, concorrência e falhas |
| #79 | Restauração inativa | Publicação atômica, preservação, bloqueios e prova real de continuidade registrada |

Os comentários e checklists dessas issues registram a revisão antes do fechamento.
Os testes com recursos/processos próprios são separados de transportes controlados;
nenhum pacote privado, token, prompt ou dump foi incluído como evidência pública.

## Prova de skills e rotinas com modelo local

Na revisão de 26/10/2026, um worker em processo próprio executou duas entradas de
skill com arquivos de bytes distintos, uma rotina agendada sem frontend e uma
execução manual. Quatro tarefas terminaram; os quatro arquivos foram conferidos;
o resultado das duas ocorrências da rotina chegou à conversa de destino.
Reiniciar o worker não duplicou a tarefa agendada, e reenviar o UUID manual
preservou a mesma tarefa. O caso final usou oito gerações locais de Qwen3:1.7b.

Uma variante anterior da instrução retornou resposta vazia e foi pausada como
`empty_response`, sem efeito de ferramenta. O caso aprovado usou uma instrução
curta e explícita; não equivale a aceite amplo do modelo para qualquer procedimento.
O produto não traduziu nem reescreveu silenciosamente o pedido para contornar a falha.

A revisão da interface encontrou e corrigiu dois problemas: disponibilidade manual
do modo agente indevidamente condicionada ao recurso textual e aviso de ativação
que descrevia esse modo como preparatório. A prova posterior com API, worker e
Chromium reais teve doze capturas em pt-BR/en/es, larguras390/1280, confirmação
manual habilitada, aviso correto e pausa preservada após reload. Nenhuma tarefa
manual ou geração nova foi criada nessa prova visual.

## Pendências de aceite amplo

A revisão de silêncio encontrou atenção de conclusão criada mesmo sem mensagem
no chat. A correção consulta resultados canônicos no writer, preservando histórico
e as atenções de erro/decisão/unknown. Integração de API autenticada e worker com
provedor **HTTP controlado** confirmou zero itens de atenção e nenhuma repetição
após restart; isso não é prova de geração real de modelo. Nos dois novos cenários
com Qwen3:1.7b, o modelo retornou resposta vazia antes de produzir o sinal; ambos
foram pausados com atenção de falha e sem ações de ferramenta. O aceite amplo de
qualidade continua separado dessas verificações de contrato.

As demais issues precisam de sua própria revisão ou implementação. Em particular,
modelo remoto com credencial, fluxos completos dos ambientes suportados, instalação
limpa e falhas reais não são comprovados apenas pela contagem de testes.
Os avisos nativos e os fontes correspondentes das imagens/binários finais devem
ser conferidos pelo responsável antes da publicação, conforme [licenças](licensing.md).
