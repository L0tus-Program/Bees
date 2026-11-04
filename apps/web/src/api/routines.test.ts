import { webcrypto } from 'node:crypto'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { checkedRoutine, checkedRoutineIntent, definitionIntent, getRoutine, isRoutineDefinition, listRoutines, routineOccurrences, routineRevisions, routineStatusIntent, routineRuntime, sendRoutine } from './routines'
import type { RoutineDefinition } from './routines'
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

describe('routine command receipts', () => {
  const binding = { installation_id: agentId, resource_id: routine.id, plugin_id: definition.skill_id, manifest_hash: 'a'.repeat(64), tool_version: '1' as const }
  function receipt(saved: RoutineDefinition, rev = 1) {
    return { ...routine, name: saved.name, instructions: saved.instructions, schedule: saved.schedule, timezone: saved.timezone, definition: saved, revision: rev }
  }
  function response(value: unknown) {
    setCsrfToken('csrf'); vi.stubGlobal('crypto', webcrypto)
    const fetcher = vi.fn().mockResolvedValue(Response.json(value)); vi.stubGlobal('fetch', fetcher)
    return fetcher
  }
  const changes: Partial<RoutineDefinition>[] = [
    { name: 'Outra agenda' }, { instructions: 'Outra instrução' },
    { schedule: { ...definition.schedule, time: '10:00' } },
    { schedule: { frequency: 'weekly', time: '09:00', weekdays: [0, 4] } },
    { timezone: 'UTC' }, { skill_id: routine.id }, { skill_version: 2 },
    { inputs: { tema: 'Outro tema' } }, { environment: 'personal' },
    { destination: { kind: 'conversation', conversation_id: routine.id } },
    { notification: 'none' }, { grace_seconds: 120 }, { execution_mode: 'agent' },
    { runtime_binding: binding },
  ]
  describe.each(['create', 'revise'] as const)('%s definition receipt', (action) => {
    it.each(changes)('rejects divergent content without retry: %j', async (patch) => {
      const fetcher = response(receipt({ ...definition, ...patch }, action === 'create' ? 1 : 2))
      // These are internally consistent snapshots; their content differs from the command.
      expect(() => checkedRoutine(receipt({ ...definition, ...patch }), agentId)).not.toThrow()
      await expect(sendRoutine(definitionIntent(agentId, definition, action === 'revise' ? routine : undefined))).rejects.toThrow('invalid_response')
      expect(fetcher).toHaveBeenCalledOnce()
    })
  })
  it.each(['create', 'revise'] as const)('accepts reordered keys and explicit preparatory defaults in a %s receipt', async (action) => {
    const supplied = { ...definition, inputs: { tema: 'Ciência', outro: 'Texto' } }
    const saved = Object.fromEntries(Object.entries({ ...supplied, inputs: { outro: 'Texto', tema: 'Ciência' }, execution_mode: 'prepare', runtime_binding: null }).reverse()) as unknown as RoutineDefinition
    const value = receipt(saved, action === 'create' ? 1 : 2); response(value)
    await expect(sendRoutine(definitionIntent(agentId, supplied, action === 'revise' ? routine : undefined))).resolves.toEqual(value)
  })
  it.each([false, true])('accepts server-selected scheduled binding only when none was supplied (explicit=%s)', async (explicit) => {
    const supplied: RoutineDefinition = { ...definition, execution_mode: 'scheduled', runtime_binding: explicit ? binding : null }
    const value = receipt({ ...supplied, runtime_binding: binding }); response(value)
    await expect(sendRoutine(definitionIntent(agentId, supplied))).resolves.toEqual(value)
  })
  it('accepts the service UUID normalization without changing user text', async () => {
    const id = 'abcdefab-abcd-4abc-8abc-abcdefabcdef'
    const supplied: RoutineDefinition = { ...definition, skill_id: id.toUpperCase(), destination: { kind: 'conversation', conversation_id: id.toUpperCase() }, execution_mode: 'scheduled', runtime_binding: { ...binding, plugin_id: id.toUpperCase() } }
    const value = receipt({ ...supplied, skill_id: id, destination: { kind: 'conversation', conversation_id: id }, runtime_binding: { ...binding, plugin_id: id } }); response(value)
    await expect(sendRoutine(definitionIntent(agentId, supplied))).resolves.toEqual(value)
  })
  it('requires a bound resource in a successful scheduled definition receipt', async () => {
    const supplied: RoutineDefinition = { ...definition, execution_mode: 'scheduled' }
    const fetcher = response(receipt(supplied))
    await expect(sendRoutine(definitionIntent(agentId, supplied))).rejects.toThrow('invalid_response')
    expect(fetcher).toHaveBeenCalledOnce()
  })
  it.each([null, { ...binding, resource_id: definition.skill_id }])('rejects replacement or loss of the explicit scheduled binding: %j', async (runtime_binding) => {
    const supplied: RoutineDefinition = { ...definition, execution_mode: 'scheduled', runtime_binding: binding }
    const fetcher = response(receipt({ ...supplied, runtime_binding }))
    await expect(sendRoutine(definitionIntent(agentId, supplied))).rejects.toThrow('invalid_response')
    expect(fetcher).toHaveBeenCalledOnce()
  })
  it('does not hide a different mode or input behind a server-selected binding', async () => {
    const supplied: RoutineDefinition = { ...definition, execution_mode: 'scheduled' }
    for (const saved of [{ ...supplied, execution_mode: 'agent' as const, runtime_binding: binding }, { ...supplied, inputs: { tema: 'Outro' }, runtime_binding: binding }]) {
      const fetcher = response(receipt(saved))
      await expect(sendRoutine(definitionIntent(agentId, supplied))).rejects.toThrow('invalid_response')
      expect(fetcher).toHaveBeenCalledOnce()
    }
  })
  it.each([false, true])('binds delayed status receipts to the sent command (altered receipt=%s)', async (altered) => {
    setCsrfToken('csrf'); vi.stubGlobal('crypto', webcrypto)
    const intent = routineStatusIntent(activeRoutine, 'paused')
    let finish!: (value: Response) => void
    const fetcher = vi.fn<typeof fetch>(() => new Promise<Response>((resolve) => { finish = resolve })); vi.stubGlobal('fetch', fetcher)
    const result = sendRoutine(intent)
    intent.agentId = routine.id; intent.routineId = agentId
    intent.body.expected_revision = 9; intent.body.status = 'cancelled'
    const value = altered ? { ...routine, agent_id: routine.id, id: agentId, revision: 10, status: 'cancelled' } : { ...routine, revision: 2 }
    finish(Response.json(value))
    if (altered) await expect(result).rejects.toThrow('invalid_response')
    else await expect(result).resolves.toEqual(value)
    expect(fetcher).toHaveBeenCalledOnce()
    expect(fetcher.mock.calls[0]).toMatchObject([`/api/v1/agents/${agentId}/routines/${routine.id}/status`, { method: 'PUT', body: expect.stringContaining('"status":"paused"') }])
  })
  it.each([false, true])('binds nested delayed definition and source to the sent command (altered receipt=%s)', async (altered) => {
    setCsrfToken('csrf'); vi.stubGlobal('crypto', webcrypto)
    const source = { conversation_id: definition.destination.conversation_id, message_id: occurrence.id, content_hash: 'a'.repeat(64) }
    const intent = definitionIntent(agentId, structuredClone(definition), undefined, { ...source })
    let finish!: (value: Response) => void
    const fetcher = vi.fn<typeof fetch>(() => new Promise<Response>((resolve) => { finish = resolve })); vi.stubGlobal('fetch', fetcher)
    const result = sendRoutine(intent)
    intent.body.definition!.inputs.tema = 'Alterado'
    intent.body.definition!.schedule.time = '10:00'
    intent.body.proposal_source!.content_hash = 'b'.repeat(64)
    const value = { ...receipt(altered ? intent.body.definition! : definition), metadata: { proposal_source: altered ? intent.body.proposal_source : source } }
    finish(Response.json(value))
    if (altered) await expect(result).rejects.toThrow('invalid_response')
    else await expect(result).resolves.toEqual(value)
    const sent = JSON.parse(String(fetcher.mock.calls[0][1]!.body))
    expect(sent).toMatchObject({ definition, proposal_source: source })
    expect(fetcher).toHaveBeenCalledOnce()
  })
})


describe('runtime mode reads', () => {
  it('queries the agent mode explicitly without changing the legacy default', async () => {
    const value = { available: true, reason: null, version: 1, max_calls: 4, max_active_seconds: 120, manual_available: true }
    const fetcher = vi.fn().mockImplementation(async () => Response.json(value)); vi.stubGlobal('fetch', fetcher)
    expect((await routineRuntime(agentId)).manual_available).toBe(true)
    expect((await routineRuntime(agentId, undefined, 'agent')).available).toBe(true)
    expect(fetcher.mock.calls[0][0]).toMatch(/\/runtime$/)
    expect(fetcher.mock.calls[1][0]).toMatch(/\/runtime\?execution_mode=agent$/)
    expect(fetcher.mock.calls.every(([, options]) => options.method === 'GET' && options.body === undefined)).toBe(true)
  })
  it('rejects an unknown runtime mode before sending a request', async () => {
    const fetcher = vi.fn(); vi.stubGlobal('fetch', fetcher)
    await expect(routineRuntime(agentId, undefined, 'unknown' as 'agent')).rejects.toThrow('invalid_request')
    expect(fetcher).not.toHaveBeenCalled()
  })
})
