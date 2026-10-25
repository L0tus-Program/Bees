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

export interface RecoveryAction {
  id: string; run_id: string; revision: number; tool_name: string; status: typeof actionStatuses[number]
  created_at: string; updated_at: string; unknown_acknowledged: boolean
  executor_command: 'queued' | 'sent' | 'confirmed' | 'failed' | 'unknown' | 'expired' | null; executor_code: string | null
}
export interface RecoveryActionPage {
  agent_id: string; task_id: string; task_revision: number; observed_at: string
  scope: 'all_canonical_task_actions'; executor_evidence: 'not_inspected'
  items: RecoveryAction[]; has_more: boolean; next_cursor: string | null
}
const date = (value: unknown): value is string => typeof value === 'string' && /^\d{4}-\d{2}-\d{2}T.*(?:Z|[+-]\d{2}:\d{2})$/.test(value) && Number.isFinite(Date.parse(value))
const executorCommands = ['queued', 'sent', 'confirmed', 'failed', 'unknown', 'expired'] as const

export function checkedRecoveryActions(value: unknown, agentId: string, taskId: string, limit = 50): RecoveryActionPage {
  const keys = ['agent_id', 'task_id', 'task_revision', 'observed_at', 'scope', 'executor_evidence', 'items', 'has_more', 'next_cursor']
  if (!record(value) || Object.keys(value).length !== keys.length || !keys.every(key => key in value)
    || !uuid(value.agent_id) || value.agent_id !== agentId || !uuid(value.task_id) || value.task_id !== taskId
    || !integer(value.task_revision) || value.task_revision < 1 || !date(value.observed_at)
    || value.scope !== 'all_canonical_task_actions' || value.executor_evidence !== 'not_inspected'
    || !Array.isArray(value.items) || value.items.length > limit || typeof value.has_more !== 'boolean') throw new ApiError('invalid_response')
  const seen = new Set<string>()
  for (const item of value.items) {
    const command = item && (item as Record<string, unknown>).executor_command
    const code = item && (item as Record<string, unknown>).executor_code
    if (!record(item) || Object.keys(item).length !== 10 || !uuid(item.id)
      || !(command === null || executorCommands.includes(command as typeof executorCommands[number])) || !(code === null || (typeof code === 'string' && code.length <= 64)) || !uuid(item.run_id) || seen.has(item.id)
      || !integer(item.revision) || item.revision < 1 || typeof item.tool_name !== 'string' || item.tool_name.trim().length === 0 || Array.from(item.tool_name).length > 200
      || !actionStatuses.includes(item.status as typeof actionStatuses[number]) || !date(item.created_at) || !date(item.updated_at) || typeof item.unknown_acknowledged !== 'boolean') throw new ApiError('invalid_response')
    seen.add(item.id)
  }
  if (value.has_more ? value.items.length !== limit || !uuid(value.next_cursor) || value.next_cursor !== value.items.at(-1)?.id : value.next_cursor !== null) throw new ApiError('invalid_response')
  return value as unknown as RecoveryActionPage
}
export async function getRecoveryActions(agentId: string, taskId: string, after: string | null = null, signal?: AbortSignal): Promise<RecoveryActionPage> {
  if (!uuid(agentId) || !uuid(taskId) || after !== null && !uuid(after)) throw new ApiError('invalid_request')
  const query = new URLSearchParams({ limit: '50' }); if (after !== null) query.set('after', after)
  return checkedRecoveryActions(await request(`/agents/${agentId}/tasks/${taskId}/recovery/actions?${query}`, { authenticated: true, signal }), agentId, taskId)
}
