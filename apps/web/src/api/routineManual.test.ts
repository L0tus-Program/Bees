import { webcrypto } from 'node:crypto'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { setCsrfToken } from './client'
import { checkedManualIntent, executeManualRoutine, getManualRoutineReceipt, manualRoutineIntent } from './routineManual'
import type { ManualRoutineIntent } from './routineManual'
import { digest } from './toolTasks'
import { routine, occurrence, agentId } from '../features/routines/fixtures'

async function receipt(intent: ManualRoutineIntent) {
  return { client_request_id: intent.body.client_request_id, request_hash: await digest(intent.body), agent_id: intent.agentId, routine_id: intent.routineId, routine_revision: intent.body.expected_revision,
    runtime_binding: { installation_id: agentId, resource_id: routine.id, plugin_id: occurrence.id, manifest_hash: 'a'.repeat(64), tool_version: '1' },
    occurrence: { ...occurrence, routine_id: intent.routineId, routine_revision: intent.body.expected_revision, status: 'running', reason: 'task_created', task_id: agentId, revision: 2, metadata: { origin: 'manual', task_status: 'queued' } } }
}
beforeEach(() => { vi.stubGlobal('crypto', webcrypto); setCsrfToken('csrf') })
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); setCsrfToken() })
describe('manual routine intent and receipt', () => {
  it('binds one human request to its revision and verifies the immutable receipt on read', async () => {
    const intent = manualRoutineIntent(routine)
    expect(intent.body).toEqual({ client_request_id: expect.any(String), expected_revision: 1 })
    const reply = await receipt(intent)
    const fetcher = vi.fn().mockResolvedValueOnce(Response.json(reply)).mockResolvedValueOnce(Response.json(reply))
    vi.stubGlobal('fetch', fetcher)
    expect(await executeManualRoutine(intent)).toEqual(reply)
    expect(await getManualRoutineReceipt(intent)).toEqual(reply)
    expect(fetcher.mock.calls[0][1].method).toBe('POST')
    expect(fetcher.mock.calls[1][1].method).toBe('GET')
    expect(fetcher.mock.calls[1][0]).toContain(intent.body.client_request_id)
  })
  it('rejects forged paths, grants, zero UUID, cross-agent and stale-shaped requests', () => {
    const intent = manualRoutineIntent(routine)
    for (const value of [{ ...intent, path: '/tasks' }, { ...intent, agentId: routine.id }, { ...intent, body: { ...intent.body, runtime_binding: {} } }, { ...intent, body: { ...intent.body, expected_revision: true } }, { ...intent, body: { ...intent.body, client_request_id: '00000000-0000-0000-0000-000000000000' } }]) expect(() => checkedManualIntent(value, agentId)).toThrow('invalid_request')
    expect(() => manualRoutineIntent({ ...routine, status: 'cancelled' })).toThrow('invalid_request')
    expect(() => manualRoutineIntent({ ...routine, origin: 'legacy', definition: null })).toThrow('invalid_request')
  })
  it('never accepts a receipt for another intent, target or incomplete occurrence', async () => {
    const intent = manualRoutineIntent(routine), reply = await receipt(intent)
    for (const value of [
      { ...reply, client_request_id: routine.id }, { ...reply, request_hash: '0'.repeat(64) }, { ...reply, agent_id: routine.id }, { ...reply, routine_revision: 2 },
      { ...reply, runtime_binding: { ...reply.runtime_binding, path: '/tmp/private' } }, { ...reply, occurrence: { ...reply.occurrence, task_id: null } },
      { ...reply, occurrence: { ...reply.occurrence, metadata: { ...reply.occurrence.metadata, inputs: 'private' } } }, { ...reply, occurrence: { ...reply.occurrence, routine_id: agentId } },
    ]) {
      vi.stubGlobal('fetch', vi.fn().mockResolvedValue(Response.json(value)))
      await expect(executeManualRoutine(intent)).rejects.toThrow('invalid_response')
    }
  })
})

const mutations: [string, (intent: ManualRoutineIntent) => void][] = [
  ['agent', (intent) => { intent.agentId = occurrence.id }],
  ['routine', (intent) => { intent.routineId = occurrence.id }],
  ['request UUID', (intent) => { intent.body.client_request_id = occurrence.id }],
  ['revision', (intent) => { intent.body.expected_revision = 2 }],
  ['replacement body', (intent) => { intent.body = { client_request_id: occurrence.id, expected_revision: 2 } }],
]
for (const [operation, run] of [['POST', executeManualRoutine], ['GET', getManualRoutineReceipt]] as const) {
  describe(`${operation} manual receipt with a caller changed in flight`, () => {
    for (const [field, mutate] of mutations) {
      it(`accepts the original receipt after changing ${field}`, async () => {
        const intent = manualRoutineIntent(routine), original = structuredClone(intent)
        const reply = await receipt(original)
        let resolve!: (value: Response) => void
        const fetcher = vi.fn(() => new Promise<Response>((done) => { resolve = done }))
        vi.stubGlobal('fetch', fetcher)
        const pending = run(intent)
        mutate(intent)
        resolve(Response.json(reply))
        await expect(pending).resolves.toEqual(reply)
        expect(fetcher).toHaveBeenCalledTimes(1)
        expect(fetcher.mock.calls[0]).toEqual(expect.arrayContaining([
          expect.stringContaining(`/agents/${original.agentId}/routines/${original.routineId}/manual`),
          expect.objectContaining({ method: operation, ...(operation === 'POST' ? { body: JSON.stringify(original.body) } : {}) }),
        ]))
      })
      it(`rejects a receipt for the changed ${field} without retrying`, async () => {
        const intent = manualRoutineIntent(routine)
        let resolve!: (value: Response) => void
        const fetcher = vi.fn(() => new Promise<Response>((done) => { resolve = done }))
        vi.stubGlobal('fetch', fetcher)
        const pending = run(intent)
        const rejection = expect(pending).rejects.toThrow('invalid_response')
        mutate(intent)
        resolve(Response.json(await receipt(intent)))
        await rejection
        expect(fetcher).toHaveBeenCalledTimes(1)
      })
    }
  })
}

it.each([executeManualRoutine, getManualRoutineReceipt])('keeps the snapshot through asynchronous hash verification (%#)', async (run) => {
  const intent = manualRoutineIntent(routine), reply = await receipt(intent)
  let release!: () => void, entered!: () => void
  const gate = new Promise<void>((done) => { release = done })
  const hashing = new Promise<void>((done) => { entered = done })
  const originalDigest = webcrypto.subtle.digest.bind(webcrypto.subtle)
  vi.spyOn(webcrypto.subtle, 'digest').mockImplementation(async (algorithm, data) => {
    const hash = await originalDigest(algorithm, data)
    entered(); await gate
    return hash
  })
  const fetcher = vi.fn().mockResolvedValue(Response.json(reply)); vi.stubGlobal('fetch', fetcher)
  const pending = run(intent)
  await hashing
  intent.agentId = occurrence.id; intent.routineId = occurrence.id; intent.body.expected_revision = 9
  release()
  await expect(pending).resolves.toEqual(reply)
  expect(fetcher).toHaveBeenCalledTimes(1)
})
