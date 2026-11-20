import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { setCsrfToken } from './client'
import { cancelLocalPull, deleteLocalModel, getLocalModels, getLocalPull, getLocalPullRequest, startLocalPull } from './localModels'

const endpoint = 'http://127.0.0.1:11434'
const pull = { id: 'p1', model: 'qwen3:latest', endpoint, status: 'running', phase: 'downloading', total_bytes: 100, completed_bytes: 40, error: null, started_at: '2026-11-03T00:00:00Z', finished_at: null }
const model = { name: 'qwen3:latest', size_bytes: 100, modified_at: null }
const suggested = { id: 'qwen3:latest', name: 'Qwen', download_bytes: 100, min_memory_gb: 4, tool_calls: true, source: 'https://ollama.com/library/qwen3', note: 'small' }
const status = { endpoint, reachable: true, version: '0.40.6', models: [model], suggested: [suggested], pulls: [pull] }

beforeEach(() => setCsrfToken('test-csrf'))
afterEach(() => { vi.unstubAllGlobals(); setCsrfToken() })
function response(value: unknown) { return vi.stubGlobal('fetch', vi.fn(async () => Response.json(value))) }

it.each([
  { status: 'unexpected' }, { status: ['completed'] }, { phase: 'unexpected' }, { phase: ['success'] }, { id: '' }, { model: null }, { endpoint: null },
  { total_bytes: -1 }, { total_bytes: true }, { total_bytes: Number.MAX_SAFE_INTEGER + 1 },
  { completed_bytes: -1 }, { completed_bytes: 0.5 }, { completed_bytes: true }, { completed_bytes: 101 },
  { error: {} }, { started_at: null }, { finished_at: 1 },
])('rejects malformed progress %j', async (patch) => {
  response({ ...pull, ...patch })
  await expect(getLocalPull(pull)).rejects.toMatchObject({ code: 'invalid_response' })
})

it.each([
  { endpoint: null }, { version: 1 }, { reachable: 'true' }, { models: [null] },
  { models: [{ ...model, name: '' }] }, { models: [{ ...model, size_bytes: -1 }] },
  { models: [{ ...model, modified_at: 1 }] }, { suggested: [null] },
  { suggested: [{ ...suggested, id: null }] }, { suggested: [{ ...suggested, name: '' }] },
  { suggested: [{ ...suggested, download_bytes: true }] }, { suggested: [{ ...suggested, min_memory_gb: -1 }] },
  { suggested: [{ ...suggested, tool_calls: 'true' }] }, { suggested: [{ ...suggested, source: null }] },
  { suggested: [{ ...suggested, note: null }] }, { pulls: [{ ...pull, status: 'unexpected' }] },
])('rejects malformed catalog %j', async (patch) => {
  response({ ...status, ...patch })
  await expect(getLocalModels(endpoint)).rejects.toMatchObject({ code: 'invalid_response' })
})

it('accepts valid catalogs, empty and unavailable snapshots', async () => {
  for (const value of [status, { ...status, models: [], pulls: [] }, { ...status, reachable: false, version: null, models: [] }]) {
    response(value)
    await expect(getLocalModels(endpoint)).resolves.toEqual(value)
  }
})

it.each(['read', 'cancel'] as const)('rejects a foreign download on %s without a follow-up', async (operation) => {
  const fetcher = vi.fn(async () => Response.json({ ...pull, id: 'foreign' }))
  vi.stubGlobal('fetch', fetcher)
  await expect(operation === 'read' ? getLocalPull(pull) : cancelLocalPull(pull)).rejects.toMatchObject({ code: 'invalid_response' })
  expect(fetcher).toHaveBeenCalledTimes(1)
})

it.each([{ model: 'other:latest' }, { endpoint: 'http://127.0.0.1:11435' }])('rejects a divergent start receipt %j', async (patch) => {
  const fetcher = vi.fn(async () => Response.json({ ...pull, ...patch }))
  vi.stubGlobal('fetch', fetcher)
  await expect(startLocalPull(endpoint, 'qwen3', 'request-1')).rejects.toMatchObject({ code: 'invalid_response' })
  expect(fetcher).toHaveBeenCalledTimes(1)
})

it.each([
  { endpoint: 'http://127.0.0.1:11435' },
  { pulls: [{ ...pull, endpoint: 'http://127.0.0.1:11435' }] },
])('rejects a foreign backend in the catalog %j', async (patch) => {
  response({ ...status, ...patch })
  await expect(getLocalModels(endpoint)).rejects.toMatchObject({ code: 'invalid_response' })
})

it.each([{}, { deleted: 'other' }])('rejects a mismatched removal receipt %j', async (value) => {
  const fetcher = vi.fn(async () => Response.json(value))
  vi.stubGlobal('fetch', fetcher)
  await expect(deleteLocalModel(endpoint, 'qwen3')).rejects.toMatchObject({ code: 'invalid_response' })
  expect(fetcher).toHaveBeenCalledTimes(1)
})

it('accepts default endpoint, trailing slash and canonical model aliases', async () => {
  response(status)
  await expect(getLocalModels(undefined)).resolves.toEqual(status)
  await expect(getLocalModels(`${endpoint}/`)).resolves.toEqual(status)
  response(pull)
  await expect(startLocalPull(undefined, 'qwen3', 'request-1')).resolves.toEqual(pull)
  await expect(startLocalPull(`${endpoint}/`, 'qwen3:latest', 'request-2')).resolves.toEqual(pull)
  await expect(getLocalPull(pull)).resolves.toEqual(pull)
  await expect(cancelLocalPull(pull)).resolves.toEqual(pull)
  response({ deleted: 'qwen3:latest' })
  await expect(deleteLocalModel(undefined, 'qwen3')).resolves.toBeUndefined()
})

const operations = [getLocalPull, cancelLocalPull]
it.each(operations)('%s rejects a different model or endpoint even with the confirmed ID', async (operation) => {
  for (const patch of [
    { model: 'other:latest' }, { model: 'qwen3:8b' }, { model: 'library/qwen3:latest' },
    { endpoint: 'http://127.0.0.1:11435' }, { endpoint: 'https://127.0.0.1:11434' },
    { endpoint: `${endpoint}/other` }, { endpoint: `${endpoint}?other=1` },
  ]) {
    const fetcher = vi.fn(async () => Response.json({ ...pull, ...patch, status: 'completed', finished_at: '2026-11-03T00:01:00Z', phase: 'success', completed_bytes: 100 }))
    vi.stubGlobal('fetch', fetcher)
    await expect(operation(pull)).rejects.toMatchObject({ code: 'invalid_response' })
    expect(fetcher).toHaveBeenCalledTimes(1)
  }
})

it.each(operations)('%s accepts canonical aliases and captures identity before awaiting', async (operation) => {
  let resolve!: (response: Response) => void
  const receipt = new Promise<Response>((done) => { resolve = done })
  const fetcher = vi.fn(() => receipt)
  vi.stubGlobal('fetch', fetcher)
  const expected = { id: pull.id, model: 'qwen3', endpoint: `${endpoint}/` }
  const controller = new AbortController()
  const result = operation(expected, controller.signal)
  expected.id = 'foreign'; expected.model = 'other'; expected.endpoint = `${endpoint}/other`
  resolve(Response.json(pull))
  await expect(result).resolves.toEqual(pull)
  expect(fetcher.mock.calls[0]).toMatchObject([
    operation === getLocalPull ? '/api/v1/local-models/pulls/p1' : '/api/v1/local-models/pulls/p1/cancel',
    { method: operation === getLocalPull ? 'GET' : 'POST' },
  ])
  expect(fetcher).toHaveBeenCalledTimes(1)
})

it.each(operations)('%s rejects a receipt matching only a mutated identity', async (operation) => {
  let resolve!: (response: Response) => void
  const receipt = new Promise<Response>((done) => { resolve = done })
  vi.stubGlobal('fetch', vi.fn(() => receipt))
  const expected = { ...pull }
  const result = operation(expected)
  expected.model = 'other:latest'; expected.endpoint = 'http://127.0.0.1:11435'
  resolve(Response.json(expected))
  await expect(result).rejects.toMatchObject({ code: 'invalid_response' })
})

it.each(operations)('%s preserves namespaces and URL normalization', async (operation) => {
  const value = { ...pull, model: 'library/qwen3:latest', endpoint: 'http://localhost:80' }
  response(value)
  await expect(operation({ id: pull.id, model: 'library/qwen3', endpoint: 'http://LOCALHOST/' })).resolves.toEqual(value)
})

const finished = '2026-11-03T00:01:00Z'
const incoherent = [
  { status: 'completed', phase: 'downloading', finished_at: finished },
  { status: 'completed', phase: 'success', finished_at: null },
  { status: 'completed', phase: 'success', finished_at: finished, error: 'pull_failed' },
  { status: 'running', finished_at: finished },
  { status: 'running', error: 'pull_failed' },
  { status: 'cancelled', finished_at: null },
  { status: 'cancelled', finished_at: finished, error: 'pull_failed' },
  { status: 'failed', finished_at: finished, error: null },
  { status: 'failed', finished_at: null, error: 'pull_failed' },
]
it.each((['catalog', 'start', 'read', 'cancel'] as const).flatMap((operation) =>
  incoherent.map((patch) => ({ operation, patch })),
))('$operation rejects contradictory lifecycle $patch without follow-up', async ({ operation, patch }) => {
  const value = { ...pull, ...patch }
  response(operation === 'catalog' ? { ...status, pulls: [value] } : value)
  const result = operation === 'catalog' ? getLocalModels(endpoint)
    : operation === 'start' ? startLocalPull(endpoint, 'qwen3', 'request-1')
      : operation === 'read' ? getLocalPull(pull) : cancelLocalPull(pull)
  await expect(result).rejects.toMatchObject({ code: 'invalid_response' })
  expect(fetch).toHaveBeenCalledTimes(1)
})

it('accepts real backend lifecycle states without equating phase or byte count to completion', async () => {
  for (const patch of [
    { status: 'running', phase: 'starting', total_bytes: 0, completed_bytes: 0 },
    { status: 'running', phase: 'success' },
    { status: 'completed', phase: 'success', finished_at: finished },
    { status: 'completed', phase: 'success', finished_at: finished, total_bytes: 0, completed_bytes: 0 },
    { status: 'cancelled', phase: 'starting', finished_at: finished },
    { status: 'cancelled', phase: 'success', finished_at: finished },
    { status: 'failed', phase: 'success', finished_at: finished, error: 'pull_failed' },
    { status: 'failed', phase: 'starting', finished_at: finished, error: 'backend_unavailable' },
  ]) {
    const value = { ...pull, ...patch }
    response(value)
    await expect(getLocalPull(pull)).resolves.toEqual(value)
    await expect(cancelLocalPull(pull)).resolves.toEqual(value)
    await expect(startLocalPull(endpoint, 'qwen3', 'request-1')).resolves.toEqual(value)
    response({ ...status, pulls: [value] })
    await expect(getLocalModels(endpoint)).resolves.toMatchObject({ pulls: [value] })
  }
})

it('looks up a request with GET, preserving canonical endpoint and model aliases', async () => {
  const fetcher = vi.fn(async () => Response.json({ client_request_id: 'request-1', pull }))
  vi.stubGlobal('fetch', fetcher)
  await expect(getLocalPullRequest(`${endpoint}/`, 'qwen3', 'request-1')).resolves.toEqual(pull)
  expect(fetcher).toHaveBeenCalledTimes(1)
  expect(fetcher.mock.calls[0]).toMatchObject(['/api/v1/local-models/pull-requests/request-1', { method: 'GET' }])
})

it.each([
  {}, { client_request_id: 'foreign', pull }, { client_request_id: 'request-1', pull: null },
  { client_request_id: 'request-1', pull: { ...pull, model: 'foreign' } },
  { client_request_id: 'request-1', pull: { ...pull, endpoint: `${endpoint}/foreign` } },
  { client_request_id: 'request-1', pull: { ...pull, status: 'completed' } },
])('rejects incompatible request lookup %j without another request', async (value) => {
  const fetcher = vi.fn(async () => Response.json(value)); vi.stubGlobal('fetch', fetcher)
  await expect(getLocalPullRequest(endpoint, 'qwen3', 'request-1')).rejects.toMatchObject({ code: 'invalid_response' })
  expect(fetcher).toHaveBeenCalledTimes(1)
})
