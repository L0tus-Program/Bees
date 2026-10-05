import { ApiError, record, request } from './client'

export const taskStatuses = ['queued', 'running', 'waiting_approval', 'waiting_resource', 'paused', 'completed', 'failed', 'cancelled'] as const
export const taskControls = ['pause', 'cancel', 'resume', 'redirect'] as const
export type TaskStatus = typeof taskStatuses[number]
export type TaskControl = typeof taskControls[number]
export interface TaskResult { content: string; created_at: string }
export interface TaskRun { id: string; status: TaskStatus; provider: string; model: string; started_at: string | null; finished_at: string | null; error_code: string | null; result: TaskResult | null }
export interface TaskRecord {
  id: string; agent_id: string; conversation_id: string | null; title: string; objective: string; expected_result: string
  status: TaskStatus; revision: number; created_at: string; updated_at: string; active_run_id: string | null
  control_requested: 'pause' | 'cancel' | null; available_controls: TaskControl[]; latest_run: TaskRun | null
  progress: { code: string; created_at: string } | null
}
export interface TaskEvent { id: string; code: string; created_at: string; run_id: string | null; content?: string | null }
export interface TaskCollection { tasks: TaskRecord[]; has_more: boolean; worker?: { available: boolean; last_seen: string | null } }
export interface TaskDetail { task: TaskRecord; events: TaskEvent[] }
export interface NewTask { client_request_id: string; conversation_id?: string; title: string; objective: string; expected_result: string; max_calls?: number; max_active_seconds?: number }
export interface TaskCommand { client_request_id: string; expected_revision: number; action: TaskControl; instruction?: string; acknowledge_unknown?: boolean }

const nullableString = (value: unknown) => value === null || typeof value === 'string'
const dateString = (value: unknown): value is string => typeof value === 'string' && !Number.isNaN(Date.parse(value))
const nullableDate = (value: unknown) => value === null || dateString(value)
const isStatus = (value: unknown) => taskStatuses.includes(value as TaskStatus)
const isResult = (value: unknown) => value === null || (record(value) && typeof value.content === 'string' && dateString(value.created_at))
function isRun(value: unknown) {
  return value === null || (record(value) && ['id', 'provider', 'model'].every((key) => typeof value[key] === 'string')
    && isStatus(value.status) && nullableDate(value.started_at) && nullableDate(value.finished_at)
    && nullableString(value.error_code) && isResult(value.result))
}
export function isTask(value: unknown): value is TaskRecord {
  return record(value) && ['id', 'agent_id', 'title', 'objective', 'expected_result'].every((key) => typeof value[key] === 'string')
    && nullableString(value.conversation_id) && nullableString(value.active_run_id) && isStatus(value.status)
    && Number.isInteger(value.revision) && (value.revision as number) > 0 && dateString(value.created_at) && dateString(value.updated_at)
    && [null, 'pause', 'cancel'].includes(value.control_requested as null | string)
    && Array.isArray(value.available_controls) && value.available_controls.every((control) => taskControls.includes(control))
    && isRun(value.latest_run) && (value.progress === null || (record(value.progress) && typeof value.progress.code === 'string' && dateString(value.progress.created_at)))
}
const base = (agentId: string) => `/agents/${encodeURIComponent(agentId)}/tasks`
function checkedTask(value: unknown, agentId: string, taskId?: string): TaskRecord {
  if (!isTask(value) || value.agent_id !== agentId || (taskId !== undefined && value.id !== taskId)) throw new ApiError('invalid_response')
  return value
}
export async function getTasks(agentId: string, signal?: AbortSignal): Promise<TaskCollection> {
  const value = await request(base(agentId), { authenticated: true, signal })
  if (!record(value) || !Array.isArray(value.tasks) || typeof value.has_more !== 'boolean') throw new ApiError('invalid_response')
  if (value.worker !== undefined && (!record(value.worker) || typeof value.worker.available !== 'boolean' || !nullableDate(value.worker.last_seen))) throw new ApiError('invalid_response')
  const tasks = value.tasks.map((task) => checkedTask(task, agentId))
  if (new Set(tasks.map((task) => task.id)).size !== tasks.length) throw new ApiError('invalid_response')
  return { tasks, has_more: value.has_more, ...(value.worker !== undefined ? { worker: value.worker as TaskCollection['worker'] } : {}) }
}
export async function getTask(agentId: string, taskId: string, signal?: AbortSignal): Promise<TaskDetail> {
  const value = await request(`${base(agentId)}/${encodeURIComponent(taskId)}`, { authenticated: true, signal })
  if (!record(value) || !Array.isArray(value.events) || !value.events.every((event) => record(event) && typeof event.id === 'string'
    && typeof event.code === 'string' && dateString(event.created_at) && nullableString(event.run_id)
    && (event.content === undefined || nullableString(event.content)))) throw new ApiError('invalid_response')
  return { task: checkedTask(value.task, agentId, taskId), events: value.events as TaskEvent[] }
}
export async function createTask(agentId: string, body: NewTask, signal?: AbortSignal): Promise<TaskRecord> {
  return checkedTask(await request(base(agentId), { body, authenticated: true, signal }), agentId)
}
export async function controlTask(agentId: string, taskId: string, body: TaskCommand, signal?: AbortSignal): Promise<TaskRecord> {
  return checkedTask(await request(`${base(agentId)}/${encodeURIComponent(taskId)}/control`, { body, authenticated: true, signal }), agentId, taskId)
}
