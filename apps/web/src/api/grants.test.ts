import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { setCsrfToken } from './client'
import { getGrants, setGrant } from './computers'

const grant = { id: 'grant-1', agent_id: 'agent-1', enabled: true, revision: 2, scope: { roots: ['Documentos'], write: false, shell: false, browser: true } }
const input = { expected_revision: 1, enabled: grant.enabled, ...grant.scope }
beforeEach(() => setCsrfToken('test-csrf'))
afterEach(() => { vi.unstubAllGlobals(); setCsrfToken() })
function response(value: unknown) {
  const fetcher = vi.fn(async () => Response.json(value))
  vi.stubGlobal('fetch', fetcher)
  return fetcher
}

const invalid = [
  null, {}, { ...grant, id: '' }, { ...grant, id: ' ' }, { ...grant, id: 1 },
  { ...grant, agent_id: '' }, { ...grant, agent_id: null },
  { ...grant, enabled: 'false' }, { ...grant, enabled: undefined },
  ...[0, -1, 0.5, true, Number.MAX_SAFE_INTEGER + 1, undefined].map((revision) => ({ ...grant, revision })),
  ...[null, {}, [], { ...grant.scope, roots: null }, { ...grant.scope, roots: 'Documentos' },
    { ...grant.scope, roots: [1] }, { ...grant.scope, write: 'false' },
    { ...grant.scope, shell: undefined }, { ...grant.scope, browser: 1 },
  ].map((scope) => ({ ...grant, scope })),
]
it.each(invalid)('rejects malformed personal grants in reads: %j', async (value) => {
  response({ grants: [value] })
  await expect(getGrants('executor-1')).rejects.toMatchObject({ code: 'invalid_response' })
})
it.each(invalid)('rejects malformed personal grants in receipts without retry: %j', async (value) => {
  const fetcher = response(value)
  await expect(setGrant('executor-1', grant.agent_id, input)).rejects.toMatchObject({ code: 'invalid_response' })
  expect(fetcher).toHaveBeenCalledTimes(1)
})
it.each([
  { grants: [grant, grant] },
  { grants: [grant, { ...grant, id: 'grant-2' }] },
  { grants: [grant, { ...grant, agent_id: 'agent-2' }] },
  { grants: null }, { grants: {} }, {}, null,
])('rejects ambiguous or malformed grant snapshots: %j', async (value) => {
  response(value)
  await expect(getGrants('executor-1')).rejects.toMatchObject({ code: 'invalid_response' })
})
it('rejects a receipt for a different agent without retrying the PUT', async () => {
  const fetcher = response({ ...grant, agent_id: 'another-agent' })
  await expect(setGrant('executor-1', grant.agent_id, input)).rejects.toMatchObject({ code: 'invalid_response' })
  expect(fetcher).toHaveBeenCalledTimes(1)
})
it('accepts empty and multi-agent snapshots including disabled grants with empty scopes', async () => {
  response({ grants: [] })
  await expect(getGrants('executor-1')).resolves.toEqual([])
  const grants = [grant, { ...grant, id: 'grant-2', agent_id: 'agent-2', enabled: false, scope: { roots: [], write: false, shell: false, browser: false } }]
  response({ grants })
  await expect(getGrants('executor-1')).resolves.toEqual(grants)
})
it('preserves the authenticated endpoint, CAS and explicit scope on a valid PUT', async () => {
  const fetcher = response(grant)
  await expect(setGrant('executor/1', 'agent-1', input)).resolves.toEqual(grant)
  const [url, init] = fetcher.mock.calls[0] as unknown as [string, RequestInit]
  expect(url).toBe('/api/v1/executors/executor%2F1/grants/agent-1')
  expect(init.method).toBe('PUT')
  expect(JSON.parse(String(init.body))).toEqual(input)
  expect(new Headers(init.headers).get('X-Bees-CSRF')).toBe('test-csrf')
  expect(fetcher).toHaveBeenCalledTimes(1)
})
