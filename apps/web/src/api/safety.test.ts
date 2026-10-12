import { afterEach, describe, expect, it, vi } from 'vitest'
import { getSafety, isSafetyState, sendSafetyCommand } from './safety'
import { setCsrfToken } from './client'

const flight = { model_calls: 0, tool_actions: 0, chat_reservations: 0, provisioning_effects: 0 }
const running = { status: 'running', generation: 0, revision: 1, changed_at: '1970-01-01T00:00:00+00:00', reason: null, in_flight: flight }
const stopped = { ...running, status: 'stopped', generation: 1, revision: 2, changed_at: '2026-10-10T12:00:00+00:00', reason: 'Teste', in_flight: { ...flight, model_calls: 1 } }
const respond = (body: unknown, status = 200) => vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } }))

afterEach(() => { vi.restoreAllMocks(); setCsrfToken() })

describe('global stop contract', () => {
  it('accepts only the exact canonical state', () => {
    expect(isSafetyState(running)).toBe(true)
    expect(isSafetyState(stopped)).toBe(true)
    expect(isSafetyState({ ...running, extra: true })).toBe(false)
    expect(isSafetyState({ ...running, status: 'paused' })).toBe(false)
    expect(isSafetyState({ ...running, revision: 0 })).toBe(false)
    expect(isSafetyState({ ...running, generation: -1 })).toBe(false)
    expect(isSafetyState({ ...running, changed_at: 'ontem' })).toBe(false)
    expect(isSafetyState({ ...running, reason: 'x'.repeat(501) })).toBe(false)
    expect(isSafetyState({ ...running, in_flight: { ...flight, model_calls: 1.5 } })).toBe(false)
    expect(isSafetyState({ ...running, in_flight: { ...flight, secret: 1 } })).toBe(false)
  })
  it('reads the state and refuses an invalid response', async () => {
    respond(running)
    await expect(getSafety()).resolves.toEqual(running)
    vi.restoreAllMocks(); respond({ ...running, in_flight: {} })
    await expect(getSafety()).rejects.toMatchObject({ code: 'invalid_response' })
  })
  it('confirms success only with the requested status and an advanced revision', async () => {
    setCsrfToken('csrf')
    const fetch = respond(stopped)
    const body = { client_request_id: '7d1c2a4e-5b6f-4c8d-9e0f-1a2b3c4d5e6f', kind: 'stop' as const, expected_revision: 1, reason: 'Teste' }
    await expect(sendSafetyCommand(body)).resolves.toEqual(stopped)
    const [url, init] = fetch.mock.calls[0]
    expect(String(url)).toContain('/api/v1/safety/commands')
    expect(init?.method).toBe('POST')
    expect(JSON.parse(String(init?.body))).toEqual(body)
    // Replay de parada depois de uma retomada alheia não pode ser exibido como parada confirmada.
    vi.restoreAllMocks(); respond({ ...running, generation: 1, revision: 3 })
    await expect(sendSafetyCommand(body)).rejects.toMatchObject({ code: 'invalid_response' })
    vi.restoreAllMocks(); respond(running)
    await expect(sendSafetyCommand({ ...body, kind: 'resume' })).rejects.toMatchObject({ code: 'invalid_response' })
  })
  it('surfaces a lost response so the same request can be replayed', async () => {
    setCsrfToken('csrf')
    vi.spyOn(globalThis, 'fetch').mockRejectedValue(new TypeError('network'))
    await expect(sendSafetyCommand({ client_request_id: '7d1c2a4e-5b6f-4c8d-9e0f-1a2b3c4d5e6f', kind: 'stop', expected_revision: 1 })).rejects.toMatchObject({ code: 'mutation_network' })
  })
})
