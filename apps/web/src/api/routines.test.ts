import { webcrypto } from 'node:crypto'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { checkedRoutine, checkedRoutineIntent, definitionIntent, getRoutine, isRoutineDefinition, listRoutines, routineOccurrences, routineRevisions, routineStatusIntent, sendRoutine } from './routines'
import { setCsrfToken } from './client'
import { activeRoutine, agentId, definition, occurrence, persistedRoutine as routine, revision } from '../features/routines/fixtures'

afterEach(() => { vi.unstubAllGlobals(); setCsrfToken() })
describe('routine transport and human commands', () => {
  it('preserves the old payload limit when an upgraded API returns preparatory defaults', () => {
    const candidate = { ...definition, instructions: 'x'.repeat(4096), inputs: Object.fromEntries(Array.from({ length: 16 }, (_, index) => [`v${index}`, 'x'])) }
    const size = () => new TextEncoder().encode(JSON.stringify(candidate)).byteLength
    for (const key of Object.keys(candidate.inputs)) {
      const extra = Math.min(2047, 32768 - size())
      candidate.inputs[key] += 'x'.repeat(extra)
      if (size() === 32768) break
    }
    expect(size()).toBe(32768)
    expect(isRoutineDefinition(candidate)).toBe(true)
    expect(isRoutineDefinition({ ...candidate, execution_mode: 'prepare', runtime_binding: null })).toBe(true)
    expect(isRoutineDefinition({ ...candidate, execution_mode: 'scheduled' })).toBe(false)
    const room = Object.keys(candidate.inputs).find((key) => candidate.inputs[key].length < 2048)!
    candidate.inputs[room] += 'x'
    expect(isRoutineDefinition({ ...candidate, execution_mode: 'prepare', runtime_binding: null })).toBe(false)
  })
  it('rejects unsafe and unsupported schedules and validates origin and revision identity', () => {
    expect(isRoutineDefinition(definition)).toBe(true)
    for (const value of [
      { ...definition, timezone: 'Invalid/Zone' }, { ...definition, misfire_policy: 'catch_up' }, { ...definition, overlap_policy: 'queue' },
      { ...definition, schedule: { ...definition.schedule, time: '24:00' } }, { ...definition, schedule: { frequency: 'weekly', time: '09:00', weekdays: [] } },
      { ...definition, schedule: { frequency: 'weekly', time: '09:00', weekdays: [1, 1] } }, { ...definition, schedule: { frequency: 'weekly', time: '09:00', weekdays: [true] } },
      { ...definition, destination: { kind: 'email', conversation_id: definition.destination.conversation_id } }, { ...definition, grant: 'allow' }, { ...definition, inputs: { tema: 'sk-or-v1-' + 'x'.repeat(30) } },
    ]) expect(isRoutineDefinition(value)).toBe(false)
    for (const value of [
      { ...routine, agent_id: routine.id }, { ...routine, origin: 'model' }, { ...routine, origin: 'legacy' }, { ...routine, definition: null },
      { ...routine, revision: 0 }, { ...routine, next_run_at: '2026-01-01T09:00:00Z' }, { ...routine, name: 'Divergent name' },
      { ...routine, schedule: { ...routine.schedule, time: '10:00' } }, { ...routine, status: 'running' },
    ]) expect(() => checkedRoutine(value, agentId)).toThrow('invalid_response')
    expect(checkedRoutine(routine, agentId)).toEqual(routine)
    expect(checkedRoutine({ ...routine, definition: null, origin: 'legacy', schedule: { cron: '*' }, misfire_policy: 'catch_up' }, agentId).origin).toBe('legacy')
  })
  it('binds edit/status to CAS and preserves UUID and complete content after a lost write', async () => {
    vi.stubGlobal('crypto', webcrypto); setCsrfToken('csrf')
    const intent = definitionIntent(agentId, definition, routine)
    expect(intent.body).toMatchObject({ expected_revision: 1, definition })
    expect(intent.body).not.toHaveProperty('active')
    const next = { ...routine, revision: 2 }
    const fetcher = vi.fn().mockRejectedValueOnce(new TypeError('Lost')).mockResolvedValueOnce(Response.json(next))
    vi.stubGlobal('fetch', fetcher)
    await expect(sendRoutine(intent)).rejects.toThrow('mutation_network')
    expect(await sendRoutine(intent)).toEqual(next)
    expect(fetcher.mock.calls[0][1].body).toBe(fetcher.mock.calls[1][1].body)
    expect(routineStatusIntent(activeRoutine, 'paused').body).toMatchObject({ expected_revision: 1, status: 'paused' })
    expect(() => definitionIntent(routine.id, definition, routine)).toThrow('invalid_request')
    expect(() => routineStatusIntent({ ...routine, status: 'cancelled' }, 'active')).toThrow('invalid_request')
  })
  it('does not allow storage to inject a path, active creation or cross-agent mutation', () => {
    vi.stubGlobal('crypto', webcrypto)
    const intent = definitionIntent(agentId, definition)
    for (const value of [
      { ...intent, path: '/tasks' }, { ...intent, agentId: routine.id }, { ...intent, routineId: routine.id },
      { ...intent, body: { ...intent.body, active: true } }, { ...intent, body: { ...intent.body, client_request_id: '00000000-0000-0000-0000-000000000000' } },
    ]) expect(() => checkedRoutineIntent(value, agentId)).toThrow('invalid_request')
  })
  it('checks pagination, duplicate routines and exact target identity on read', async () => {
    const fetcher = vi.fn().mockResolvedValueOnce(Response.json([routine])).mockResolvedValueOnce(Response.json([routine, routine])).mockResolvedValueOnce(Response.json({ ...routine, id: agentId }))
    vi.stubGlobal('fetch', fetcher)
    expect(await listRoutines(agentId, 50)).toEqual([routine])
    expect(fetcher.mock.calls[0][0]).toContain('limit=50&offset=50')
    await expect(listRoutines(agentId)).rejects.toThrow('invalid_response')
    await expect(getRoutine(agentId, routine.id)).rejects.toThrow('invalid_response')
    await expect(listRoutines(agentId, -1)).rejects.toThrow('invalid_request')
  })
  it('checks history, immutable revision identities and skipped range constraints', async () => {
    const fetcher = vi.fn().mockResolvedValueOnce(Response.json([occurrence])).mockResolvedValueOnce(Response.json([{ ...occurrence, routine_id: agentId }]))
      .mockResolvedValueOnce(Response.json([{ ...occurrence, status: 'pending', window_end: '2026-10-17T12:00:00Z' }]))
      .mockResolvedValueOnce(Response.json([revision])).mockResolvedValueOnce(Response.json([revision, revision]))
    vi.stubGlobal('fetch', fetcher)
    expect(await routineOccurrences(agentId, routine.id)).toEqual([occurrence])
    await expect(routineOccurrences(agentId, routine.id)).rejects.toThrow('invalid_response')
    await expect(routineOccurrences(agentId, routine.id)).rejects.toThrow('invalid_response')
    expect(await routineRevisions(agentId, routine.id, 50)).toEqual([revision])
    expect(fetcher.mock.calls[3][0]).toContain('revisions?limit=50&offset=50')
    await expect(routineRevisions(agentId, routine.id)).rejects.toThrow('invalid_response')
  })
})
