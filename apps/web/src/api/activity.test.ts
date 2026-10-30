import { afterEach, describe, expect, it, vi } from 'vitest'
import { checkedObservation, getActivity } from './activity'
import { agent, item, page, runId, taskId } from '../features/activity/fixtures'
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })
function serve(value: unknown, status = 200) { return vi.spyOn(globalThis, 'fetch').mockResolvedValue(Response.json(value, { status })) }
describe('closed activity projection', () => {
  it('reads a task-bound token projection with session and no cache', async () => {
    const response = { ...page, scope: { task_id: taskId, run_id: runId } }; const fetcher = serve(response)
    expect(await getActivity(agent.id, { task_id: taskId, run_id: runId, window_seconds: 86400 })).toEqual(response)
    expect(fetcher).toHaveBeenCalledWith(expect.stringContaining(`task_id=${taskId}`), expect.objectContaining({ credentials: 'same-origin', cache: 'no-store' }))
    expect(fetcher.mock.calls[0][0]).toContain('window_seconds=86400'); expect(fetcher.mock.calls[0][0]).not.toContain('until=')
  })
  it.each([
    { agent_id: taskId }, { secret: 'hidden' }, { unit: 'USD' }, { snapshot: { ...page.snapshot, private: true } }, { snapshot: { ...page.snapshot, event_seq: -1 } },
    { interval: { ...page.interval, since: 'without-timezone' } }, { scope: { task_id: taskId, run_id: null } }, { operations: { ...page.operations, private_executor_id: agent.id } },
    { usage: { ...page.usage, unknown_tokens: -1 } }, { usage: { ...page.usage, estimate_methods: ['raw upstream private body'] } }, { usage: { ...page.usage, estimate_methods: ['unknown', 'unknown'] } },
    { next_before: 100 }, { next_before: -1 }, { items: [item, item] },
  ])('rejects wrong bindings, uncertainty or extra fields: %j', async (change) => {
    serve({ ...page, ...change }); await expect(getActivity(agent.id)).rejects.toMatchObject({ code: 'invalid_response' })
  })
  it.each([{ run_id: null, entity_type: 'model_call' }, { entity_type: 'task', entity_id: agent.id }, { environment: { kind: 'vm', environment_id: agent.id } }, { environment: { kind: 'textual', environment_id: null, path: 'private' } }, { task_title: null }, { status: 'private_upstream_output' }, { entity_type: 'unknown' }, { occurred_at: '2026-10-10T11:00:00Z' }, { revision: 0 }, { body: 'private' }])('rejects unsafe event metadata: %j', async (change) => {
    serve({ ...page, items: [{ ...item, ...change }] }); await expect(getActivity(agent.id)).rejects.toMatchObject({ code: 'invalid_response' })
  })
  it('separates historical reservation from final liquidation without treating uncertainty as zero', async () => {
    const reserve = { ...item, entity_type: 'usage_entry', entity_id: actionIdForUsage(), status: 'reserved', usage: { status: 'reserved', source: 'task', usage_kind: null, reserved_tokens: 200, counted_tokens: 200, input_tokens: null, output_tokens: null, total_tokens: null, estimate_method: 'chars_div_3_v1' } }
    serve({ ...page, items: [reserve] }); expect((await getActivity(agent.id)).items[0].usage?.counted_tokens).toBe(200)
    vi.restoreAllMocks(); serve({ ...page, items: [{ ...reserve, usage: { ...reserve.usage, counted_tokens: 0 } }] })
    await expect(getActivity(agent.id)).rejects.toMatchObject({ code: 'invalid_response' })
  })
  it('requires pagination with exact interval and snapshot and verifies the cursor', async () => {
    const response = { ...page, items: [{ ...item, seq: 9 }] }; serve(response)
    expect(await getActivity(agent.id, { since: page.interval.since, until: page.interval.until, before: 10, snapshot_seq: 10 })).toEqual(response)
    vi.restoreAllMocks(); serve({ ...response, snapshot: { ...page.snapshot, event_seq: 11 } })
    await expect(getActivity(agent.id, { since: page.interval.since, until: page.interval.until, before: 10, snapshot_seq: 10 })).rejects.toMatchObject({ code: 'invalid_response' })
  })
  it.each([{ run_id: runId }, { before: 10 }, { window_seconds: 3600 }, { window_seconds: 86400, since: page.interval.since }, { snapshot_seq: 10 }, { before: 10, snapshot_seq: 10 }, { extra: true }])('rejects invalid queries before any network: %j', async (query) => {
    const fetcher = serve(page); await expect(getActivity(agent.id, query)).rejects.toMatchObject({ code: 'invalid_request' }); expect(fetcher).not.toHaveBeenCalled()
  })
  it('propagates a snapshot conflict for human refresh without replaying a request', async () => {
    const fetcher = serve({ error: { code: 'state_conflict' } }, 409)
    await expect(getActivity(agent.id)).rejects.toMatchObject({ code: 'state_conflict', status: 409 }); expect(fetcher).toHaveBeenCalledTimes(1)
  })
  it('compares observation order while allowing changing derivations at the same event sequence', () => {
    expect(() => checkedObservation({ event_seq: 10, observed_at: '2026-10-12T12:01:00Z' }, page.snapshot)).not.toThrow()
    expect(() => checkedObservation({ event_seq: 9, observed_at: '2026-10-12T12:01:00Z' }, page.snapshot)).toThrow('invalid_response')
    expect(() => checkedObservation({ event_seq: 11, observed_at: '2026-10-12T11:59:00Z' }, page.snapshot)).toThrow('invalid_response')
    expect(() => checkedObservation({ event_seq: 10, observed_at: '2026-10-12T12:00:00.000001Z' }, { event_seq: 10, observed_at: '2026-10-12T12:00:00.000002Z' })).toThrow('invalid_response')
  })
})
function actionIdForUsage() { return '66666666-6666-4666-8666-666666666666' }
