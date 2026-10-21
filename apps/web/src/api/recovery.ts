import { ApiError, record, request } from './client'
import { actionStatuses, taskStatuses } from './tasks'
import type { TaskStatus } from './tasks'
export interface RecoveryCounts {
  prepared: number; awaiting_approval: number; ready: number; dispatch_started: number; confirmed: number; failed_no_effect: number; outcome_unknown: number; cancelled: number
  unknown_acknowledged: number; unknown_unacknowledged: number; total: number
}
export interface RecoveryInventory {
  agent_id: string; task_id: string; task_revision: number; task_status: TaskStatus; observed_at: string
  scope: 'all_canonical_task_history'; executor_evidence: 'not_inspected'; runs_total: number
  actions: RecoveryCounts; model_calls: RecoveryCounts
}
const integer = (value: unknown): value is number => Number.isSafeInteger(value) && Number(value) >= 0
const uuid = (value: unknown): value is string => typeof value === 'string' && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(value) && value !== '00000000-0000-0000-0000-000000000000'
function counts(value: unknown): value is RecoveryCounts {
  if (!record(value) || Object.keys(value).length !== 11 || ![...actionStatuses, 'unknown_acknowledged', 'unknown_unacknowledged', 'total'].every(key => integer(value[key]))) return false
  const total = actionStatuses.reduce((sum, key) => sum + Number(value[key]), 0)
  return integer(total) && total === value.total && Number(value.unknown_acknowledged) + Number(value.unknown_unacknowledged) === value.outcome_unknown
}
export function checkedRecovery(value: unknown, agentId: string, taskId: string): RecoveryInventory {
  const keys = ['agent_id', 'task_id', 'task_revision', 'task_status', 'observed_at', 'scope', 'executor_evidence', 'runs_total', 'actions', 'model_calls']
  if (!record(value) || Object.keys(value).length !== keys.length || !keys.every(key => key in value)
    || !uuid(value.agent_id) || value.agent_id !== agentId || !uuid(value.task_id) || value.task_id !== taskId
    || !integer(value.task_revision) || value.task_revision < 1 || !taskStatuses.includes(value.task_status as TaskStatus)
    || typeof value.observed_at !== 'string' || !/^\d{4}-\d{2}-\d{2}T.*(?:Z|[+-]\d{2}:\d{2})$/.test(value.observed_at) || !Number.isFinite(Date.parse(value.observed_at))
    || value.scope !== 'all_canonical_task_history' || value.executor_evidence !== 'not_inspected' || !integer(value.runs_total)
    || !counts(value.actions) || !counts(value.model_calls) || value.model_calls.ready !== 0 || value.model_calls.awaiting_approval !== 0) throw new ApiError('invalid_response')
  return value as unknown as RecoveryInventory
}
export async function getRecoveryInventory(agentId: string, taskId: string, signal?: AbortSignal) {
  if (!uuid(agentId) || !uuid(taskId)) throw new ApiError('invalid_request')
  return checkedRecovery(await request(`/agents/${agentId}/tasks/${taskId}/recovery`, { authenticated: true, signal }), agentId, taskId)
}
