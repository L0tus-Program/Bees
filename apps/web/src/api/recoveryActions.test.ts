import { afterEach, expect, it, vi } from 'vitest'
import { checkedRecoveryActions, getRecoveryActions } from './recovery'
const agent = '10000000-0000-4000-8000-000000000001', task = '20000000-0000-4000-8000-000000000001', run = '30000000-0000-4000-8000-000000000001'
const id = (n: number) => `40000000-0000-4000-8000-${String(n).padStart(12, '0')}`
const item = (n = 1) => ({ id: id(n), run_id: run, revision: 2, tool_name: 'tool.intent', status: 'outcome_unknown', unknown_acknowledged: true, created_at: '2026-10-21T19:00:00Z', updated_at: '2026-10-21T19:01:00Z', executor_command: null, executor_code: null })
const page = () => ({ agent_id: agent, task_id: task, task_revision: 2, observed_at: '2026-10-21T19:02:00Z', scope: 'all_canonical_task_actions', executor_evidence: 'not_inspected', items: [item()], has_more: false, next_cursor: null })
afterEach(() => vi.unstubAllGlobals())
it('accepts only bounded closed scoped pages and exact final cursors', () => {
  expect(checkedRecoveryActions(page(), agent, task).items[0].unknown_acknowledged).toBe(true)
  expect(checkedRecoveryActions({ ...page(), items: [], has_more: false }, agent, task).items).toEqual([])
  expect(checkedRecoveryActions({ ...page(), items: [item(), item(2)], has_more: true, next_cursor: id(2) }, agent, task, 2).next_cursor).toBe(id(2))
})
it('rejects wrong scope, fake inspection, unbounded data and cursor inconsistencies', () => {
  for (const patch of [{ agent_id: task }, { task_id: agent }, { scope: 'latest_page' }, { executor_evidence: 'verified' }, { task_revision: 0 }, { observed_at: '2026-10-21' }, { secret: 'PRIVATE' }, { has_more: true }, { has_more: true, next_cursor: id(1) }, { next_cursor: id(1) }, { has_more: 'true' }, { items: Array.from({ length: 51 }, (_, n) => item(n + 1)) }, { items: [item(), item()] }]) expect(() => checkedRecoveryActions({ ...page(), ...patch }, agent, task)).toThrow('invalid_response')
})
it('rejects malformed item identity, status, dates, acknowledgement and private extra fields', () => {
  for (const patch of [{ id: '../path' }, { run_id: null }, { revision: true }, { revision: 0 }, { tool_name: '' }, { tool_name: 'x'.repeat(201) }, { status: 'ok' }, { unknown_acknowledged: 'true' }, { created_at: 'today' }, { updated_at: null }, { parameters: { private: 'PRIVATE' } }]) expect(() => checkedRecoveryActions({ ...page(), items: [{ ...item(), ...patch }] }, agent, task)).toThrow('invalid_response')
})
it('only fetches the same human-scoped GET and sends cursor as query, never command body', async () => {
  const fetcher = vi.fn().mockResolvedValue(Response.json(page())); vi.stubGlobal('fetch', fetcher); await getRecoveryActions(agent, task, id(9))
  expect(fetcher).toHaveBeenCalledOnce(); const [url, options] = fetcher.mock.calls[0]; expect(url).toBe(`/api/v1/agents/${agent}/tasks/${task}/recovery/actions?limit=50&after=${id(9)}`)
  expect(options).toMatchObject({ method: 'GET', credentials: 'same-origin', cache: 'no-store', redirect: 'error' }); expect(options.body).toBeUndefined()
})
it('rejects path-like cursors before HTTP and preserves404 instead of showing empty page', async () => {
  const fetcher = vi.fn().mockResolvedValue(Response.json({ error: { code: 'not_found' } }, { status: 404 })); vi.stubGlobal('fetch', fetcher)
  await expect(getRecoveryActions(agent, task, '../other')).rejects.toThrow('invalid_request'); expect(fetcher).not.toHaveBeenCalled()
  await expect(getRecoveryActions(agent, task, id(9))).rejects.toMatchObject({ code: 'not_found', status: 404 }); expect(fetcher).toHaveBeenCalledOnce()
})
