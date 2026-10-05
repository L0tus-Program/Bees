import { afterEach, describe, expect, it, vi } from 'vitest'
import { setCsrfToken } from './client'
import { createMemory, deleteMemory, getMemories, isMemory, saveConfiguration, saveProfile, updateMemory } from './bee'
import { testModel } from './onboarding'
import type { ModelConfig } from './onboarding'
import { canReuseSecret, connectionConfig } from '../features/model/connection'
import { observedAt, safeSourceUrl, toDateTimeLocal } from '../features/memory/format'

const config: ModelConfig = { kind: 'openai_compatible', endpoint: 'https://models.example.test/v1', model: 'test-model', capabilities: { text: true, tool_calls: false }, secret_ref: 'vault:opaque-test-reference', timeout_seconds: 30 }
const agent = { id: 'bee-1', name: 'Test bee', purpose: 'Tests', instructions: '', provider_config: config, conversation_id: 'conversation-1', revision: 2, status: 'active', memory_enabled: true }
const memory = { id: 'memory-1', revision: 1, scope: 'agent', agent_id: 'bee-1', task_id: null, content: 'Prefiro exemplos práticos', kind: 'preference', source_ref: null, observed_at: null, created_at: '2026-10-05T12:00:00Z', updated_at: '2026-10-05T12:00:00Z' }

afterEach(() => { vi.unstubAllGlobals(); setCsrfToken() })

describe('profile and model configuration', () => {
  it('envia revisão esperada e opção real de memória sem configurar ferramentas', async () => {
    const fetcher = vi.fn().mockResolvedValue(Response.json(agent))
    vi.stubGlobal('fetch', fetcher); setCsrfToken('test-csrf')
    await saveProfile(agent.id, 1, { name: agent.name, purpose: 'New purpose', instructions: 'Concise answers', memory_enabled: false })
    expect(JSON.parse(fetcher.mock.calls[0][1].body)).toEqual({ expected_revision: 1, name: agent.name, purpose: 'New purpose', instructions: 'Concise answers', memory_enabled: false })
  })

  it('rejeita perfil retornado para outra abelha', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(Response.json({ ...agent, id: 'bee-2' }))); setCsrfToken('test-csrf')
    await expect(saveProfile(agent.id, 1, { name: agent.name, purpose: '', instructions: '', memory_enabled: true })).rejects.toMatchObject({ code: 'invalid_response' })
  })

  it('preserva receipt associado à abelha no teste e envia CAS na troca', async () => {
    const fetcher = vi.fn().mockResolvedValueOnce(Response.json({ status: 'ok', code: 'model_available', capabilities: config.capabilities, validation_token: 'test-receipt' })).mockResolvedValueOnce(Response.json(agent))
    vi.stubGlobal('fetch', fetcher); setCsrfToken('test-csrf')
    await testModel(config, '', undefined, 'bee-1')
    expect(JSON.parse(fetcher.mock.calls[0][1].body)).toEqual({ config, agent_id: 'bee-1' })
    const result = await saveConfiguration('bee-1', 1, config, 'test-receipt', '')
    expect(JSON.parse(fetcher.mock.calls[1][1].body)).toEqual({ expected_revision: 1, config, validation_token: 'test-receipt' })
    expect(result.conversation_id).toBe('conversation-1')
  })

  it('conflito de revisão não faz retry nem sobrescrita automática', async () => {
    const fetcher = vi.fn().mockResolvedValue(Response.json({ error: { code: 'state_conflict' } }, { status: 409 }))
    vi.stubGlobal('fetch', fetcher); setCsrfToken('test-csrf')
    await expect(saveConfiguration('bee-1', 1, config, 'test-receipt', '')).rejects.toMatchObject({ code: 'state_conflict' })
    expect(fetcher).toHaveBeenCalledTimes(1)
  })
})

describe('explicit credential reuse', () => {
  const values = { kind: config.kind, endpoint: config.endpoint, model: 'new-model', tools: false, reuse: false }
  it('não inclui a referência salva sem escolha explícita', () => {
    expect(connectionConfig(config, values).secret_ref).toBeUndefined()
    expect(connectionConfig(config, { ...values, reuse: true }).secret_ref).toBe(config.secret_ref)
    expect(connectionConfig(config, { ...values, reuse: true }).timeout_seconds).toBe(30)
  })

  it.each([
    { kind: 'openai_compatible' as const, endpoint: 'https://other.example.test/v1' },
    { kind: 'openai_compatible' as const, endpoint: 'https://models.example.test/v2' },
    { kind: 'ollama' as const, endpoint: 'http://127.0.0.1:11434' },
  ])('não reaproveita referência em outro destino/tipo: %j', (destination) => {
    expect(canReuseSecret(config, destination)).toBe(false)
    expect(connectionConfig(config, { ...values, ...destination, reuse: true }).secret_ref).toBeUndefined()
  })
})

describe('memory scopes and revision', () => {
  it('lista contexto pessoal e desta abelha; só oferece tarefas retornadas', async () => {
    const personal = { ...memory, id: 'personal-1', scope: 'user', agent_id: null }
    const data = { memories: [memory, personal], tasks: [], has_more: false }
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(Response.json(data)))
    await expect(getMemories('bee-1')).resolves.toEqual(data)
  })

  it('recusa memória de outra abelha em resposta aparentemente válida', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(Response.json({ memories: [{ ...memory, agent_id: 'bee-2' }], tasks: [], has_more: false })))
    await expect(getMemories('bee-1')).rejects.toMatchObject({ code: 'invalid_response' })
  })

  it('cria pessoal sem anexar agent_id e atualiza com revisão mantendo escopo', async () => {
    const personal = { ...memory, scope: 'user', agent_id: null }
    const fetcher = vi.fn().mockImplementation(() => Promise.resolve(Response.json(personal)))
    vi.stubGlobal('fetch', fetcher); setCsrfToken('test-csrf')
    const data = { content: memory.content, kind: 'preference' as const, source_ref: null, observed_at: null }
    await createMemory('bee-1', { ...data, scope: 'user' })
    expect(JSON.parse(fetcher.mock.calls[0][1].body)).toEqual({ ...data, scope: 'user' })
    await updateMemory('bee-1', memory.id, 1, data)
    expect(JSON.parse(fetcher.mock.calls[1][1].body)).toEqual({ ...data, expected_revision: 1 })
  })

  it('delete envia revisão e aceita resposta 204 sem reenviar', async () => {
    const fetcher = vi.fn().mockResolvedValue(new Response(null, { status: 204 }))
    vi.stubGlobal('fetch', fetcher); setCsrfToken('test-csrf')
    await deleteMemory('bee-1', memory.id, 1)
    expect(fetcher.mock.calls[0][0]).toBe('/api/v1/agents/bee-1/memories/memory-1/delete')
    expect(JSON.parse(fetcher.mock.calls[0][1].body)).toEqual({ expected_revision: 1 })
    expect(fetcher).toHaveBeenCalledTimes(1)
  })

  it('fatos exigem procedência e data; tarefa exige vínculos explícitos', () => {
    expect(isMemory({ ...memory, kind: 'fact' })).toBe(false)
    expect(isMemory({ ...memory, kind: 'fact', source_ref: 'https://example.test/fact', observed_at: '2026-10-05T12:00:00Z' })).toBe(true)
    expect(isMemory({ ...memory, scope: 'task' })).toBe(false)
    expect(isMemory({ ...memory, scope: 'task', task_id: 'task-1' })).toBe(true)
  })
})

describe('memory evidence presentation', () => {
  it('datas locais voltam para o mesmo instante em UTC', () => {
    const instant = '2026-10-05T12:34:00.000Z'
    expect(observedAt(toDateTimeLocal(instant))).toBe(instant)
    expect(observedAt('')).toBeNull()
    expect(() => observedAt('invalid-date')).toThrow('invalid_observed_at')
  })

  it.each(['javascript:alert(1)', 'data:text/html,test', 'file:///test', 'https://user:password@example.test', 'Documento interno'])('não transforma referência insegura em link: %s', (source) => {
    expect(safeSourceUrl(source)).toBeNull()
  })

  it('fontes HTTP/HTTPS podem ser links sem transformar conteúdo em instruções', () => {
    expect(safeSourceUrl('https://example.test/fact')).toBe('https://example.test/fact')
    expect(safeSourceUrl('http://127.0.0.1/source')).toBe('http://127.0.0.1/source')
  })
})
