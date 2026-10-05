import { afterEach, describe, expect, it, vi } from 'vitest'
import { setCsrfToken } from './client'
import { discoverModels, getProviders } from './providers'
import type { Provider } from './providers'
import { isModelConfig } from './onboarding'
import type { ModelConfig } from './onboarding'
import { connectionConfig, inferProvider, modelOptions } from '../features/model/connection'
import { createRequestGate } from '../features/model/requestGate'

const presets: Provider[] = [
  { id: 'openai', name: 'OpenAI', kind: 'openai_compatible', endpoint: 'https://api.openai.com/v1', requires_api_key: true },
  { id: 'ollama', name: 'Ollama', kind: 'ollama', endpoint: 'http://ollama:11434', requires_api_key: false },
  { id: 'custom', name: 'Custom API', kind: 'openai_compatible', endpoint: '', requires_api_key: false },
  { id: 'custom_ollama', name: 'Custom Ollama', kind: 'ollama', endpoint: '', requires_api_key: false },
]
const config: ModelConfig = { kind: 'openai_compatible', endpoint: 'https://api.openai.com/v1/', model: 'legacy-model', capabilities: { text: true, tool_calls: false }, max_catalog_bytes: 4194304 }

afterEach(() => { vi.unstubAllGlobals(); setCsrfToken() })

describe('provider and model discovery contracts', () => {
  it('fetches authenticated catalog without submitting credentials and filters non-display fields', async () => {
    const fetcher = vi.fn().mockResolvedValue(Response.json({ providers: presets.map((item) => ({ ...item, api_key: 'must-not-retain' })) }))
    vi.stubGlobal('fetch', fetcher)
    await expect(getProviders()).resolves.toEqual(presets)
    expect(fetcher.mock.calls[0][0]).toBe('/api/v1/providers')
    expect(fetcher.mock.calls[0][1]).toMatchObject({ method: 'GET', credentials: 'same-origin', cache: 'no-store' })
    expect(fetcher.mock.calls[0][1].body).toBeUndefined()
  })
  it.each([
    {}, { providers: [] }, { providers: [{ ...presets[0], id: '' }] },
    { providers: [{ ...presets[0], kind: 'unsupported' }] }, { providers: [{ ...presets[0], requires_api_key: 'yes' }] },
    { providers: [presets[0], presets[0]] },
  ])('rejects malformed provider catalog %j', async (data) => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(Response.json(data)))
    await expect(getProviders()).rejects.toMatchObject({ code: 'invalid_response' })
  })
  it('POSTs explicit credential-bound discovery with CSRF without saving the key in returned state', async () => {
    const fetcher = vi.fn().mockResolvedValue(Response.json({ provider_id: 'custom', models: [{ id: 'model-1', name: 'Model one', api_key: 'must-not-retain' }] }))
    vi.stubGlobal('fetch', fetcher); setCsrfToken('test-csrf')
    const body = { provider_id: 'custom', endpoint: 'https://models.example.test/v1', api_key: 'DISCARDABLE-TEST-KEY', agent_id: 'bee-1' }
    await expect(discoverModels(body)).resolves.toEqual([{ id: 'model-1', name: 'Model one' }])
    expect(JSON.parse(fetcher.mock.calls[0][1].body)).toEqual(body)
    expect(fetcher.mock.calls[0][1].headers['X-Bees-CSRF']).toBe('test-csrf')
    expect(fetcher).toHaveBeenCalledTimes(1)
  })
  it('submits saved credential reference only as explicit caller data', async () => {
    const fetcher = vi.fn().mockResolvedValue(Response.json({ provider_id: 'openai', models: [] }))
    vi.stubGlobal('fetch', fetcher); setCsrfToken('test-csrf')
    const body = { provider_id: 'openai', agent_id: 'bee-1', secret_ref: 'vault:test-reference' }
    await expect(discoverModels(body)).resolves.toEqual([])
    expect(JSON.parse(fetcher.mock.calls[0][1].body)).toEqual(body)
  })
  it.each([
    { provider_id: 'other', models: [] }, { provider_id: 'openai', models: {} },
    { provider_id: 'openai', models: [{ id: '', name: 'Empty' }] },
    { provider_id: 'openai', models: [{ id: 'model-1', name: null }] },
    { provider_id: 'openai', models: [{ id: 'a', name: 'A' }, { id: 'a', name: 'B' }] },
  ])('rejects malformed or mismatched discovery %j', async (data) => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(Response.json(data))); setCsrfToken('test-csrf')
    await expect(discoverModels({ provider_id: 'openai' })).rejects.toMatchObject({ code: 'invalid_response' })
  })
  it('cannot discover without session CSRF', async () => {
    const fetcher = vi.fn(); vi.stubGlobal('fetch', fetcher)
    await expect(discoverModels({ provider_id: 'openai' })).rejects.toMatchObject({ code: 'csrf_missing' })
    expect(fetcher).not.toHaveBeenCalled()
  })
  it('does not retry authentication failures or retain provider error details', async () => {
    const fetcher = vi.fn().mockResolvedValue(Response.json({ error: { code: 'authentication_failed', message: 'sensitive upstream text' } }, { status: 401 }))
    vi.stubGlobal('fetch', fetcher); setCsrfToken('test-csrf')
    await expect(discoverModels({ provider_id: 'openai' })).rejects.toMatchObject({ code: 'authentication_failed', message: 'authentication_failed' })
    expect(fetcher).toHaveBeenCalledTimes(1)
  })
})

describe('existing model selection and configuration', () => {
  it('infers preset after slash normalization without rewriting legacy configuration', () => {
    expect(inferProvider(presets, config)?.id).toBe('openai')
    expect(config.endpoint).toBe('https://api.openai.com/v1/')
    expect(config.model).toBe('legacy-model')
  })
  it.each([
    { ...config, endpoint: 'https://independent.example.test/v1', expected: 'custom' },
    { ...config, kind: 'ollama' as const, endpoint: 'http://127.0.0.1:11434', expected: 'custom_ollama' },
  ])('falls back to custom matching driver for unknown endpoints', ({ expected, ...value }) => {
    expect(inferProvider(presets, value)?.id).toBe(expected)
  })
  it('creation has no provider silently selected', () => { expect(inferProvider(presets)).toBeUndefined() })
  it('preserves selected legacy model absent in discovered list, without duplicates', () => {
    const models = [{ id: 'new-model', name: 'New model' }]
    expect(modelOptions(models, 'legacy-model')).toEqual([{ id: 'legacy-model', name: 'legacy-model' }, ...models])
    expect(modelOptions(models, 'new-model')).toEqual(models)
    expect(modelOptions([], '')).toEqual([])
  })
  it('preserves catalog size limit when building edited configuration', () => {
    expect(isModelConfig(config)).toBe(true)
    expect(connectionConfig(config, { kind: config.kind, endpoint: config.endpoint, model: 'new-model', tools: false, reuse: false }).max_catalog_bytes).toBe(4194304)
  })
  it.each([0, -1, 2.5, '4194304', null])('rejects invalid catalog limit %j', (max_catalog_bytes) => {
    expect(isModelConfig({ ...config, max_catalog_bytes })).toBe(false)
  })
})

describe('request selection cancellation', () => {
  it('destination changes and cleanup abort active requests and reject their results', () => {
    const gate = createRequestGate(); const previous = gate.begin()
    expect(gate.current(previous)).toBe(true)
    gate.cancel()
    expect(previous.signal.aborted).toBe(true)
    expect(gate.current(previous)).toBe(false)
  })
  it('late response from replaced discovery cannot replace the active selection', async () => {
    const gate = createRequestGate(); const first = gate.begin()
    let deliver!: (value: string) => void
    const delayed = new Promise<string>((resolve) => { deliver = resolve })
    const second = gate.begin(); let selected = 'new-provider'
    deliver('old-model')
    const result = await delayed
    if (gate.current(first)) selected = result
    expect(selected).toBe('new-provider')
    expect(first.signal.aborted).toBe(true)
    expect(gate.current(second)).toBe(true)
  })
})
