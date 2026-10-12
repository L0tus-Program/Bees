import { ApiError } from '../../api/client'
import { checkedObservation, instant } from '../../api/activity'
import type { SafetyInFlight, SafetyKind, SafetyState } from '../../api/safety'

export interface SafetyIntent { kind: SafetyKind; revision: number; requestId: string; reason: string; sent: boolean }

export const FLIGHT: (keyof SafetyInFlight)[] = ['model_calls', 'tool_actions', 'chat_reservations', 'provisioning_effects']

// Perda de resposta conserva o mesmo UUID: a nova tentativa é replay, nunca segunda parada.
export function safetyFailure(error: ApiError): 'lost' | 'conflict' | 'error' {
  if (error.code === 'mutation_network' || error.code === 'mutation_timeout' || error.status >= 500) return 'lost'
  if (error.status === 409 || error.code === 'invalid_response') return 'conflict'
  return 'error'
}

export const inFlightTotal = (state: SafetyState) => FLIGHT.reduce((sum, key) => sum + state.in_flight[key], 0)

export function checkedSafetySnapshot(incoming: SafetyState, previous: SafetyState | null) {
  if (previous) checkedObservation(incoming.snapshot, previous.snapshot)
  if (previous && (incoming.revision < previous.revision || incoming.generation < previous.generation || instant(incoming.changed_at) < instant(previous.changed_at)
    || incoming.revision === previous.revision && (incoming.status !== previous.status || incoming.generation !== previous.generation || incoming.reason !== previous.reason || incoming.changed_at !== previous.changed_at))) throw new ApiError('invalid_response')
  // in_flight is derived and may change without another human command.
  return incoming
}
