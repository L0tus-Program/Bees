import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError, request, setCsrfToken } from './client'
import { authStatus, configurationSignature, createAgent, isAgent, messages, testModel } from './onboarding'
import type { ModelConfig } from './onboarding'

const config: ModelConfig = { kind: 'ollama', endpoint: 'http://127.0.0.1:11434', model: 'test-model', capabilities: { text: true, tool_calls: false } }
const agent = { id: 'bee-1', name: 'Test bee', purpose: 'Tests', instructions: '', provider_config: config, conversation_id: 'conversation-1', revision: 1, status: 'active', memory_enabled: true }

afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers(); setCsrfToken() })

describe('authenticated client', () => {
  it('obtém CSRF em memória e inclui cookie e token nas mutações', async () => {
    const fetcher = vi.fn().mockResolvedValueOnce(Response.json({ configured: true, authenticated: true, user: { name: 'Tester' }, csrf_token: 'test-csrf' })).mockResolvedValueOnce(Response.json({ ok: true }))
    vi.stubGlobal('fetch', fetcher)
    await authStatus()
    await request('/sample', { body: {}, authenticated: true })
    expect(fetcher).toHaveBeenLastCalledWith('/api/v1/sample', expect.objectContaining({ method: 'POST', credentials: 'same-origin', headers: expect.objectContaining({ 'X-Bees-CSRF': 'test-csrf' }) }))
  })

  it('não envia mutação autenticada sem CSRF', async () => {
    const fetcher = vi.fn()
    vi.stubGlobal('fetch', fetcher)
    await expect(request('/sample', { body: {}, authenticated: true })).rejects.toMatchObject({ code: 'csrf_missing' })
    expect(fetcher).not.toHaveBeenCalled()
  })

  it('sessão expirada invalida CSRF e emite evento para limpar a interface', async () => {
    const dispatchEvent = vi.fn()
    vi.stubGlobal('window', { dispatchEvent })
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(Response.json({ error: { code: 'authentication_required', message: 'Never rendered raw' } }, { status: 401 })))
    setCsrfToken('test-csrf')
    await expect(request('/private', { authenticated: true })).rejects.toMatchObject({ code: 'authentication_required' })
    expect(dispatchEvent).toHaveBeenCalledWith(expect.objectContaining({ type: 'bees:session-expired' }))
    await expect(request('/private', { authenticated: true, body: {} })).rejects.toMatchObject({ code: 'csrf_missing' })
  })

  it('não coloca mensagens arbitrárias do servidor em erros', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(Response.json({ error: { code: 'invalid_config', message: 'FAKE_TEST_SECRET_DO_NOT_RENDER' } }, { status: 422 })))
    try { await request('/sample') } catch (error) {
      expect(error).toBeInstanceOf(ApiError)
      expect(JSON.stringify(error)).not.toContain('FAKE_TEST_SECRET_DO_NOT_RENDER')
      expect((error as Error).message).toBe('invalid_config')
    }
  })

  it('rejeita códigos malformados que poderiam ecoar payloads', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(Response.json({ error: { code: 'key=FAKE_SECRET', message: 'Unsafe' } }, { status: 500 })))
    await expect(request('/sample')).rejects.toMatchObject({ code: 'http_500' })
  })

  it('trata perda de conexão após POST como resultado desconhecido sem retry', async () => {
    const fetcher = vi.fn().mockRejectedValue(new TypeError('Network failed'))
    vi.stubGlobal('fetch', fetcher)
    await expect(request('/auth/login', { body: { password: 'FAKE_TEST_PASSWORD' } })).rejects.toMatchObject({ code: 'mutation_network' })
    expect(fetcher).toHaveBeenCalledTimes(1)
  })

  it('aborta ao exceder prazo de mutação e não dispara novamente', async () => {
    vi.useFakeTimers()
    const fetcher = vi.fn((_url: string, init: RequestInit) => new Promise<Response>((_resolve, reject) => {
      init.signal?.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')))
    }))
    vi.stubGlobal('fetch', fetcher)
    const result = expect(request('/auth/login', { body: {}, timeoutMs: 50 })).rejects.toMatchObject({ code: 'mutation_timeout' })
    await vi.advanceTimersByTimeAsync(50)
    await result
    expect(fetcher).toHaveBeenCalledTimes(1)
  })

  it('preserva cancelamento da interface sem tratá-lo como falha do serviço', async () => {
    vi.stubGlobal('fetch', vi.fn((_url: string, init: RequestInit) => new Promise<Response>((_resolve, reject) => {
      init.signal?.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')))
    })))
    const controller = new AbortController()
    const result = expect(request('/sample', { signal: controller.signal })).rejects.toMatchObject({ name: 'AbortError' })
    controller.abort()
    await result
  })

  it('rejeita status autenticado sem usuário ou CSRF', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(Response.json({ configured: true, authenticated: true })))
    await expect(authStatus()).rejects.toMatchObject({ code: 'invalid_response' })
  })
})

describe('onboarding contracts', () => {
  it('teste inclui somente configuração e recebe comprovante de diagnóstico', async () => {
    const diagnostic = { status: 'ok', code: 'model_available', capabilities: config.capabilities, validation_token: 'test-receipt' }
    const fetcher = vi.fn().mockResolvedValue(Response.json(diagnostic))
    vi.stubGlobal('fetch', fetcher)
    setCsrfToken('test-csrf')
    await expect(testModel(config, '')).resolves.toEqual(diagnostic)
    expect(JSON.parse(fetcher.mock.calls[0][1].body)).toEqual({ config })
  })

  it('recusa diagnóstico sem receipt válido, mesmo com status ok', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(Response.json({ status: 'ok', code: 'model_available', capabilities: config.capabilities })))
    setCsrfToken('test-csrf')
    await expect(testModel(config, '')).rejects.toMatchObject({ code: 'invalid_response' })
  })

  it('configuração ou chave alterada invalida o teste correspondente', () => {
    const tested = configurationSignature(config, 'fake-key-1')
    expect(configurationSignature({ ...config, model: 'another-model' }, 'fake-key-1')).not.toBe(tested)
    expect(configurationSignature({ ...config, endpoint: 'http://127.0.0.1:11435' }, 'fake-key-1')).not.toBe(tested)
    expect(configurationSignature({ ...config, kind: 'openai_compatible' }, 'fake-key-1')).not.toBe(tested)
    expect(configurationSignature({ ...config, capabilities: { text: true, tool_calls: true } }, 'fake-key-1')).not.toBe(tested)
    expect(configurationSignature(config, 'fake-key-2')).not.toBe(tested)
  })

  it('criação carrega receipt e chave explicitamente sem devolver chave na lista', async () => {
    const fetcher = vi.fn().mockResolvedValue(Response.json(agent))
    vi.stubGlobal('fetch', fetcher)
    setCsrfToken('test-csrf')
    const result = await createAgent({ name: agent.name, purpose: agent.purpose, instructions: '', config, validation_token: 'test-receipt', api_key: 'fake-key' })
    expect(result).toEqual(agent)
    expect(JSON.parse(fetcher.mock.calls[0][1].body)).toMatchObject({ validation_token: 'test-receipt', api_key: 'fake-key' })
  })

  it('aceita legado sem modelo ou conversa, sem fabricar capacidades', () => {
    expect(isAgent({ ...agent, provider_config: null, conversation_id: null })).toBe(true)
    expect(isAgent({ ...agent, provider_config: {} })).toBe(false)
  })

  it('busca conversa explicitamente e rejeita histórico malformado', async () => {
    const fetcher = vi.fn().mockResolvedValue(Response.json({ conversation_id: 'conversation-1', messages: [{ id: 'message-1', role: 'assistant', content: 'Olá', created_at: '2026-10-05T12:00:00Z' }] }))
    vi.stubGlobal('fetch', fetcher)
    await expect(messages('bee-1', 'conversation-1')).resolves.toMatchObject({ conversation_id: 'conversation-1' })
    expect(fetcher.mock.calls[0][0]).toBe('/api/v1/agents/bee-1/messages?conversation_id=conversation-1')
    fetcher.mockResolvedValue(Response.json({ conversation_id: 'conversation-1', messages: [{ content: 'Only text' }] }))
    await expect(messages('bee-1', 'conversation-1')).rejects.toMatchObject({ code: 'invalid_response' })
  })

  it('rejeita histórico de conversa diferente da solicitada, mesmo quando válido', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(Response.json({
      conversation_id: 'conversation-2',
      messages: [{ id: 'message-2', role: 'assistant', content: 'Outra conversa', created_at: '2026-10-05T12:00:00Z' }],
    })))
    await expect(messages('bee-1', 'conversation-1')).rejects.toMatchObject({ code: 'invalid_response' })
  })
})
