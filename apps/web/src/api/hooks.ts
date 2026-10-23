import { ApiError, record, request } from './client'

export interface RoutineHook { id: string; routine_id: string; status: 'active' | 'revoked'; min_interval_seconds: number; last_triggered_at: string | null; last_result: string | null; trigger_count: number; revision: number; created_at: string }
const base = (agentId: string, routineId: string) => `/agents/${encodeURIComponent(agentId)}/routines/${encodeURIComponent(routineId)}/hook`
const isHook = (value: unknown): value is RoutineHook => record(value) && typeof value.id === 'string' && Number.isSafeInteger(value.revision)

export async function getHook(agentId: string, routineId: string, signal?: AbortSignal): Promise<RoutineHook | null> {
  const value = await request(base(agentId, routineId), { authenticated: true, signal })
  if (!record(value) || !(value.hook === null || isHook(value.hook))) throw new ApiError('invalid_response')
  return value.hook as RoutineHook | null
}
export async function createHook(agentId: string, routineId: string, minInterval: number): Promise<{ hook: RoutineHook; url: string }> {
  const value = await request(base(agentId, routineId), { authenticated: true, body: { min_interval_seconds: minInterval } })
  if (!record(value) || !isHook(value.hook) || typeof value.url !== 'string') throw new ApiError('invalid_response')
  return { hook: value.hook, url: value.url }
}
export async function revokeHook(agentId: string, hook: RoutineHook) {
  const value = await request(`${base(agentId, hook.routine_id)}/revoke`, { authenticated: true, body: { expected_revision: hook.revision } })
  if (!record(value) || !isHook(value.hook)) throw new ApiError('invalid_response')
  return value.hook
}
