import { afterEach, describe, expect, it, vi } from 'vitest'
import { ActivityReader, groupActivity, mergeActivity } from './state'
import { actionId, agent, item, page, runId, taskId } from './fixtures'
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })
describe('activity context and observation', () => {
  it('groups events by task, run and action', () => {
    const groups = groupActivity([item, { ...item, id: agent.id, action_id: actionId }, { ...item, id: runId, action_id: actionId }, { ...item, id: taskId, task_id: null, task_title: null, run_id: null }])
    expect(groups).toHaveLength(2); expect(groups[0].runs[0].actions[1].items).toHaveLength(2); expect(groups[1].taskId).toBeNull()
  })
  it('rejects rewritten history while allowing current task titles and error projections to change', () => {
    expect(mergeActivity([item], [{ ...item, task_title: 'Título atualizado', error_code: null }], false)).toHaveLength(1)
    for (const change of [{ seq: 9 }, { revision: 1 }, { task_id: agent.id }, { run_id: agent.id }, { status: 'running' }]) expect(() => mergeActivity([item], [{ ...item, ...change }], false)).toThrow('invalid_response')
  })
  it('preserves the entire last snapshot on failed reload and refuses regressive observations', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(Response.json(page)).mockRejectedValueOnce(new Error('network')).mockResolvedValueOnce(Response.json({ ...page, snapshot: { ...page.snapshot, event_seq: 9 } }))
    const reader = new ActivityReader(agent.id, 1, page.scope, vi.fn()); await reader.load(); const original = reader.state.page
    await reader.load(); expect(reader.state.page).toBe(original); expect(reader.state.error?.code).toBe('network_error')
    await reader.load(); expect(reader.state.page).toBe(original); expect(reader.state.error?.code).toBe('invalid_response')
  })
  it('keeps current in-flight and unknown operations even with an empty interval', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(Response.json({ ...page, items: [] }))
    const reader = new ActivityReader(agent.id, 1, page.scope, vi.fn()); await reader.load()
    expect(reader.state.page?.items).toEqual([]); expect(reader.state.page?.operations.unknown_model_calls).toBe(1); expect(reader.state.page?.operations.model_calls_in_flight).toBe(1)
  })
  it('pins pagination to the exact query snapshot and keeps earlier context on conflict', async () => {
    const fetcher = vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(Response.json({ ...page, next_before: 10 })).mockResolvedValueOnce(Response.json({ error: { code: 'state_conflict' } }, { status: 409 }))
    const reader = new ActivityReader(agent.id, 1, { task_id: null, run_id: null }, vi.fn()); await reader.load(); await reader.load(true)
    const url = new URL(String(fetcher.mock.calls[1][0]), 'http://127.0.0.1')
    expect(url.searchParams.get('since')).toBe(page.interval.since); expect(url.searchParams.get('until')).toBe(page.interval.until); expect(url.searchParams.get('snapshot_seq')).toBe('10'); expect(url.searchParams.get('before')).toBe('10')
    expect(reader.state.page?.items).toEqual([item]); expect(reader.state.error?.status).toBe(409)
  })
  it('aborts and fences late replies when leaving a bee or scope', async () => {
    let reply!: (value: Response) => void
    const fetcher = vi.spyOn(globalThis, 'fetch').mockImplementation(() => new Promise<Response>((resolve) => { reply = resolve }))
    const publish = vi.fn(); const reader = new ActivityReader(agent.id, 1, page.scope, publish); const pending = reader.load(); reader.stop()
    expect(fetcher.mock.calls[0][1]?.signal?.aborted).toBe(true); reply(Response.json(page)); await pending
    expect(reader.state.page).toBeNull(); expect(publish).toHaveBeenCalledTimes(1)
  })
  it('uses the server interval instead of a browser clock even when it is far ahead', async () => {
    vi.spyOn(Date, 'now').mockReturnValue(Date.parse('2099-01-01T00:00:00Z'))
    const fetcher = vi.spyOn(globalThis, 'fetch').mockResolvedValue(Response.json(page))
    const reader = new ActivityReader(agent.id, 1, page.scope, vi.fn()); await reader.load()
    expect(fetcher.mock.calls[0][0]).toContain('window_seconds=86400'); expect(fetcher.mock.calls[0][0]).not.toContain('until='); expect(reader.state.error).toBeNull()
  })
})
