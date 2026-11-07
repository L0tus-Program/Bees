import { createHash } from 'node:crypto'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { decideAgentApproval, getAgentApproval, listAgentApprovals, revokeAgentApproval } from './agent'
import type { AgentApproval } from './agent'
import { setCsrfToken } from './client'

const pending: AgentApproval = {
  id: 'approval', agent_id: 'bee', task_id: 'task', run_id: 'run', status: 'pending', decision: null,
  revision: 1, created_at: '2026-10-24T10:00:00Z', expires_at: '2026-10-24T11:00:00Z', consumed_at: null,
  valid: true, stale_reason: null, tool_name: 'artifacts.publish', effect: 'write', summary: 'Publicar', task_title: 'Tarefa',
  target: { kind: 'control_plane', environment_id: 'control_plane' }, arguments_preview: null,
}
const requestId = '2a1e0b22-011c-4bb2-bc05-a40f3f8e63a6'
const decisionInput = { client_request_id: requestId, decision: 'allow_once', expected_revision: 1, reason: '', rule_name: null }
const revokeInput = { client_request_id: requestId, expected_revision: 2, status: 'revoked' }
const approved: AgentApproval = { ...pending, status: 'approved', decision: 'allow_once', revision: 2 }
const revoked: AgentApproval = { ...approved, status: 'revoked', revision: 3 }
function receipt(operation: 'decide' | 'revoke') {
  const input = operation === 'decide' ? decisionInput : revokeInput
  // Ordem canônica equivalente à serialização dos modelos Pydantic da API.
  const request_hash = createHash('sha256').update(JSON.stringify({ input, operation })).digest('hex')
  return { approval_id: pending.id, client_request_id: requestId, expected_revision: input.expected_revision, operation, request_hash, confirmed: true }
}

beforeEach(() => setCsrfToken('csrf'))
afterEach(() => { vi.unstubAllGlobals(); setCsrfToken() })

it.each([approved, revoked])('confirms the original decision with the current $status projection', async (approval) => {
  const fetcher = vi.fn<typeof fetch>().mockResolvedValue(Response.json({ approval, receipt: receipt('decide') }))
  vi.stubGlobal('fetch', fetcher)
  await expect(decideAgentApproval('bee', pending, 'allow_once', requestId)).resolves.toEqual(approval)
  expect(fetcher.mock.calls[0][1]!.method).toBe('POST')
  expect(JSON.parse(fetcher.mock.calls[0][1]!.body as string)).toEqual(decisionInput)
})

it.each([
  { approval_id: 'other' }, { client_request_id: 'other' }, { operation: 'revoke' },
  { expected_revision: 2 }, { request_hash: '0'.repeat(64) }, { confirmed: false },
])('rejects a decision receipt with a different %j', async (mismatch) => {
  vi.stubGlobal('fetch', vi.fn(async () => Response.json({ approval: approved, receipt: { ...receipt('decide'), ...mismatch } })))
  await expect(decideAgentApproval('bee', pending, 'allow_once', requestId)).rejects.toThrow('invalid_response')
})

it.each([
  { id: 'other' }, { agent_id: 'other' }, { task_id: 'other' }, { run_id: 'other' },
  { revision: 1 }, { status: 'pending' }, { decision: 'allow_tool' },
])('rejects a decision projection with a different %j', async (mismatch) => {
  vi.stubGlobal('fetch', vi.fn(async () => Response.json({ approval: { ...approved, ...mismatch }, receipt: receipt('decide') })))
  await expect(decideAgentApproval('bee', pending, 'allow_once', requestId)).rejects.toThrow('invalid_response')
})

it('requires the revocation receipt and a revoked projection', async () => {
  const fetcher = vi.fn()
    .mockResolvedValueOnce(Response.json({ approval: revoked }))
    .mockResolvedValueOnce(Response.json({ approval: { ...revoked, status: 'approved' }, receipt: receipt('revoke') }))
    .mockResolvedValueOnce(Response.json({ approval: revoked, receipt: receipt('revoke') }))
  vi.stubGlobal('fetch', fetcher)
  await expect(revokeAgentApproval('bee', approved, requestId)).rejects.toThrow('invalid_response')
  await expect(revokeAgentApproval('bee', approved, requestId)).rejects.toThrow('invalid_response')
  await expect(revokeAgentApproval('bee', approved, requestId)).resolves.toEqual(revoked)
  expect(fetcher.mock.calls[2][1].method).toBe('PATCH')
  expect(JSON.parse(fetcher.mock.calls[2][1].body)).toEqual(revokeInput)
})

it.each([[{ ...pending, agent_id: 'other' }], [{ ...pending, task_id: 'other' }], [approved], [pending, pending]])(
  'rejects a list that does not match the requested context: %j', async (...items) => {
    vi.stubGlobal('fetch', vi.fn(async () => Response.json({ approvals: items, has_more: false })))
    await expect(listAgentApprovals('bee', { taskId: 'task', status: 'pending' })).rejects.toThrow('invalid_response')
  },
)

it.each([{ id: 'other' }, { agent_id: 'other' }, { task_id: 'other' }, { run_id: 'other' }, { expires_at: 'invalid' }])('rejects invalid approval detail %j', async (mismatch) => {
  vi.stubGlobal('fetch', vi.fn(async () => Response.json({ ...pending, ...mismatch })))
  await expect(getAgentApproval('bee', pending)).rejects.toThrow('invalid_response')
})
