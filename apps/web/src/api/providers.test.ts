import { afterEach, describe, expect, it, vi } from 'vitest'
import { setCsrfToken } from './client'
import { discoverModels, getProviders } from './providers'
import type { Provider } from './providers'
import { createAgent, isModelConfig, prepareModel, testModel } from './onboarding'
import type { ModelConfig } from './onboarding'
import { canSaveModel, connectionConfig, inferProvider, mergeModels, modelOptions } from '../features/model/connection'
import { createRequestGate } from '../features/model/requestGate'
import { preparedSave } from '../features/model/preparedSave'
import { saveConfiguration } from './bee'

const presets: Provider[] = [
  { id: 'openai', name: 'OpenAI', kind: 'openai_compatible', endpoint: 'https://api.openai.com/v1', requires_api_key: true, models: [{ id: 'suggested-model', name: 'Suggested model' }] },
  { id: 'ollama', name: 'Ollama', kind: 'ollama', endpoint: 'http://ollama:11434', requires_api_key: false, models: [{ id: 'suggested-local', name: 'Suggested local model' }] },
  { id: 'custom', name: 'Custom API', kind: 'openai_compatible', endpoint: '', requires_api_key: false, models: [] },
  { id: 'custom_ollama', name: 'Custom Ollama', kind: 'ollama', endpoint: '', requires_api_key: false, models: [] },
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
    { providers: [presets[0], presets[0]] }, { providers: [{ ...presets[0], models: undefined }] },
    { providers: [{ ...presets[0], models: [{ id: 'model', name: null }] }] },
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

describe('suggested models with optional discovery', () => {
  it('makes suggestions selectable before any remote request or API key', () => {
    expect(modelOptions(mergeModels(presets[0].models, []), '')).toEqual(presets[0].models)
  })
  it('adds discovered models without deleting suggestions and gives discovered display names precedence', () => {
    const merged = mergeModels(presets[0].models, [{ id: 'suggested-model', name: 'Provider display name' }, { id: 'new-model', name: 'New model' }])
    expect(merged).toEqual([{ id: 'suggested-model', name: 'Provider display name' }, { id: 'new-model', name: 'New model' }])
    expect(modelOptions(merged, 'manual-model').map((model) => model.id)).toEqual(['manual-model', 'suggested-model', 'new-model'])
    expect(mergeModels(merged, [])).toEqual(merged)
  })
  it('same model selected manually is not duplicated after discovery', () => {
    expect(modelOptions(mergeModels(presets[0].models, [{ id: 'manual-model', name: 'Provider name' }]), 'manual-model').length).toBe(2)
  })
  it('can save any manually entered remote or local model without a discovery or diagnostic receipt', () => {
    expect(canSaveModel(presets[0], { ...config, model: 'manually-entered-model' }, 'DISCARDABLE-KEY', true)).toBe(true)
    expect(canSaveModel(presets[1], { ...config, kind: 'ollama', endpoint: presets[1].endpoint, model: 'manual-local' }, '', false)).toBe(true)
  })
  it('requires named provider credentials and refuses saving a new key without a vault', () => {
    expect(canSaveModel(presets[0], config, '', true)).toBe(false)
    expect(canSaveModel(presets[0], config, 'DISCARDABLE-KEY', false)).toBe(false)
    expect(canSaveModel(presets[0], { ...config, secret_ref: 'vault:explicit-opt-in' }, '', false)).toBe(true)
  })
  it.each(['', ' ', 'm'.repeat(201)])('invalid identifier prevents saving: %s', (model) => {
    expect(canSaveModel(presets[0], { ...config, model }, 'key', true)).toBe(false)
  })
  it.each(['', 'file:///tmp/model', 'https://user:password@example.test', 'not-a-url'])('invalid endpoint prevents saving: %s', (endpoint) => {
    expect(canSaveModel(presets[0], { ...config, endpoint }, 'key', true)).toBe(false)
  })
})

describe('local preparation followed by atomic persistence', () => {
  const agent = { id: 'bee-1', name: 'Test bee', purpose: 'Test purpose', instructions: '', provider_config: config, conversation_id: 'conversation-1', revision: 3, status: 'active', memory_enabled: true }
  it('prepares creation without test/discovery and sends exactly the new receipt and configuration', async () => {
    const fetcher = vi.fn().mockResolvedValueOnce(Response.json({ validation_token: 'local-receipt' })).mockResolvedValueOnce(Response.json(agent))
    vi.stubGlobal('fetch', fetcher); setCsrfToken('test-csrf')
    const signal = new AbortController().signal
    await preparedSave(config, 'DISCARDABLE-KEY', signal, (validation_token) => createAgent({ name: agent.name, purpose: agent.purpose, instructions: '', config, api_key: 'DISCARDABLE-KEY', validation_token }, signal))
    expect(fetcher.mock.calls.map((call) => call[0])).toEqual(['/api/v1/models/prepare', '/api/v1/agents'])
    expect(JSON.parse(fetcher.mock.calls[0][1].body)).toEqual({ config, api_key: 'DISCARDABLE-KEY' })
    expect(JSON.parse(fetcher.mock.calls[1][1].body)).toEqual({ name: agent.name, purpose: agent.purpose, instructions: '', config, api_key: 'DISCARDABLE-KEY', validation_token: 'local-receipt' })
  })
  it('prepares editing with agent-bound credential and preserves revision CAS plus conversation', async () => {
    const fetcher = vi.fn().mockResolvedValueOnce(Response.json({ validation_token: 'local-edit-receipt' })).mockResolvedValueOnce(Response.json(agent))
    vi.stubGlobal('fetch', fetcher); setCsrfToken('test-csrf')
    const reuseConfig = { ...config, secret_ref: 'vault:explicit-opt-in' }
    const signal = new AbortController().signal
    const result = await preparedSave(reuseConfig, '', signal, (token) => saveConfiguration(agent.id, 2, reuseConfig, token, '', signal), agent.id)
    expect(JSON.parse(fetcher.mock.calls[0][1].body)).toEqual({ config: reuseConfig, agent_id: agent.id })
    expect(JSON.parse(fetcher.mock.calls[1][1].body)).toEqual({ config: reuseConfig, expected_revision: 2, validation_token: 'local-edit-receipt' })
    expect(result.conversation_id).toBe(agent.conversation_id)
  })
  it('remote catalog failure does not prevent local preparation and saving', async () => {
    const fetcher = vi.fn().mockResolvedValueOnce(Response.json({ error: { code: 'access_denied' } }, { status: 403 })).mockResolvedValueOnce(Response.json({ validation_token: 'local-receipt' }))
    vi.stubGlobal('fetch', fetcher); setCsrfToken('test-csrf')
    await expect(testModel(config, 'key')).rejects.toMatchObject({ code: 'access_denied' })
    const persist = vi.fn().mockResolvedValue('saved')
    await expect(preparedSave(config, 'key', new AbortController().signal, persist)).resolves.toBe('saved')
    expect(persist).toHaveBeenCalledExactlyOnceWith('local-receipt')
  })
  it('preparation failure never invokes persistence or retries', async () => {
    const fetcher = vi.fn().mockResolvedValue(Response.json({ error: { code: 'invalid_config' } }, { status: 400 }))
    vi.stubGlobal('fetch', fetcher); setCsrfToken('test-csrf')
    const persist = vi.fn()
    await expect(preparedSave(config, '', new AbortController().signal, persist)).rejects.toMatchObject({ code: 'invalid_config' })
    expect(persist).not.toHaveBeenCalled(); expect(fetcher).toHaveBeenCalledTimes(1)
  })
  it('configuration edit or logout during preparation prevents the second request even if transport returns late', async () => {
    const controller = new AbortController()
    const fetcher = vi.fn().mockImplementation(async () => { controller.abort(); return Response.json({ validation_token: 'late-receipt' }) })
    vi.stubGlobal('fetch', fetcher); setCsrfToken('test-csrf')
    const persist = vi.fn()
    await expect(preparedSave(config, '', controller.signal, persist)).rejects.toMatchObject({ name: 'AbortError' })
    expect(persist).not.toHaveBeenCalled(); expect(fetcher).toHaveBeenCalledTimes(1)
  })
  it.each([{}, { validation_token: '' }, { validation_token: 123 }])('invalid preparation receipt is refused before persistence: %j', async (body) => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(Response.json(body))); setCsrfToken('test-csrf')
    await expect(prepareModel(config, '')).rejects.toMatchObject({ code: 'invalid_response' })
  })
})
