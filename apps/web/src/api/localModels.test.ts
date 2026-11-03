import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { setCsrfToken } from './client'
import { cancelLocalPull, deleteLocalModel, getLocalModels, getLocalPull, startLocalPull } from './localModels'

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
  await expect(getLocalPull('p1')).rejects.toMatchObject({ code: 'invalid_response' })
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
  await expect(operation === 'read' ? getLocalPull('p1') : cancelLocalPull('p1')).rejects.toMatchObject({ code: 'invalid_response' })
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
  await expect(getLocalPull('p1')).resolves.toEqual(pull)
  await expect(cancelLocalPull('p1')).resolves.toEqual(pull)
  response({ deleted: 'qwen3:latest' })
  await expect(deleteLocalModel(undefined, 'qwen3')).resolves.toBeUndefined()
})
