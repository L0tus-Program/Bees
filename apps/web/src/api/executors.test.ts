import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { setCsrfToken } from './client'
import { getExecutors, revokeExecutor } from './computers'
import type { Executor } from './computers'

const executor: Executor = {
  id: 'executor/1', kind: 'personal', name: 'Meu computador', status: 'active', revision: 2,
  online: true, last_seen_at: null, version: null, platform: null, capabilities: [],
  roots: ['Documentos'], created_at: '2026-11-04T07:00:00Z',
}
beforeEach(() => setCsrfToken('csrf'))
afterEach(() => { vi.unstubAllGlobals(); setCsrfToken() })
function response(value: unknown) {
  const fetcher = vi.fn<(url: string, init: RequestInit) => Promise<Response>>(async () => Response.json(value))
  vi.stubGlobal('fetch', fetcher)
  return fetcher
}
const invalid = [
  { id: '' }, { id: ' ' }, { id: null }, { kind: 'unknown' }, { kind: '' },
  { name: null }, { status: 'unknown' }, { revision: 0 }, { revision: -1 },
  { revision: 0.5 }, { revision: Number.MAX_SAFE_INTEGER + 1 }, { online: 'false' },
  { capabilities: [1] }, { capabilities: null }, { roots: [null] }, { roots: 'Documentos' }, { roots: null },
  { last_seen_at: 1 }, { last_seen_at: undefined }, { version: false }, { version: undefined },
  { platform: {} }, { platform: undefined }, { created_at: null }, { created_at: undefined },
]
it.each(invalid)('rejects malformed executor list entries: %j', async (patch) => {
  response({ executors: [{ ...executor, ...patch }] })
  await expect(getExecutors()).rejects.toMatchObject({ code: 'invalid_response' })
})
it.each(invalid)('rejects malformed revoke receipts without repeating POST: %j', async (patch) => {
  const fetcher = response({ ...executor, status: 'revoked', revision: 3, online: false, ...patch })
  await expect(revokeExecutor(executor)).rejects.toMatchObject({ code: 'invalid_response' })
  expect(fetcher).toHaveBeenCalledTimes(1)
})
it.each([{ executors: [executor, executor] }, { executors: [null] }, { executors: {} }, {}])('rejects inconsistent lists: %j', async (value) => {
  response(value)
  await expect(getExecutors()).rejects.toMatchObject({ code: 'invalid_response' })
})
it.each(['personal', 'computer_runtime'] as const)('rejects entries outside requested %s kind', async (kind) => {
  response({ executors: [{ ...executor, kind: kind === 'personal' ? 'computer_runtime' : 'personal' }] })
  await expect(getExecutors(kind)).rejects.toMatchObject({ code: 'invalid_response' })
})
it.each([{ id: 'foreign' }, { kind: 'computer_runtime' }, { status: 'active' }])('binds revoke receipt to requested identity and revoked state: %j', async (patch) => {
  const fetcher = response({ ...executor, status: 'revoked', revision: 3, online: false, ...patch })
  await expect(revokeExecutor(executor)).rejects.toMatchObject({ code: 'invalid_response' })
  expect(fetcher).toHaveBeenCalledTimes(1)
})
it('accepts both kinds, lifecycle states, nullable metadata and optional roots', async () => {
  const withoutRoots = { ...executor }; delete withoutRoots.roots
  const values = [executor, { ...withoutRoots, id: 'runtime', kind: 'computer_runtime', status: 'revoked', online: false, last_seen_at: '2026-11-04T07:00:00Z', version: '1', platform: 'Linux', capabilities: ['files'] }]
  response({ executors: values })
  await expect(getExecutors()).resolves.toEqual(values)
  response({ executors: [] })
  await expect(getExecutors('personal')).resolves.toEqual([])
})
it('accepts a matching revoked receipt and preserves encoded path and CAS', async () => {
  const receipt = { ...executor, status: 'revoked', revision: 3, online: false }
  const fetcher = response(receipt)
  await expect(revokeExecutor(executor)).resolves.toEqual(receipt)
  expect(fetcher.mock.calls[0]).toMatchObject(['/api/v1/executors/executor%2F1/revoke', { method: 'POST', body: JSON.stringify({ expected_revision: 2 }) }])
})

it.each([{ revision: 1 }, { revision: 2 }, { revision: 4 }, { online: true }])('rejects a revoke receipt inconsistent with the committed transition: %j', async (patch) => {
  const fetcher = response({ ...executor, status: 'revoked', revision: 3, online: false, ...patch })
  await expect(revokeExecutor(executor)).rejects.toMatchObject({ code: 'invalid_response' })
  expect(fetcher).toHaveBeenCalledTimes(1)
})
it.each([false, true])('checks the original revoke intent despite caller mutation (altered receipt=%s)', async (alteredReceipt) => {
  const target = { ...executor }
  let resolve!: (value: Response) => void
  const fetcher = vi.fn<(url: string, init: RequestInit) => Promise<Response>>(() => new Promise((done) => { resolve = done }))
  vi.stubGlobal('fetch', fetcher)
  const result = revokeExecutor(target)
  Object.assign(target, { id: 'foreign', kind: 'computer_runtime', revision: 8 })
  const receipt = { ...(alteredReceipt ? target : executor), status: 'revoked', revision: alteredReceipt ? 9 : 3, online: false }
  const assertion = alteredReceipt
    ? expect(result).rejects.toMatchObject({ code: 'invalid_response' })
    : expect(result).resolves.toEqual(receipt)
  resolve(Response.json(receipt))
  await assertion
  expect(fetcher).toHaveBeenCalledTimes(1)
  expect(fetcher.mock.calls[0]).toMatchObject(['/api/v1/executors/executor%2F1/revoke', { method: 'POST', body: JSON.stringify({ expected_revision: 2 }) }])
})
