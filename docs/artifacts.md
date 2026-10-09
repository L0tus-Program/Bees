# Artefatos

Implementação inicial de BEES-018.1 em `packages/core/src/bees_core/artifacts.py`. O `ArtifactStore` publica arquivos produzidos por código confiável do Bees, guarda versões imutáveis e serve download autenticado. Anexação pelo usuário, interface de cartões e revisão (BEES-018.2) e geração de relatório/tabela por tarefa real (BEES-018.3) ainda não existem.

## Contrato

- **Formatos aceitos:** `text/plain`, `text/markdown`, `text/csv` e `application/json`. Todos em UTF-8 estrito, sem NUL. JSON sem chaves duplicadas nem `NaN`; CSV analisado em modo estrito. O limite padrão é 25 MiB por arquivo. Outros tipos são recusados antes de gravar qualquer coisa.
- **Nome:** só para exibição, de 1 a 200 caracteres, sem separadores de caminho, controles, caracteres de formatação invisíveis ou espaços nas pontas. O caminho físico deriva apenas do UUID do artefato (`artifacts/blobs/<uuid>`). O banco guarda a chave `blob/v1/<uuid>`, e uma chave diferente da esperada é tratada como violação de integridade, nunca como caminho.
- **Estados:**
  - `draft`: publicação ainda não confirmada; nunca é servido.
  - `ready`: blob publicado e relido com o mesmo SHA-256 e tamanho.
  - `failed`: publicação recusada, com `metadata.error_code`; terminal, evidência preservada.
  - `deleted`: reservado; não há exclusão neste recorte.
- **Versões:** cada versão é um registro imutável com `series_id`, `version` e `previous_id`. Uma revisão cria nova versão a partir de uma versão `ready` e preserva a anterior. `(series_id, version)` é único: entre duas versões concorrentes a partir da mesma base, apenas uma é criada; a outra recebe `artifact_version_conflict`. Registros anteriores à migração 0009 não têm série; a primeira nova versão inicia a série pelo id do legado.
- **Vínculo:** todo artefato pertence a uma tarefa e, opcionalmente, a uma execução da mesma tarefa. O acesso confere que a tarefa pertence à abelha do caminho. IDs de outra abelha resultam em "não encontrado", sem bytes.

## Publicação

Banco e arquivos não fecham numa transação única ([ADR 0001](adr/0001-foundation.md)). A sequência é:

1. Validar formato, nome e tamanho e calcular SHA-256.
2. Criar a linha `draft` já com hash, tamanho e chave. Isso vincula o id ao conteúdo antes de qualquer arquivo.
3. Gravar o staging próprio (`staging/<uuid>.<aleatório>.part`, criação exclusiva, `0600` no POSIX, `fsync`). Publicar sem sobrescrever (`rename` no Windows, `link` no POSIX) e sincronizar diretórios no POSIX.
4. Reler o blob publicado: sem link/reparse, um único hard link, mesmo arquivo aberto, tamanho e hash esperados.
5. Atualizar `draft → ready` com controle de revisão.

**Repetição com o mesmo id e conteúdo:**
- termina uma publicação interrompida;
- devolve o registro já pronto, sem novo blob;
- com conteúdo ou vínculos diferentes, é recusada (`artifact_conflict`).

**Se o destino já existir com outro conteúdo:** o rascunho vira `failed` e o arquivo existente é preservado.

**Falha de disco:** o rascunho vira `failed`, e o staging parcial próprio é removido.

**Processo interrompido:** a linha fica `draft`, para reconciliação e repetição explícita.

`reconcile()` é somente leitura. Relata:
- rascunhos pendentes (sem blob), interrompidos (blob íntegro) e em conflito (blob divergente);
- prontos sem integridade e falhas com blob;
- blobs órfãos e arquivos de staging.

Não apaga, não move, não reaproveita staging alheio e não conclui publicações.

## API

Rotas autenticadas por sessão. Não há rota de upload nem de alteração neste recorte.

- `GET /api/v1/agents/{agent_id}/tasks/{task_id}/artifacts?offset=0&limit=100`
  - Pagina metadados seguros: id, tarefa, execução, nome, tipo, tamanho, hash, versão, série, anterior, estado, revisão e datas.
  - Nunca expõe chave de armazenamento, caminho ou `metadata`.
- `GET /api/v1/agents/{agent_id}/artifacts/{artifact_id}/download`
  - Relê e confere o blob antes de enviar qualquer byte.
  - Responde sempre como anexo (`Content-Disposition: attachment` com `filename*` UTF-8), `nosniff`, `Cache-Control: no-store` e uma `Content-Security-Policy: sandbox` adicional à política global.
  - Rascunho, blob ausente ou alterado retornam `409` com código (`artifact_not_ready`, `artifact_blob_missing`, `artifact_integrity_failed`), sem caminho nem conteúdo.

## Operação e limites

Os blobs ficam em `BEES_DATA_DIR/artifacts`, no mesmo volume `bees-data` da distribuição Docker. Backup da base SQLite não inclui blobs nem o cofre. Copie a pasta de dados inteira com API e worker parados, como descrito em [persistência](persistence.md) e [containers](containers.md).

Não há exclusão, retenção, cota por abelha ou exportação de artefatos. Essas políticas pertencem a BEES-023/024.

No Windows, a privacidade depende das ACLs da pasta de dados; a biblioteca não altera ACLs. A verificação de links cobre os ancestrais da raiz e os arquivos publicados, mas não é isolamento contra outro processo da mesma conta ou de administrador.

## Validação

Testes com SQLite e arquivos reais em diretórios próprios (`packages/core/tests/test_artifacts.py` e `apps/api/tests/test_artifacts_api.py`) cobrem:
- publicação e leitura independente dos bytes no disco após reinício;
- interrupção antes e depois do blob, com repetição idêntica e conflitante;
- blob estranho no destino, disco cheio, blob alterado, ausente, com hard link ou com chave reescrita;
- raiz com symlink (pulado no Windows sem privilégio);
- acesso por outra abelha;
- versões concorrentes;
- gatilhos SQL de imutabilidade e append-only;
- validação de formatos e nomes;
- reconciliação sem mutação;
- upgrade 8→9 preservando registro legado;
- headers e respostas da API.
