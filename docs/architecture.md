# Arquitetura do Bees

Decisão vigente: [ADR 0001](adr/0001-foundation.md).

## Componentes

```mermaid
flowchart LR
    UI["Frontend React"] -->|REST + SSE| API["API FastAPI"]
    API --> DB["SQLite APSW + artefatos"]
    WORKER["Worker e agenda duráveis"] --> DB
    WORKER --> MODELS["Adaptadores de modelos"]
    WORKER --> POLICY["Políticas e journal"]
    POLICY --> GATEWAY["Gateway de executores"]
    GATEWAY -->|WS autenticado| VM["VM Linux da abelha"]
    GATEWAY -->|WS autenticado| LOCAL["Conector pessoal Windows"]
    UI -->|controle autenticado| VIEW["Gateway visual"]
    VIEW --> VM
    VIEW --> LOCAL
```

O diagrama mostra o desenho alvo. A base atual contém API de saúde/status, autenticação individual, cofre, onboarding, perfis/memória editáveis e conversa web persistente com contexto limitado, além de packages/core com estado, migrações, repositórios e adaptadores remoto/local de texto e funções. A CLI compartilha referências de credenciais e conversa persistida; funções são pedidos validados, ainda sem execução. O worker textual, seu journal e políticas antes das gerações estão implementados, com regras editáveis por interface. Aprovações pontuais, agenda de rotinas, gateways e ambientes continuam pendentes; artefatos têm metadados persistentes, não publicação de blobs. Veja [políticas](policies.md), [tarefas](tasks.md), [primeiro acesso](onboarding.md), [persistência](persistence.md) e [modelos](providers.md).

A comunicação com LLMs usa HTTPX dentro de adaptadores próprios (`packages/core/providers`), com contratos de mensagens, capacidades, credenciais, catálogo e uso independentes do fornecedor. A interface escolhe provedores conhecidos sem expor sua URL como requisito de setup; sugestões de modelos vêm do catálogo versionado do serviço, com descoberta opcional e digitação manual para todos os provedores. Preparar/salvar configuração não depende do catálogo remoto nem afirma compatibilidade real do modelo. Não há LangChain, LangGraph, DeepAgents ou outro harness de agentes na implementação atual; adotar uma biblioteca de execução exige avaliar os contratos de políticas, retomada e estado, conforme a ADR.

## Contratos

| Contrato | Responsabilidade |
| --- | --- |
| ModelAdapter | Normalizar capabilities, mensagens, tools, streaming e uso; nenhum estado exclusivo do fornecedor. |
| UnitOfWork/Repositories | Transações de domínio, revisão e migrações; não abrir transação durante ferramenta. |
| PolicyService | Avaliar política atual, escopo/parâmetros e decisões humanas antes de ação. |
| ExecutorTransport | Pareamento, capabilities, comando versionado, fencing e resultado. |
| TaskService/TaskWorker | Comandos idempotentes, fila durável, snapshot de contexto, limites, leases e journal de chamadas de modelo. |
| ActionJournal | Intenção, início, confirmação e resultado desconhecido; reconciliar antes de repetir. |
| EnvironmentAdapter | Provisionamento/status, persistência, controle e limites reais. |
| SecretStore | Referências opacas, acesso controlado, rotação e ausência no contexto/log. |
| ArtifactStore | Staging/publicação, versões, hashes e download autorizado. |
| EventStore/Outbox | Eventos de estado duráveis e entrega deduplicável. |
| RoutineStore | Agenda IANA, ocorrência única, atraso/sobreposição e estado persistido. |

Um controle por tela. Uma conexão não concede acesso. Nenhum fallback silencioso de máquina/provedor. Rotinas e agentes usam a mesma política atual.

## Organização prevista

- apps/api e apps/web: primeira base executável.
- packages/core: persistência e contratos/drivers de modelo implementados; apps/worker compõe o executor de tarefas independente. apps/connector e runtime serão criados conforme a implementação do comportamento.
- docs: decisões, instalação e contratos públicos, sem depender dos arquivos locais ignorados.

O plano de controle pode rodar localmente ou em VPS. Computador próprio é VM, hospedada no mesmo host quando suportado ou em infraestrutura do usuário. Banco não é compartilhado com executores e não fica em filesystem de rede.
