import { afterEach, describe, expect, it, vi } from 'vitest'
import { setCsrfToken } from './client'
import { controlTask, createTask, getTask, getTasks, isTask } from './tasks'
import type { TaskRecord } from './tasks'
import { commandIdentity, mergeTaskSnapshot, taskOutcomeUnknown } from '../features/tasks/state'
import { startTaskPolling } from '../features/tasks/polling'

const stamp = '2026-10-05T17:00:00Z'
const task: TaskRecord = { id: 'task-1', agent_id: 'bee-1', conversation_id: 'task-conversation', title: 'Comparar opções', objective: 'Compare A e B', expected_result: 'Vantagens e limites', status: 'queued', revision: 1, created_at: stamp, updated_at: stamp, active_run_id: 'run-1', control_requested: null, available_controls: ['pause', 'cancel', 'redirect'], latest_run: { id: 'run-1', status: 'queued', provider: 'openai_compatible', model: 'model-1', started_at: null, finished_at: null, error_code: null, result: null }, progress: { code: 'queued', created_at: stamp }, unknown_model_calls: 0, unknown_tool_actions: 0, unknown_requires_ack: false, action_in_flight: false }
const pending: ReturnType<typeof startTaskPolling>[] = []
afterEach(() => { pending.forEach((poller) => poller.stop()); pending.length = 0; vi.useRealTimers(); vi.unstubAllGlobals(); setCsrfToken() })

describe('durable task API contracts', () => {
  it('reads worker presence and task state without starting execution', async () => {
    const fetcher = vi.fn().mockResolvedValue(Response.json({ tasks: [task], has_more: false, worker: { available: false, last_seen: null } }))
    vi.stubGlobal('fetch', fetcher)
    await expect(getTasks('bee-1')).resolves.toEqual({ tasks: [task], has_more: false, worker: { available: false, last_seen: null } })
    expect(fetcher.mock.calls[0][0]).toBe('/api/v1/agents/bee-1/tasks')
    expect(fetcher.mock.calls[0][1]).toMatchObject({ method: 'GET', credentials: 'same-origin' })
    expect(fetcher.mock.calls[0][1].body).toBeUndefined()
  })
  it.each([
    { ...task, agent_id: 'another-bee' }, { ...task, revision: 0 }, { ...task, status: 'invented' },
    { ...task, control_requested: 'invented' }, { ...task, available_controls: ['approve'] },
    { ...task, updated_at: 'invalid-date' }, { ...task, latest_run: { ...task.latest_run, result: { content: null, created_at: stamp } } },
  ])('refuses mismatched or malformed task state: %j', async (record) => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(Response.json({ tasks: [record], has_more: false })))
    await expect(getTasks('bee-1')).rejects.toMatchObject({ code: 'invalid_response' })
  })
  it('refuses duplicate tasks and malformed worker timestamps', async () => {
    const fetcher = vi.fn().mockResolvedValueOnce(Response.json({ tasks: [task, task], has_more: false })).mockResolvedValueOnce(Response.json({ tasks: [], has_more: false, worker: { available: true, last_seen: 'yesterday' } }))
    vi.stubGlobal('fetch', fetcher)
    await expect(getTasks('bee-1')).rejects.toMatchObject({ code: 'invalid_response' })
    await expect(getTasks('bee-1')).rejects.toMatchObject({ code: 'invalid_response' })
  })
  it('recovers a persisted textual result and nullable command content', async () => {
    const completed = { ...task, status: 'completed', revision: 3, available_controls: [], latest_run: { ...task.latest_run, status: 'completed', result: { content: 'A prioriza rapidez; B oferece mais controle.', created_at: stamp } } }
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(Response.json({ task: completed, events: [{ id: 'event-1', code: 'pause', created_at: stamp, run_id: null, content: null }], actions: [], has_more: false })))
    expect((await getTask('bee-1', task.id)).task.latest_run?.result?.content).toBe('A prioriza rapidez; B oferece mais controle.')
  })
  it('rejects another task returned by the detail or mutation endpoint', async () => {
    const fetcher = vi.fn().mockResolvedValueOnce(Response.json({ task: { ...task, id: 'wrong-task' }, events: [], actions: [], has_more: false })).mockResolvedValueOnce(Response.json({ ...task, id: 'wrong-task' }))
    vi.stubGlobal('fetch', fetcher); setCsrfToken('test-csrf')
    await expect(getTask('bee-1', task.id)).rejects.toMatchObject({ code: 'invalid_response' })
    await expect(controlTask('bee-1', task.id, { action: 'pause', client_request_id: 'request-1', expected_revision: 1 })).rejects.toMatchObject({ code: 'invalid_response' })
  })
  it('creates with explicit identity, objective and result; no model call is initiated by the client', async () => {
    const fetcher = vi.fn().mockResolvedValue(Response.json(task)); vi.stubGlobal('fetch', fetcher); setCsrfToken('test-csrf')
    const body = { client_request_id: 'request-1', conversation_id: 'origin-conversation', title: task.title, objective: task.objective, expected_result: task.expected_result }
    await expect(createTask(task.agent_id, body)).resolves.toEqual(task)
    expect(JSON.parse(fetcher.mock.calls[0][1].body)).toEqual(body)
    expect(fetcher.mock.calls[0][1].headers['X-Bees-CSRF']).toBe('test-csrf')
    expect(fetcher).toHaveBeenCalledTimes(1)
  })
  it('controls use revision CAS and unknown resume acknowledgment explicitly', async () => {
    const paused = { ...task, status: 'paused', revision: 3, available_controls: ['resume', 'cancel'], latest_run: { ...task.latest_run, error_code: 'outcome_unknown' } }
    const fetcher = vi.fn().mockResolvedValue(Response.json(paused)); vi.stubGlobal('fetch', fetcher); setCsrfToken('test-csrf')
    const body = { client_request_id: 'request-2', expected_revision: 2, action: 'resume' as const, acknowledge_unknown: true }
    await controlTask(task.agent_id, task.id, body)
    expect(fetcher.mock.calls[0][0]).toBe('/api/v1/agents/bee-1/tasks/task-1/control')
    expect(JSON.parse(fetcher.mock.calls[0][1].body)).toEqual(body)
  })
  it('a revision conflict or unknown outcome never triggers a mutation retry', async () => {
    const fetcher = vi.fn().mockResolvedValue(Response.json({ error: { code: 'state_conflict' } }, { status: 409 }))
    vi.stubGlobal('fetch', fetcher); setCsrfToken('test-csrf')
    await expect(controlTask(task.agent_id, task.id, { client_request_id: 'request-1', expected_revision: 1, action: 'redirect', instruction: 'Priorize B' })).rejects.toMatchObject({ code: 'state_conflict' })
    expect(fetcher).toHaveBeenCalledTimes(1)
  })
  it('cannot delegate without session CSRF', async () => {
    const fetcher = vi.fn(); vi.stubGlobal('fetch', fetcher)
    await expect(createTask(task.agent_id, { client_request_id: 'request-1', title: 'Test', objective: 'Test', expected_result: 'Test' })).rejects.toMatchObject({ code: 'csrf_missing' })
    expect(fetcher).not.toHaveBeenCalled()
  })
})

describe('task observation and command identities', () => {
  it('keeps newer acknowledged revision when older polling arrives late', () => {
    const newer = { ...task, revision: 4, status: 'paused' as const }
    expect(mergeTaskSnapshot([task], [newer])).toEqual([newer])
    expect(mergeTaskSnapshot([newer], [task])).toEqual([newer])
  })
  it('preserves request identity for an explicit retry of the same command and changes identity for new input', () => {
    let count = 0; const identity = commandIdentity(() => `id-${++count}`)
    const body = { expected_revision: 1, action: 'pause' }
    expect(identity.forPayload(body)).toBe('id-1')
    expect(identity.forPayload({ ...body })).toBe('id-1')
    expect(identity.forPayload({ ...body, action: 'cancel' })).toBe('id-2')
    identity.reset(); expect(identity.forPayload(body)).toBe('id-3')
  })
  it('unknown outcome uses durable global counts, never generic failures or an already acknowledged run', () => {
    expect(taskOutcomeUnknown(task)).toBe(false)
    expect(taskOutcomeUnknown({ ...task, latest_run: { ...task.latest_run!, error_code: 'outcome_unknown' }, unknown_model_calls: 1, unknown_requires_ack: true })).toBe(true)
    expect(taskOutcomeUnknown({ ...task, latest_run: { ...task.latest_run!, error_code: 'outcome_unknown' } })).toBe(false)
    expect(taskOutcomeUnknown({ ...task, latest_run: { ...task.latest_run!, error_code: 'authentication_failed' } })).toBe(false)
    expect(isTask({ ...task, status: 'paused', available_controls: ['resume'] })).toBe(true)
  })
  it('polling is sequential: long reads do not start overlapping intervals', async () => {
    vi.useFakeTimers()
    let resolve!: (value: string) => void
    const load = vi.fn().mockImplementationOnce(() => new Promise<string>((done) => { resolve = done })).mockResolvedValue('next')
    const accept = vi.fn(); const poller = startTaskPolling(load, accept, vi.fn(), () => true); pending.push(poller)
    await vi.advanceTimersByTimeAsync(15000)
    expect(load).toHaveBeenCalledTimes(1)
    resolve('first'); await vi.advanceTimersByTimeAsync(0)
    await vi.advanceTimersByTimeAsync(5000)
    expect(load).toHaveBeenCalledTimes(2); expect(accept.mock.calls.map((call) => call[0])).toEqual(['first', 'next'])
  })
  it('refresh aborts the previous read and ignores its late result', async () => {
    vi.useFakeTimers()
    let resolve!: (value: string) => void
    const load = vi.fn().mockImplementationOnce(() => new Promise<string>((done) => { resolve = done })).mockResolvedValue('current')
    const accept = vi.fn(); const poller = startTaskPolling(load, accept, vi.fn(), () => true); pending.push(poller)
    const signal = load.mock.calls[0][0] as AbortSignal
    poller.refresh(); await vi.advanceTimersByTimeAsync(0)
    resolve('stale'); await vi.advanceTimersByTimeAsync(0)
    expect(signal.aborted).toBe(true); expect(accept).toHaveBeenCalledExactlyOnceWith('current')
  })
  it('logout/unmount aborts reads and stops future observations', async () => {
    vi.useFakeTimers()
    let resolve!: (value: string) => void
    const load = vi.fn().mockImplementation(() => new Promise<string>((done) => { resolve = done }))
    const accept = vi.fn(); const reject = vi.fn(); const poller = startTaskPolling(load, accept, reject, () => true)
    const signal = load.mock.calls[0][0] as AbortSignal
    poller.stop(); resolve('late'); await vi.advanceTimersByTimeAsync(20000)
    expect(signal.aborted).toBe(true); expect(load).toHaveBeenCalledTimes(1); expect(accept).not.toHaveBeenCalled(); expect(reject).not.toHaveBeenCalled()
  })
  it('hidden pages do not start periodic observations until visible again', async () => {
    vi.useFakeTimers(); let visible = false
    const load = vi.fn().mockResolvedValue('snapshot'); const poller = startTaskPolling(load, vi.fn(), vi.fn(), () => visible); pending.push(poller)
    await vi.advanceTimersByTimeAsync(20000); expect(load).toHaveBeenCalledTimes(1)
    visible = true; await vi.advanceTimersByTimeAsync(5000); expect(load).toHaveBeenCalledTimes(2)
  })
  it('read failure does not overwrite the last valid snapshot', async () => {
    vi.useFakeTimers()
    const load = vi.fn().mockResolvedValueOnce('valid').mockRejectedValueOnce(new Error('offline')).mockResolvedValueOnce('recovered')
    const accept = vi.fn(); const reject = vi.fn(); const poller = startTaskPolling(load, accept, reject, () => true); pending.push(poller)
    await vi.advanceTimersByTimeAsync(5000); expect(accept).toHaveBeenCalledExactlyOnceWith('valid'); expect(reject).toHaveBeenCalledTimes(1)
    await vi.advanceTimersByTimeAsync(5000); expect(accept.mock.calls.map((call) => call[0])).toEqual(['valid', 'recovered'])
  })
})
