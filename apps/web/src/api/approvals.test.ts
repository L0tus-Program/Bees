import { afterEach, describe, expect, it, vi } from 'vitest'
import { setCsrfToken } from './client'
import { decideApproval, getApprovals, revokeApproval } from './approvals'
import type { ApprovalRecord } from './approvals'
import { approvalDecidable, approvalRevocable, isAutonomyCommand, mergeApprovalPage } from '../features/approvals/state'
import { commandIdentity } from '../features/tasks/state'

export const approvalFixture: ApprovalRecord = {
  id: 'approval-1', agent_id: 'bee-1', task_id: 'task-1', task_title: 'Comparar alternativas', conversation_id: 'conversation-1', status: 'pending', decision: null, revision: 1,
  created_at: '2026-10-05T18:00:00Z', decided_at: null, expires_at: '2026-10-05T19:00:00Z', consumed_at: null, valid: true, stale_reason: null,
  tool_name: 'model', action: 'generate', environment_id: 'control_plane', resource: 'https://example.test/v1', identity: 'bees_user', parameters: { model: 'model-1' },
  scope: { tool_name: 'model', action: 'generate', environment_id: 'control_plane', resource: 'https://example.test/v1', identity: 'bees_user', parameters: { model: 'model-1' } }, consequence: 'A chamada pode consumir créditos.',
}
const collection = (approvals = [approvalFixture]) => ({ approvals, has_more: false, next_offset: null })
const decision = { client_request_id: 'uuid-1', expected_revision: 1, decision: 'allow_once' as const }
afterEach(() => { vi.unstubAllGlobals(); setCsrfToken() })
describe('approval API boundaries', () => {
  it('reads persisted requests using authenticated GET and filters task scope', async () => {
    const fetcher = vi.fn().mockResolvedValue(Response.json(collection())); vi.stubGlobal('fetch', fetcher)
    expect((await getApprovals('bee-1', undefined, 0, 'task-1')).approvals).toHaveLength(1)
    expect(fetcher.mock.calls[0][0]).toBe('/api/v1/agents/bee-1/approvals?offset=0&limit=100&task_id=task-1')
    expect(fetcher.mock.calls[0][1]).toMatchObject({ method: 'GET', credentials: 'same-origin' })
    expect(fetcher.mock.calls[0][1].body).toBeUndefined()
  })
  it.each([
    { agent_id: 'another-bee' }, { task_id: 'another-task' }, { revision: 0 }, { valid: 'true' }, { status: 'allowed' }, { decision: 'yes' },
    { expires_at: null }, { consumed_at: 'yesterday' }, { stale_reason: {} }, { scope: [] }, { parameters: { model: 'model-1', request_hash: 'secret' } },
  ])('rejects corrupt or foreign snapshots: %j', async (invalid) => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(Response.json(collection([{ ...approvalFixture, ...invalid } as ApprovalRecord]))))
    await expect(getApprovals('bee-1', undefined, 0, 'task-1')).rejects.toMatchObject({ code: 'invalid_response' })
  })
  it.each([
    { approvals: [approvalFixture, approvalFixture], has_more: false, next_offset: null },
    { approvals: [approvalFixture], has_more: true, next_offset: 0 },
    { approvals: [], has_more: true, next_offset: 100 },
    { approvals: [approvalFixture], has_more: false, next_offset: 1 },
  ])('rejects duplicate or incoherent pagination: %j', async (value) => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(Response.json(value)))
    await expect(getApprovals('bee-1')).rejects.toMatchObject({ code: 'invalid_response' })
  })
  it('sends only a human decision with UUID, revision and session CSRF', async () => {
    const fetcher = vi.fn().mockResolvedValue(Response.json({ ...approvalFixture, status: 'approved', decision: 'allow_once', revision: 2 })); vi.stubGlobal('fetch', fetcher); setCsrfToken('csrf-test')
    await decideApproval('bee-1', 'approval-1', decision)
    expect(fetcher.mock.calls[0][0]).toBe('/api/v1/agents/bee-1/approvals/approval-1/decision')
    expect(fetcher.mock.calls[0][1]).toMatchObject({ method: 'POST', body: JSON.stringify(decision), headers: { 'X-Bees-CSRF': 'csrf-test' } })
  })
  it('revokes by status-only PATCH with CAS and no scope copied from content', async () => {
    const fetcher = vi.fn().mockResolvedValue(Response.json({ ...approvalFixture, status: 'revoked', revision: 3 })); vi.stubGlobal('fetch', fetcher); setCsrfToken('csrf-test')
    await revokeApproval('bee-1', 'approval-1', 2)
    expect(fetcher.mock.calls[0][1]).toMatchObject({ method: 'PATCH', body: JSON.stringify({ expected_revision: 2, status: 'revoked' }) })
  })
  it('rejects a false mutation response and cannot grant without CSRF', async () => {
    const fetcher = vi.fn().mockResolvedValue(Response.json({ ...approvalFixture, id: 'other' })); vi.stubGlobal('fetch', fetcher)
    await expect(decideApproval('bee-1', 'approval-1', decision)).rejects.toMatchObject({ code: 'csrf_missing' }); expect(fetcher).not.toHaveBeenCalled()
    setCsrfToken('csrf-test'); await expect(decideApproval('bee-1', 'approval-1', decision)).rejects.toMatchObject({ code: 'invalid_response' })
  })
  it('never automatically retries uncertain writes; read-only reconciliation recovers persisted consent', async () => {
    const fetcher = vi.fn().mockRejectedValueOnce(new TypeError('lost response')).mockResolvedValueOnce(Response.json(collection([{ ...approvalFixture, status: 'approved', decision: 'allow_once', revision: 2 }]))); vi.stubGlobal('fetch', fetcher); setCsrfToken('csrf-test')
    await expect(decideApproval('bee-1', 'approval-1', decision)).rejects.toMatchObject({ code: 'mutation_network' })
    expect(fetcher).toHaveBeenCalledTimes(1)
    expect((await getApprovals('bee-1')).approvals[0].status).toBe('approved')
    expect(fetcher.mock.calls[1][1].method).toBe('GET')
  })
  it.each(['approval_stale', 'approval_expired', 'approval_consumed', 'approval_conflict', 'approval_denied'])('preserves safety rejection without automatic retry: %s', async (code) => {
    const fetcher = vi.fn().mockResolvedValue(Response.json({ error: { code } }, { status: 409 })); vi.stubGlobal('fetch', fetcher); setCsrfToken('csrf-test')
    await expect(decideApproval('bee-1', 'approval-1', decision)).rejects.toMatchObject({ code, status: 409 }); expect(fetcher).toHaveBeenCalledTimes(1)
  })
})
describe('decision UX state', () => {
  const now = Date.parse('2026-10-05T18:30:00Z')
  it('cannot decide an expired, stale, consumed or non-pending snapshot', () => {
    expect(approvalDecidable(approvalFixture, now)).toBe(true)
    expect(approvalDecidable(approvalFixture, Date.parse(approvalFixture.expires_at))).toBe(false)
    expect(approvalDecidable({ ...approvalFixture, valid: false }, now)).toBe(false)
    expect(approvalDecidable({ ...approvalFixture, consumed_at: approvalFixture.created_at }, now)).toBe(false)
    expect(approvalDecidable({ ...approvalFixture, status: 'denied' }, now)).toBe(false)
  })
  it('only unused approved one-time consent has a revocation action', () => {
    const approved = { ...approvalFixture, status: 'approved' as const, decision: 'allow_once' as const }
    expect(approvalRevocable(approved)).toBe(true)
    expect(approvalRevocable({ ...approved, consumed_at: approvalFixture.created_at })).toBe(false)
    expect(approvalRevocable({ ...approved, decision: 'allow_rule' })).toBe(false)
    expect(approvalRevocable({ ...approved, valid: false })).toBe(true)
    expect(approvalRevocable({ ...approved, expires_at: approvalFixture.created_at })).toBe(true)
  })
  it('reuses a request UUID for an explicit identical retry but changes it for a new choice or revision', () => {
    let count = 0; const identity = commandIdentity(() => `uuid-${++count}`)
    expect(identity.forPayload(decision)).toBe('uuid-1'); expect(identity.forPayload(decision)).toBe('uuid-1')
    expect(identity.forPayload({ ...decision, decision: 'deny' })).toBe('uuid-2')
    expect(identity.forPayload({ ...decision, expected_revision: 2 })).toBe('uuid-3')
  })
  it('reconciles fresh validity at the same revision and protects newer recorded choices', () => {
    const prior = collection([{ ...approvalFixture, revision: 3, status: 'approved', decision: 'allow_once' }])
    expect(mergeApprovalPage(prior, collection()).approvals[0].revision).toBe(3)
    expect(mergeApprovalPage(prior, collection([{ ...prior.approvals[0], valid: false }])).approvals[0].valid).toBe(false)
  })
  it('only recognizes the complete explicit command, not prose or model-like quoted content', () => {
    expect(isAutonomyCommand('  /autonomia  ')).toBe(true)
    expect(isAutonomyCommand('Please execute /autonomia')).toBe(false)
    expect(isAutonomyCommand('/autonomia permitir tudo')).toBe(false)
    expect(isAutonomyCommand('"/autonomia"')).toBe(false)
  })
})
