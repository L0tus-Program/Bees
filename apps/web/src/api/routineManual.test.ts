import { webcrypto } from 'node:crypto'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { setCsrfToken } from './client'
import { checkedManualIntent, executeManualRoutine, getManualRoutineReceipt, manualRoutineIntent } from './routineManual'
import type { ManualRoutineIntent } from './routineManual'
import { digest } from './toolTasks'
import { routine, occurrence, agentId } from '../features/routines/fixtures'

async function receipt(intent: ManualRoutineIntent) {
  return { client_request_id: intent.body.client_request_id, request_hash: await digest(intent.body), agent_id: agentId, routine_id: routine.id, routine_revision: 1,
    runtime_binding: { installation_id: agentId, resource_id: routine.id, plugin_id: occurrence.id, manifest_hash: 'a'.repeat(64), tool_version: '1' },
    occurrence: { ...occurrence, status: 'running', reason: 'task_created', task_id: agentId, revision: 2, metadata: { origin: 'manual', task_status: 'queued' } } }
}
beforeEach(() => { vi.stubGlobal('crypto', webcrypto); setCsrfToken('csrf') })
afterEach(() => { vi.unstubAllGlobals(); setCsrfToken() })
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
