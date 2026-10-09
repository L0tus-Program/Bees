# Persistência do estado individual

Implementação de BEES-003 em `packages/core`, independente de FastAPI e dos fornecedores de modelos. O serviço inicializa a base antes de aceitar tráfego. Reiniciar o frontend ou criar uma nova instância do serviço não recria os registros.

## Dados canônicos

| Repositório | Conteúdo e vínculos |
| --- | --- |
| agents | Identidade, instruções e metadados de configuração do modelo. |
| conversations / messages | Conversa do agente e mensagens append-only. |
| tasks / runs | Objetivo, estado, execução e checkpoint; vínculos com conversa/rotina. |
| task_commands / model_calls / execution_leases | Comandos idempotentes, journal das gerações e fencing durável da fila textual (migração 0003). |
| actions | Intenção, parâmetros, estado de efeito, resultado, vínculo de execução e reconhecimento separado de resultado desconhecido (migração 0007). |
| provisioning_* | Planos imutáveis, autorizações pontuais, credenciais próprias por host, gerações/claims exclusivos, intents/receipts e comandos idempotentes (migração 0008). |
| policies / approvals | Escolhas registradas, revisão, escopo e decisão da ação. |
| plugins / tool_grants | Manifestos locais imutáveis e concessões por ferramenta/abelha; revisão, habilitação e vínculo canônico (migração 0004). |
| environments / host_jobs | Pedidos de computador e journal da operação no host, sem provisionador ativo (migração 0005). |
| host_link_installation / host_link_invites / host_links / host_link_commands / host_link_report_receipts | Pareamento de diagnóstico com hashes, TTL, revisão humana, sequência de relatórios e receipts; não executa jobs de VM (migração 0006). |
| routines | Agenda declarada, fuso IANA e opções de atraso/sobreposição. |
| memories | Memória de usuário, agente ou tarefa, conteúdo e origem. |
| artifacts | Metadados de resultado, referência de armazenamento, hash/tamanho, série e versão anterior (migração 0009); blobs ficam em `artifacts/` fora do SQLite. |
| budget_limits / usage_entries | Limite de tokens por abelha e registro de consumo: reserva antes da rede e liquidação final informada/incerta/liberada (migração 0010). |
| domain_events | Ledger de criação/edição/reconciliação com IDs, revisão e estado. |

UUIDs, datas UTC com timezone, validação de tipos e revisões fazem parte dos contratos. SQLite também verifica FKs, vínculos entre agentes/tarefas, estados e JSON. `tzdata` acompanha as dependências para validar fusos no Windows.

Os repositórios armazenam **registros**; não executam agenda ou autorização por si. [Políticas](policies.md) e [aprovações](approvals.md) compõem a autorização das gerações no executor. Rotinas ainda não possuem agenda em execução. Metadados de artefato `ready` exigem referência/hash. Pelo [ArtifactStore](artifacts.md), `ready` só é gravado depois de o blob ser publicado e relido com o mesmo hash/tamanho; registros gravados diretamente pelo repositório continuam sem essa prova. A camada de persistência não chama modelos nem executa ferramentas. O [driver de modelos](providers.md) usa essas transações para guardar conversas, com a chamada HTTP fora da unidade de trabalho.

## Transações e revisões

`Database.transaction()` cria e fecha uma conexão própria. Escritas usam BEGIN IMMEDIATE; leituras usam snapshot e query_only. As conexões aplicam WAL, synchronous=FULL, FKs e timeout de lock. Banco e processos API/worker devem ficar no mesmo host, em filesystem local; não usar pasta de rede ou sincronização para o estado ativo.

```python
from pathlib import Path

from bees_core.models import Agent, Memory
from bees_core.storage.database import Database
from bees_core.storage.store import StateStore

# Exemplo de desenvolvimento: escolha uma pasta de dados própria.
database = Database(Path("data") / "bees.sqlite3")
database.initialize()  # serviço parado para manutenção de esquema
store = StateStore(database)

with store.transaction(actor="user", source="local-example") as uow:
    agent = uow.agents.create(Agent(name="Pesquisadora"))
    memory = uow.memories.create(
        Memory(agent_id=agent.id, scope="agent", content="Preferir fontes primárias.")
    )

# Outra conexão lê o que foi confirmado, inclusive após reiniciar o processo.
with store.transaction(write=False) as uow:
    current = uow.memories.get(memory.id)

with store.transaction() as uow:
    changed = current.model_copy(update={"content": "Registrar fontes e datas."})
    current = uow.memories.update(changed, expected_revision=current.revision)
```

Uma revisão desatualizada gera `RevisionConflict`; releia antes de editar. Registro e evento são escritos juntos. Falha reverte a unidade de trabalho; falha de uma mutação também reverte seu savepoint quando o chamador a trata dentro da transação. Repositórios não sobrevivem ao fechamento da unidade de trabalho.

Mensagens são append-only. Após sair de prepared, ações preservam parâmetros; após despacho, preservam evidências. Resultado `outcome_unknown` não volta para `ready` e não é repetido automaticamente. `actions.reconcile(...)` é uma operação interna explícita, exige revisão e referência de evidência e apenas conclui como `confirmed` ou `failed_no_effect`. Não constitui verificação externa nem aprovação de usuário por si só.

Eventos guardam metadados de mudança, sem copiar textos, parâmetros ou resultados. Não são snapshots integrais de versões anteriores. Conteúdo livre pode ser privado: não coloque credenciais em `provider_config`, memória ou parâmetros; credenciais usam referências env:VAR ou vault:UUID no SecretStore. Actor/source/correlation são metadados internos, não prova de identidade ou permissão.

## Migrações e backup

SQL numerado fica em `bees_core/storage/migrations`. A tabela `schema_migrations` registra nome, versão, checksum SHA-256 e data. Checksums normalizam CRLF para LF, permitindo mover a instalação entre Windows e Linux. Não editar uma migração aplicada; acrescentar outra.

Ao iniciar, histórico adulterado, versão desconhecida ou base sem histórico reconhecido causam recusa. Não substituir a base nem tentar um fallback. Migrações pendentes são aplicadas em transação exclusiva; SQL versionado não pode encerrar essa transação ou desativar as restrições por PRAGMA/ATTACH.

A migração 0003 acrescenta fila/controladores, orçamento básico, comandos e journal de modelos. O worker usa `require_current_schema()` em modo somente leitura e recusa migrações pendentes. Consulte [tarefas](tasks.md) para interrupção e resultado desconhecido.

A migração 0004 acrescenta instalações declarativas e concessões locais por ferramenta/abelha. Manifesto/hash e identidade da concessão são imutáveis; habilitação muda com revisão. Eventos conservam digest, escopo e mudanças de habilitação sem conteúdo processado. Consulte [ferramentas](tools.md).

A migração 0005 acrescenta pedidos imutáveis de computador e jobs de host vinculados, com estados/revisões, UUIDs idempotentes e cancelamento atômico. Não existe provisionador ativo. Consulte [computadores](environments.md). O lock Windows inicializa seu byte de manutenção somente depois de adquirir a trava, evitando a corrida entre migradores diante de arquivo vazio.

A migração 0006 acrescenta identidade da instalação, convites, vínculos de diagnóstico, comandos humanos e receipts de relatórios. Credenciais são hashes; confirmação e relatórios têm revisões separadas. Há um host ativo por instalação, e revogação é terminal para o vínculo. Receipts são canônicos e não entram na limpeza de cache. O helper mantém estado cifrado próprio fora do banco do serviço; não recebe acesso a SQLite. Consulte [vínculo do host](environments.md#vínculo-de-diagnóstico-do-host).

A migração 0007 acrescenta vínculo imutável de execução às ações e a data de reconhecimento de resultado desconhecido. O vínculo conserva dono, tarefa, abelha, revisão de controle e gerações de leases. O reconhecimento exige comando humano explícito de retomada, sem mudar o estado `outcome_unknown` nem substituir evidências. Ações antigas continuam preservadas; sem vínculo válido não são despachadas pelo novo dispatcher. Consultas públicas não expõem esse vínculo nem resultados privados. Consulte [ferramentas](tools.md).

A migração 0008 acrescenta a autoridade própria de provisionamento: planos imutáveis, autorizações pontuais, credenciais em hash, gerações e claims exclusivos, intents/receipts, comandos e vínculo do VMID observado. Atualizações preservam as tabelas e checksums anteriores. Unknown mantém a instalação em quarentena, inclusive após novo pareamento de host; reconhecimento humano não libera efeitos. Hardware confirmado continua sem uso/boot. Consulte [provisionamento](provisioning.md).

A migração 0009 acrescenta a linhagem de versões dos artefatos: `series_id`, `previous_id`, unicidade de `storage_key` e de `(series_id, version)` entre registros não `failed`. Gatilhos exigem linhagem coerente na inserção e tornam imutáveis identidade, vínculos, chave de armazenamento, conteúdo declarado (hash/tamanho/tipo, já no rascunho), retorno de `ready` e estados terminais; linhas não podem ser apagadas. Linhas anteriores ficam sem série e preservam seus metadados; uma nova versão de um registro legado inicia a série pelo id dele. Uma base com `storage_key` legado duplicado não migra: a transação é revertida, a versão 8 e o backup ficam preservados, e a duplicidade precisa de decisão do operador. Consulte [artefatos](artifacts.md).

A migração 0010 acrescenta `budget_limits` e `usage_entries`. Identidade e reserva do consumo são imutáveis, a liquidação é final e as linhas são append-only. Limites são desativados, não apagados. Nenhum dado anterior é alterado. Consulte [consumo e limites](budgets.md).

**Atualização de esquema exige API, worker e outros escritores parados.** O lock de manutenção coordena migradores do Bees, mas não impede processos externos de abrir SQLite diretamente. Não é isolamento universal de manutenção.

Uma base existente recebe backup coerente pela Backup API antes de uma migração pendente. O arquivo aparece ao lado da base como `bees.sqlite3.backup-<instante-UTC>.sqlite3`; falha de backup impede a migração. O backup é verificado e preservado quando uma migração falha. Reinício sem migração pendente não cria outro backup.

Backups contêm os dados privados da base e não têm limpeza automática nesta etapa. Para restaurar, pare todos os processos e use um backup conhecido em um diretório limpo, sem reaproveitar WAL/SHM de outra versão. Restore operacional guiado e exportação lógica são entregas posteriores. Não copiar apenas o arquivo principal de uma base ativa. [Backup SQLite](https://sqlite.org/backup.html), [APSW Backup API](https://rogerbinns.github.io/apsw/connection.html#apsw.Connection.backup).

No Linux, diretórios novos são privados e a base usa permissões restritas. No Windows, as permissões dependem das ACLs da pasta do usuário; a biblioteca não modifica ACLs do sistema nem criptografa o banco. Escolha uma pasta privada.

## Retenção

Dados canônicos e ledger ficam preservados por padrão. Apenas `cache_entries` são derivados e descartáveis. `put_cache` usa TTL configurável; leituras ignoram entradas expiradas. `prune_cache` remove somente caches expirados, em lote limitado. O serviço executa um lote na inicialização; não há scheduler de limpeza contínua nesta etapa.

- BEES_DATA_DIR / --data-dir: pasta da base, padrão `data` na raiz do checkout.
- BEES_CACHE_TTL_SECONDS / --cache-ttl-seconds: TTL padrão de 86400 segundos; de 1 a 31536000.
- BEES_CACHE_PRUNE_LIMIT / --cache-prune-limit: até 1000 entradas expiradas por inicialização; de 1 a 1000.

Repositórios não oferecem deleção genérica nem TTL para histórico, aprovações, efeitos desconhecidos ou arquivos. Memórias possuem exclusão explícita com revisão: registro removido e evento sem conteúdo confirmados na mesma transação. Isso não elimina backups, páginas livres ou WAL; veja [memória](memory.md). Artefatos não têm exclusão nem retenção: linhas são append-only e blobs publicados não são removidos. Retenção de artefatos/logs/exportações será tratada com os recursos correspondentes. Caches e backups também podem conter dados privados.

## Serviço e validação

`GET /api/v1/state/status` informa apenas disponibilidade, engine e versão de esquema. Não revela textos, IDs de agentes ou caminho da base. A criação de abelhas e as conversas têm endpoints protegidos por sessão e CSRF, descritos em [onboarding](onboarding.md). Os repositórios e a CLI continuam interfaces internas de confiança do operador.

Testes usam bases reais em pastas temporárias: grafo inteiro reaberto em outro processo, rollback de registro/evento, revisões concorrentes, integridade de vínculos, backups/migrações com falha e retenção sem perda de estado canônico. Testes de lifecycle reabrem a API na mesma pasta e comprovam recusa de checksum alterado.

## Identidade e credenciais

A migração 0002 acrescenta identidade individual, bootstrap de uso único, sessões e limites de tentativas. Senhas são hashes Argon2id; tokens de sessão/bootstrap são armazenados por hash. Sessões têm validade absoluta e revogação persistida. Os códigos de teste de conexão ficam em memória por cinco minutos; uma criação confirmada possui evidência de idempotência no banco, permitindo reconhecer o mesmo pedido após reinício.

Chaves de modelo são arquivos cifrados em `BEES_DATA_DIR/vault`; o banco guarda referências opacas. Backup da base sozinho não inclui o cofre nem os blobs de `BEES_DATA_DIR/artifacts`; copie a pasta de dados inteira com os serviços parados. Preserve os arquivos e o acesso à chave externa no Linux ou ao perfil DPAPI original no Windows. Copiar blobs DPAPI para outro usuário/máquina não constitui restauração portável. Não há exportação/restauração guiada de segredos nesta etapa.
