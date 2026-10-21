import { afterEach, expect, it, vi } from 'vitest'
import { checkedRecovery, getRecoveryInventory } from './recovery'
import { setCsrfToken } from './client'

const agent = '10000000-0000-4000-8000-000000000001', task = '20000000-0000-4000-8000-000000000001'
const empty = { prepared: 0, awaiting_approval: 0, ready: 0, dispatch_started: 0, confirmed: 0, failed_no_effect: 0, outcome_unknown: 0, cancelled: 0, unknown_acknowledged: 0, unknown_unacknowledged: 0, total: 0 }
const fixture = () => ({ agent_id: agent, task_id: task, task_revision: 2, task_status: 'paused', observed_at: '2026-10-21T19:00:00Z', scope: 'all_canonical_task_history', executor_evidence: 'not_inspected', runs_total: 1003, actions: { ...empty, outcome_unknown: 2, unknown_acknowledged: 1, unknown_unacknowledged: 1, confirmed: 102, total: 104 }, model_calls: { ...empty, prepared: 1002, total: 1002 } })
afterEach(() => { vi.unstubAllGlobals(); setCsrfToken() })
it('accepts all-history counts and retains acknowledged unknown as unknown', () => {
  const result = checkedRecovery(fixture(), agent, task)
  expect(result.actions.outcome_unknown).toBe(2); expect(result.actions.confirmed).toBe(102); expect(result.runs_total).toBe(1003)
})
it('rejects false totals, ack-as-confirmation, negative/unsafe counters and unexpected count fields', () => {
  for (const patch of [{ total: 1 }, { unknown_acknowledged: 0 }, { confirmed: -1 }, { confirmed: 0.5 }, { confirmed: true }, { confirmed: Number.MAX_SAFE_INTEGER + 1 }, { confirmed: undefined }, { secret: 'PRIVATE' }]) expect(() => checkedRecovery({ ...fixture(), actions: { ...fixture().actions, ...patch } }, agent, task)).toThrow('invalid_response')
})
it('rejects wrong identities, fake inspection, scope, timestamps, status, revisions and extra fields', () => {
  for (const patch of [{ agent_id: task }, { task_id: agent }, { agent_id: '00000000-0000-0000-0000-000000000000' }, { executor_evidence: 'verified' }, { scope: 'latest_page' }, { observed_at: '2026-10-21' }, { task_status: 'ready' }, { task_revision: 0 }, { runs_total: -1 }, { secret: 'PRIVATE' }]) expect(() => checkedRecovery({ ...fixture(), ...patch }, agent, task)).toThrow('invalid_response')
  expect(() => checkedRecovery({ ...fixture(), model_calls: { ...empty, awaiting_approval: 1, total: 1 } }, agent, task)).toThrow('invalid_response')
})
it('fetches only the scoped authenticated GET, with no command, CSRF or retry', async () => {
  const fetcher = vi.fn().mockResolvedValue(Response.json(fixture())); vi.stubGlobal('fetch', fetcher)
  await getRecoveryInventory(agent, task)
  expect(fetcher).toHaveBeenCalledOnce(); expect(fetcher.mock.calls[0][0]).toBe(`/api/v1/agents/${agent}/tasks/${task}/recovery`)
  expect(fetcher.mock.calls[0][1]).toMatchObject({ method: 'GET', credentials: 'same-origin', cache: 'no-store', redirect: 'error' }); expect(fetcher.mock.calls[0][1].body).toBeUndefined()
})
it('rejects paths before HTTP and does not repeat a failed query', async () => {
  const fetcher = vi.fn().mockRejectedValue(new Error('PRIVATE')); vi.stubGlobal('fetch', fetcher)
  await expect(getRecoveryInventory('../private', task)).rejects.toThrow('invalid_request'); expect(fetcher).not.toHaveBeenCalled()
  await expect(getRecoveryInventory(agent, task)).rejects.toThrow('network_error'); expect(fetcher).toHaveBeenCalledOnce()
})
it('does not expose raw unauthorized response or silently return empty inventory', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(Response.json({ error: { code: 'session_required', message: 'PRIVATE' } }, { status: 401 })))
  await expect(getRecoveryInventory(agent, task)).rejects.toMatchObject({ code: 'session_required', status: 401 })
})
