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

it.each([
  { ...grant, revision: 1 }, { ...grant, revision: 3 },
  { ...grant, enabled: false },
  ...['write', 'shell', 'browser'].map((key) => ({ ...grant, scope: { ...grant.scope, [key]: !grant.scope[key as keyof typeof grant.scope] } })),
  ...[[], ['Outra'], ['Documentos', 'Outra'], ['Documentos', 'Documentos']].map((roots) => ({ ...grant, scope: { ...grant.scope, roots } })),
])('rejects a well-formed receipt that differs from the submitted decision: %j', async (receipt) => {
  const fetcher = response(receipt)
  await expect(setGrant('executor-1', grant.agent_id, input)).rejects.toMatchObject({ code: 'invalid_response' })
  expect(fetcher).toHaveBeenCalledTimes(1)
})
it('requires initial revision one when creating a grant', async () => {
  const fetcher = response(grant)
  await expect(setGrant('executor-1', grant.agent_id, { ...input, expected_revision: 0 })).rejects.toMatchObject({ code: 'invalid_response' })
  expect(fetcher).toHaveBeenCalledTimes(1)
})
it('rejects reordered roots because the backend preserves the submitted array', async () => {
  response({ ...grant, scope: { ...grant.scope, roots: ['Downloads', 'Documentos'] } })
  await expect(setGrant('executor-1', grant.agent_id, { ...input, roots: ['Documentos', 'Downloads'] })).rejects.toMatchObject({ code: 'invalid_response' })
})
it.each([true, false])('accepts an exact newly created grant (enabled=%s)', async (enabled) => {
  const scope = { roots: [], write: false, shell: false, browser: false }
  const receipt = { ...grant, revision: 1, enabled, scope }
  response(receipt)
  await expect(setGrant('executor-1', grant.agent_id, { expected_revision: 0, enabled, ...scope })).resolves.toEqual(receipt)
})
it('checks the receipt against the decision sent even if the caller mutates its input in flight', async () => {
  let resolve!: (value: Response) => void
  const pending = new Promise<Response>((done) => { resolve = done })
  const fetcher = vi.fn(async () => pending)
  vi.stubGlobal('fetch', fetcher)
  const body = { ...input, roots: [...input.roots] }
  const saved = setGrant('executor-1', grant.agent_id, body)
  body.enabled = false; body.roots.push('Outra'); body.expected_revision = 9
  resolve(Response.json(grant))
  await expect(saved).resolves.toEqual(grant)
  expect(fetcher).toHaveBeenCalledTimes(1)
})
