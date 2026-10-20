import { ApiError, request } from './client'
import { exact, instant, timestamp, uuid } from './activity'
import { digest } from './toolTasks'
import type { Routine, RoutineOccurrence } from './routines'

export interface ManualRoutineIntent { agentId: string; routineId: string; body: { client_request_id: string; expected_revision: number } }
export interface ManualRoutineReceipt { client_request_id: string; request_hash: string; agent_id: string; routine_id: string; routine_revision: number; runtime_binding: { installation_id: string; resource_id: string; plugin_id: string; manifest_hash: string; tool_version: '1' }; occurrence: RoutineOccurrence }
const identity = (value: unknown): value is string => uuid(value) && value === value.toLowerCase() && value !== '00000000-0000-0000-0000-000000000000'
export function checkedManualIntent(value: unknown, agentId: string): ManualRoutineIntent {
  if (!exact(value, ['agentId', 'routineId', 'body']) || !identity(agentId) || value.agentId !== agentId || !identity(value.routineId)
    || !exact(value.body, ['client_request_id', 'expected_revision']) || !identity(value.body.client_request_id) || !Number.isSafeInteger(value.body.expected_revision) || Number(value.body.expected_revision) < 1) throw new ApiError('invalid_request')
  return value as unknown as ManualRoutineIntent
}
export function manualRoutineIntent(routine: Routine): ManualRoutineIntent {
  if (!routine.definition || routine.origin !== 'human' || routine.status === 'cancelled') throw new ApiError('invalid_request')
  return checkedManualIntent({ agentId: routine.agent_id, routineId: routine.id, body: { client_request_id: crypto.randomUUID(), expected_revision: routine.revision } }, routine.agent_id)
}
async function checkedReceipt(value: unknown, intent: ManualRoutineIntent): Promise<ManualRoutineReceipt> {
  if (!exact(value, ['client_request_id', 'request_hash', 'agent_id', 'routine_id', 'routine_revision', 'runtime_binding', 'occurrence']) || value.client_request_id !== intent.body.client_request_id || value.request_hash !== await digest(intent.body)
    || value.agent_id !== intent.agentId || value.routine_id !== intent.routineId || value.routine_revision !== intent.body.expected_revision
    || !exact(value.runtime_binding, ['installation_id', 'resource_id', 'plugin_id', 'manifest_hash', 'tool_version']) || !identity(value.runtime_binding.installation_id) || !identity(value.runtime_binding.resource_id) || !identity(value.runtime_binding.plugin_id) || typeof value.runtime_binding.manifest_hash !== 'string' || !/^[0-9a-f]{64}$/.test(value.runtime_binding.manifest_hash) || value.runtime_binding.tool_version !== '1'
    || !exact(value.occurrence, ['id', 'routine_id', 'routine_revision', 'scheduled_at', 'window_end', 'status', 'reason', 'task_id', 'revision', 'created_at', 'updated_at', 'metadata'])
    || !identity(value.occurrence.id) || value.occurrence.routine_id !== intent.routineId || value.occurrence.routine_revision !== intent.body.expected_revision || !identity(value.occurrence.task_id)
    || value.occurrence.status !== 'running' || value.occurrence.reason !== 'task_created' || value.occurrence.revision !== 2 || !timestamp(value.occurrence.scheduled_at) || !timestamp(value.occurrence.window_end) || instant(value.occurrence.scheduled_at) !== instant(value.occurrence.window_end)
    || !timestamp(value.occurrence.created_at) || !timestamp(value.occurrence.updated_at) || instant(value.occurrence.updated_at) < instant(value.occurrence.created_at)
    || !exact(value.occurrence.metadata, ['origin', 'task_status']) || value.occurrence.metadata.origin !== 'manual' || value.occurrence.metadata.task_status !== 'queued') throw new ApiError('invalid_response')
  return value as unknown as ManualRoutineReceipt
}
const endpoint = (intent: ManualRoutineIntent) => `/agents/${intent.agentId}/routines/${intent.routineId}/manual`
export async function executeManualRoutine(raw: ManualRoutineIntent, signal?: AbortSignal) {
  const intent = checkedManualIntent(raw, raw.agentId)
  return checkedReceipt(await request(endpoint(intent), { body: intent.body, authenticated: true, signal }), intent)
}
export async function getManualRoutineReceipt(raw: ManualRoutineIntent, signal?: AbortSignal) {
  const intent = checkedManualIntent(raw, raw.agentId)
  return checkedReceipt(await request(`${endpoint(intent)}/${intent.body.client_request_id}`, { authenticated: true, signal }), intent)
}
const key = (agentId: string) => `bees.routine-manual.pending.v1.${agentId}`
export function restoreManualRoutine(agentId: string): { intent: ManualRoutineIntent | null; corrupt: boolean } {
  try {
    const raw = sessionStorage.getItem(key(agentId))
    if (!raw) return { intent: null, corrupt: false }
    try { return { intent: checkedManualIntent(JSON.parse(raw), agentId), corrupt: false } } catch { return { intent: null, corrupt: true } }
  } catch { return { intent: null, corrupt: false } }
}
export function preserveManualRoutine(intent: ManualRoutineIntent) { sessionStorage.setItem(key(intent.agentId), JSON.stringify(checkedManualIntent(intent, intent.agentId))) }
export function clearManualRoutine(agentId: string) { sessionStorage.removeItem(key(agentId)) }
