import { ApiError, request } from './client'
import { exact, uuid } from './activity'
import { isTask } from './tasks'
import type { TaskRecord } from './tasks'

export interface ToolTaskBody { client_request_id: string; title: string; objective: string; expected_result: string; max_calls: 4; max_active_seconds: 120; deliverables: [] }
export interface ToolTaskIntent { agentId: string; body: ToolTaskBody }
export interface ToolTaskCapabilities { available: boolean; reason: 'worker_unavailable' | 'resource_unavailable' | 'tool_unavailable' | null; tool_name: 'normalize_text'; max_calls: 4; max_active_seconds: 120; max_actions: 16 }
const identity = (value: unknown): value is string => uuid(value) && value === value.toLowerCase() && value !== '00000000-0000-0000-0000-000000000000'
const text = (value: unknown, limit: number, required = true): value is string => typeof value === 'string' && (!required || value.trim().length > 0) && !value.includes('\0') && Array.from(value).length <= limit
function canonical(value: unknown): string {
  if (Array.isArray(value)) return '[' + value.map(canonical).join(',') + ']'
  if (typeof value === 'object' && value !== null) return '{' + Object.entries(value).sort(([a], [b]) => a < b ? -1 : 1).map(([key, item]) => JSON.stringify(key) + ':' + canonical(item)).join(',') + '}'
  return JSON.stringify(value)
}
async function digest(value: unknown) {
  return Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(canonical(value)))), (byte) => byte.toString(16).padStart(2, '0')).join('')
}
export function checkedToolTaskIntent(value: unknown, agentId: string): ToolTaskIntent {
  if (!identity(agentId) || !exact(value, ['agentId', 'body']) || value.agentId !== agentId || !exact(value.body, ['client_request_id', 'title', 'objective', 'expected_result', 'max_calls', 'max_active_seconds', 'deliverables'])
    || !identity(value.body.client_request_id) || !text(value.body.title, 200) || value.body.title !== value.body.title.trim() || !text(value.body.objective, 12000) || value.body.objective !== value.body.objective.trim()
    || !text(value.body.expected_result, 4000, false) || value.body.max_calls !== 4 || value.body.max_active_seconds !== 120 || !Array.isArray(value.body.deliverables) || value.body.deliverables.length !== 0
    || new TextEncoder().encode(JSON.stringify(value.body)).byteLength > 65536) throw new ApiError('invalid_request')
  return value as unknown as ToolTaskIntent
}
export async function getToolTaskCapabilities(agentId: string, signal?: AbortSignal): Promise<ToolTaskCapabilities> {
  if (!identity(agentId)) throw new ApiError('invalid_request')
  const value = await request(`/agents/${agentId}/tool-task-capabilities`, { authenticated: true, signal })
  if (!exact(value, ['available', 'reason', 'tool_name', 'max_calls', 'max_active_seconds', 'max_actions']) || typeof value.available !== 'boolean' || value.available !== (value.reason === null)
    || !(value.reason === null || ['worker_unavailable', 'resource_unavailable', 'tool_unavailable'].includes(String(value.reason))) || value.tool_name !== 'normalize_text' || value.max_calls !== 4 || value.max_active_seconds !== 120 || value.max_actions !== 16) throw new ApiError('invalid_response')
  return value as unknown as ToolTaskCapabilities
}
export async function createToolTask(intent: ToolTaskIntent, signal?: AbortSignal): Promise<TaskRecord> {
  checkedToolTaskIntent(intent, intent.agentId)
  const value = await request(`/agents/${intent.agentId}/tool-tasks`, { body: intent.body, authenticated: true, signal })
  if (!exact(value, ['task', 'client_request_id', 'request_hash']) || value.client_request_id !== intent.body.client_request_id || value.request_hash !== await digest(intent.body)
    || !isTask(value.task) || value.task.agent_id !== intent.agentId || !identity(value.task.id) || !identity(value.task.conversation_id) || !identity(value.task.active_run_id)
    || value.task.title !== intent.body.title || value.task.objective !== intent.body.objective || value.task.expected_result !== intent.body.expected_result
    || value.task.tool_approvals_available !== true || value.task.model_approvals_available !== true || value.task.max_calls !== 4 || value.task.max_active_seconds !== 120) throw new ApiError('invalid_response')
  return value.task
}
