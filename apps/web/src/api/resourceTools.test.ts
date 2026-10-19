import { webcrypto } from 'node:crypto'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { checkedResourceApproval, checkedResourceIntent, getResourceApproval, getResourceApprovals, resourceIntent, sendResourceDecision } from './resourceTools'
import { setCsrfToken } from './client'
import { resourceApproval as source, resourceIds as ids, resourceReceipt } from '../features/approvals/resourceFixtures'

beforeEach(() => { vi.stubGlobal('crypto', webcrypto); setCsrfToken('csrf') })
afterEach(() => { vi.unstubAllGlobals(); setCsrfToken() })
describe('resource approval contracts', () => {
  it.each([
    { agent_id: ids.task }, { task_id: ids.agent }, { id: '00000000-0000-0000-0000-000000000000' }, { authority: 'model.generate' },
    { target: { ...source.target, adapter: 'shell' } }, { target: { ...source.target, agent_id: ids.task } },
    { scope: { ...source.scope, action: 'generate' } }, { scope: { ...source.scope, parameters: { ...source.scope.parameters, command: 'exec' } } },
    { scope: { ...source.scope, parameters: { ...source.scope.parameters, grant_revision: 0 } } }, { input_bytes: 8193 },
    { consumed_at: source.created_at }, { effect_status: 'confirmed' }, { valid: false },
  ])('refuses foreign or inconsistent authority %#', (change) => {
    expect(() => checkedResourceApproval({ ...source, ...change }, ids.agent, ids.task)).toThrow('invalid_response')
  })
  it('fetches the private preview only from the explicit detail, with a verified content hash', async () => {
    const fetcher = vi.fn().mockResolvedValueOnce(Response.json({ approvals: [{ ...source, input_preview: null }], has_more: false })).mockResolvedValueOnce(Response.json(source))
    vi.stubGlobal('fetch', fetcher)
    expect((await getResourceApprovals(ids.agent, ids.task)).approvals[0].input_preview).toBeNull()
    expect((await getResourceApproval(ids.agent, ids.task, ids.approval)).input_preview).toBe(source.input_preview)
    expect(fetcher.mock.calls.every(([, options]) => options.method === 'GET')).toBe(true)
    fetcher.mockResolvedValueOnce(Response.json({ ...source, input_preview: source.input_preview!.replace('Olá', 'Olé') }))
    await expect(getResourceApproval(ids.agent, ids.task, ids.approval)).rejects.toMatchObject({ code: 'invalid_response' })
  })
  it('rejects a private preview in a listing', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(Response.json({ approvals: [source], has_more: false })))
    await expect(getResourceApprovals(ids.agent, ids.task)).rejects.toMatchObject({ code: 'invalid_response' })
  })
  it('sends the exact human request and accepts its receipt after a subsequent revocation', async () => {
    const intent = resourceIntent(source, 'allow_once')
    const revoked = { ...source, status: 'revoked' as const, decision: 'allow_once' as const, revision: 3, valid: false, stale_reason: 'approval_revoked' }
    const fetcher = vi.fn().mockResolvedValueOnce(Response.json(revoked)).mockResolvedValueOnce(Response.json(resourceReceipt(intent, revoked)))
    vi.stubGlobal('fetch', fetcher)
    expect((await sendResourceDecision(intent)).status).toBe('revoked')
    expect(fetcher.mock.calls[1][1]).toMatchObject({ method: 'POST', body: JSON.stringify(intent.body), headers: { 'X-Bees-CSRF': 'csrf' } })
    expect(fetcher.mock.calls[1][1].body).not.toContain('parameters')
  })
  it.each(['uuid', 'hash', 'revision', 'confirmed'])('does not accept a false HTTP 200 receipt: %s', async (kind) => {
    const intent = resourceIntent(source, 'allow_once'); const result = resourceReceipt(intent)
    if (kind === 'uuid') result.receipt.client_request_id = ids.task
    if (kind === 'hash') result.receipt.request_hash = '0'.repeat(64)
    if (kind === 'revision') result.receipt.expected_revision = 2
    if (kind === 'confirmed') result.receipt.confirmed = false
    vi.stubGlobal('fetch', vi.fn().mockResolvedValueOnce(Response.json(source)).mockResolvedValueOnce(Response.json(result)))
    await expect(sendResourceDecision(intent)).rejects.toMatchObject({ code: 'invalid_response' })
  })
  it('never sends an intent with an arbitrary path, authority, or changed resource', async () => {
    const intent = resourceIntent(source, 'allow_once')
    expect(() => checkedResourceIntent({ ...intent, path: '/tools/execute' }, ids.agent, ids.task, ids.approval)).toThrow('invalid_request')
    expect(() => checkedResourceIntent({ ...intent, body: { ...intent.body, authority: 'allow' } }, ids.agent, ids.task, ids.approval)).toThrow('invalid_request')
    const fetcher = vi.fn().mockResolvedValue(Response.json({ ...source, scope: { ...source.scope, parameters: { ...source.scope.parameters, grant_revision: 2 } } })); vi.stubGlobal('fetch', fetcher)
    await expect(sendResourceDecision(intent)).rejects.toMatchObject({ code: 'invalid_request' })
    expect(fetcher).toHaveBeenCalledTimes(1)
  })
})
