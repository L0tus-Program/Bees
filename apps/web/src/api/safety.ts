import { ApiError, record, request } from './client'

export type SafetyKind = 'stop' | 'resume'
export interface SafetyInFlight { model_calls: number; tool_actions: number; chat_reservations: number; provisioning_effects: number }
export interface SafetyState {
  status: 'running' | 'stopped'; generation: number; revision: number; changed_at: string
  reason: string | null; in_flight: SafetyInFlight
}

const KEYS = ['changed_at', 'generation', 'in_flight', 'reason', 'revision', 'status']
const FLIGHT = ['chat_reservations', 'model_calls', 'provisioning_effects', 'tool_actions']
const count = (value: unknown) => Number.isSafeInteger(value) && (value as number) >= 0
const exact = (value: Record<string, unknown>, keys: string[]) => Object.keys(value).sort().join() === keys.join()

export function isSafetyState(value: unknown): value is SafetyState {
  return record(value) && exact(value, KEYS)
    && (value.status === 'running' || value.status === 'stopped')
    && count(value.generation) && Number.isSafeInteger(value.revision) && (value.revision as number) > 0
    && typeof value.changed_at === 'string' && !Number.isNaN(Date.parse(value.changed_at))
    && (value.reason === null || (typeof value.reason === 'string' && value.reason.length <= 500))
    && record(value.in_flight) && exact(value.in_flight, FLIGHT) && FLIGHT.every((key) => count((value.in_flight as Record<string, unknown>)[key]))
}

export async function getSafety(signal?: AbortSignal): Promise<SafetyState> {
  const value = await request('/safety', { authenticated: true, signal })
  if (!isSafetyState(value)) throw new ApiError('invalid_response')
  return value
}

// O mesmo client_request_id em nova tentativa é replay: nunca aplica a parada duas vezes.
export async function sendSafetyCommand(
  body: { client_request_id: string; kind: SafetyKind; expected_revision: number; reason?: string },
  signal?: AbortSignal,
): Promise<SafetyState> {
  const value = await request('/safety/commands', { body, authenticated: true, signal })
  const expected = body.kind === 'stop' ? 'stopped' : 'running'
  // Sucesso só com o estado canônico pedido; replay pode já ter avançado além da revisão lida.
  if (!isSafetyState(value) || value.status !== expected || value.revision <= body.expected_revision) throw new ApiError('invalid_response')
  return value
}
