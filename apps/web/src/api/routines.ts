import { ApiError, record, request } from './client'
import { exact, instant, timestamp, uuid } from './activity'

export type RoutineStatus = 'active' | 'paused' | 'cancelled'
export interface RoutineProposalSource { conversation_id: string; message_id: string; content_hash: string }
export interface RoutineDefinition {
  execution_mode?: 'prepare' | 'scheduled' | 'agent'
  runtime_binding?: { installation_id: string; resource_id: string; plugin_id: string; manifest_hash: string; tool_version: '1' } | null
  name: string; instructions: string
  schedule: { frequency: 'daily' | 'weekly'; time: string; weekdays: number[] }
  timezone: string; skill_id: string; skill_version: number; inputs: Record<string, string>
  environment: 'none' | 'own' | 'personal'
  destination: { kind: 'conversation'; conversation_id: string }
  notification: 'always' | 'failures' | 'none'; grace_seconds: number
  misfire_policy: 'skip'; overlap_policy: 'skip'
}
export interface Routine {
  id: string; agent_id: string; name: string; instructions: string; schedule: Record<string, unknown>
  timezone: string; next_run_at: string | null; status: RoutineStatus
  misfire_policy: 'skip' | 'catch_up'; overlap_policy: 'skip' | 'queue'
  created_at: string; updated_at: string; revision: number; metadata: Record<string, unknown>
  definition: RoutineDefinition | null; origin: 'human' | 'legacy'
}
export interface RoutineOccurrence {
  id: string; routine_id: string; routine_revision: number; scheduled_at: string; window_end: string
  status: 'pending' | 'running' | 'completed' | 'failed' | 'cancelled' | 'skipped' | 'outcome_unknown'
  reason: string; task_id: string | null; revision: number; created_at: string; updated_at: string; metadata: Record<string, unknown>
}
export interface RoutineRevision { routine_id: string; revision: number; definition: RoutineDefinition; origin: 'human'; created_at: string }
export interface RoutineIntent {
  agentId: string; routineId?: string; action: 'create' | 'revise' | 'status'
  body: { client_request_id: string; expected_revision?: number; definition?: RoutineDefinition; status?: RoutineStatus; proposal_source?: RoutineProposalSource }
}
const positive = (value: unknown): value is number => Number.isSafeInteger(value) && (value as number) > 0
const text = (value: unknown, max: number): value is string => typeof value === 'string' && value.trim().length > 0 && Array.from(value).length <= max
const zone = (value: unknown): value is string => { if (typeof value !== 'string') return false; try { new Intl.DateTimeFormat('en', { timeZone: value }); return true } catch { return false } }
const status = (value: unknown): value is RoutineStatus => ['active', 'paused', 'cancelled'].includes(String(value))
const secret = /(?:sk-(?:or-v1-)?[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,}|Bearer\s+[A-Za-z0-9_.-]{20,}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----)/i
const nil = '00000000-0000-0000-0000-000000000000'
const commandId = (value: unknown): value is string => uuid(value) && value !== nil
export function isRoutineProposalSource(value: unknown): value is RoutineProposalSource {
  return exact(value, ['conversation_id', 'message_id', 'content_hash']) && commandId(value.conversation_id) && commandId(value.message_id) && typeof value.content_hash === 'string' && /^[0-9a-f]{64}$/.test(value.content_hash)
}
export function isRoutineDefinition(value: unknown): value is RoutineDefinition {
  const keys = ['name', 'instructions', 'schedule', 'timezone', 'skill_id', 'skill_version', 'inputs', 'environment', 'destination', 'notification', 'grace_seconds', 'misfire_policy', 'overlap_policy']
  if (!record(value) || !keys.every((key) => Object.hasOwn(value, key)) || Object.keys(value).some((key) => ![...keys, 'execution_mode', 'runtime_binding'].includes(key))) return false
  if (value.runtime_binding !== undefined && value.runtime_binding !== null && (!exact(value.runtime_binding, ['installation_id', 'resource_id', 'plugin_id', 'manifest_hash', 'tool_version']) || !commandId(value.runtime_binding.installation_id) || !commandId(value.runtime_binding.resource_id) || !commandId(value.runtime_binding.plugin_id) || typeof value.runtime_binding.manifest_hash !== 'string' || !/^[0-9a-f]{64}$/.test(value.runtime_binding.manifest_hash) || value.runtime_binding.tool_version !== '1')) return false
  if (('execution_mode' in value && !['prepare', 'scheduled', 'agent'].includes(String(value.execution_mode)))
    || !text(value.name, 200) || !text(value.instructions, 4096) || !zone(value.timezone) || !uuid(value.skill_id) || !positive(value.skill_version)
    || !exact(value.schedule, ['frequency', 'time', 'weekdays']) || !['daily', 'weekly'].includes(String(value.schedule.frequency)) || typeof value.schedule.time !== 'string' || !/^(?:[01][0-9]|2[0-3]):[0-5][0-9]$/.test(value.schedule.time)
    || !Array.isArray(value.schedule.weekdays) || value.schedule.weekdays.length > 7 || !value.schedule.weekdays.every((day) => Number.isInteger(day) && day >= 0 && day <= 6) || new Set(value.schedule.weekdays).size !== value.schedule.weekdays.length
    || (value.schedule.frequency === 'weekly') !== (value.schedule.weekdays.length > 0)
    || !record(value.inputs) || Object.keys(value.inputs).length > 20 || !Object.entries(value.inputs).every(([name, input]) => /^[a-z][a-z0-9_.-]{0,63}$/.test(name) && text(input, 2048))
    || !['none', 'own', 'personal'].includes(String(value.environment)) || !exact(value.destination, ['kind', 'conversation_id']) || value.destination.kind !== 'conversation' || !uuid(value.destination.conversation_id)
    || !['always', 'failures', 'none'].includes(String(value.notification)) || !Number.isSafeInteger(value.grace_seconds) || (value.grace_seconds as number) < 0 || (value.grace_seconds as number) > 3600
    || value.misfire_policy !== 'skip' || value.overlap_policy !== 'skip') return false
  const bounded = { ...value }
  if ((value.execution_mode ?? 'prepare') === 'prepare' && value.runtime_binding == null) {
    delete bounded.execution_mode; delete bounded.runtime_binding
  }
  const raw = JSON.stringify(bounded)
  return new TextEncoder().encode(raw).byteLength <= 32768 && !secret.test(raw)
}
export function checkedRoutine(value: unknown, agentId: string, routineId?: string): Routine {
  if (!exact(value, ['id', 'agent_id', 'name', 'instructions', 'schedule', 'timezone', 'next_run_at', 'status', 'misfire_policy', 'overlap_policy', 'created_at', 'updated_at', 'revision', 'metadata', 'definition', 'origin'])
    || !uuid(value.id) || value.agent_id !== agentId || routineId !== undefined && value.id !== routineId || !text(value.name, 200) || !(typeof value.instructions === 'string' && value.instructions.trim()) || !record(value.schedule) || !record(value.metadata)
    || !zone(value.timezone) || !(value.next_run_at === null || timestamp(value.next_run_at)) || !status(value.status) || !positive(value.revision) || !timestamp(value.created_at) || !timestamp(value.updated_at) || instant(value.updated_at) < instant(value.created_at)
    || !['skip', 'catch_up'].includes(String(value.misfire_policy)) || !['skip', 'queue'].includes(String(value.overlap_policy))
    || !(value.origin === 'legacy' && value.definition === null || value.origin === 'human' && isRoutineDefinition(value.definition))) throw new ApiError('invalid_response')
  const result = value as unknown as Routine
  if (result.definition) {
    const definition = result.definition
    if (definition.name !== result.name || definition.instructions !== result.instructions || definition.timezone !== result.timezone || definition.misfire_policy !== result.misfire_policy || definition.overlap_policy !== result.overlap_policy
      || !exact(result.schedule, ['frequency', 'time', 'weekdays']) || result.schedule.frequency !== definition.schedule.frequency || result.schedule.time !== definition.schedule.time || JSON.stringify(result.schedule.weekdays) !== JSON.stringify(definition.schedule.weekdays)
      || result.status !== 'active' && result.next_run_at !== null) throw new ApiError('invalid_response')
  }
  return result
}
function base(agentId: string) { if (!uuid(agentId)) throw new ApiError('invalid_request'); return `/agents/${agentId}/routines` }
function page(offset: number) { if (!Number.isSafeInteger(offset) || offset < 0 || offset > 2 ** 31 - 1) throw new ApiError('invalid_request'); return `?limit=50&offset=${offset}` }
export async function listRoutines(agentId: string, offset = 0, signal?: AbortSignal): Promise<Routine[]> {
  const value = await request(base(agentId) + page(offset), { authenticated: true, signal })
  if (!Array.isArray(value) || value.length > 50) throw new ApiError('invalid_response')
  const items = value.map((item) => checkedRoutine(item, agentId))
  if (new Set(items.map((item) => item.id)).size !== items.length) throw new ApiError('invalid_response')
  return items
}
export async function getRoutine(agentId: string, routineId: string, signal?: AbortSignal) {
  if (!uuid(routineId)) throw new ApiError('invalid_request')
  return checkedRoutine(await request(`${base(agentId)}/${routineId}`, { authenticated: true, signal }), agentId, routineId)
}
export async function routineOccurrences(agentId: string, routineId: string, offset = 0, signal?: AbortSignal): Promise<RoutineOccurrence[]> {
  if (!uuid(routineId)) throw new ApiError('invalid_request')
  const value = await request(`${base(agentId)}/${routineId}/occurrences${page(offset)}`, { authenticated: true, signal })
  if (!Array.isArray(value) || value.length > 50 || !value.every((item) => exact(item, ['id', 'routine_id', 'routine_revision', 'scheduled_at', 'window_end', 'status', 'reason', 'task_id', 'revision', 'created_at', 'updated_at', 'metadata'])
    && uuid(item.id) && item.routine_id === routineId && positive(item.routine_revision) && timestamp(item.scheduled_at) && timestamp(item.window_end) && instant(item.window_end) >= instant(item.scheduled_at)
    && ['pending', 'running', 'completed', 'failed', 'cancelled', 'skipped', 'outcome_unknown'].includes(String(item.status)) && (item.status === 'skipped' || instant(item.window_end) === instant(item.scheduled_at))
    && typeof item.reason === 'string' && /^[a-z][a-z0-9_]{0,63}$/.test(item.reason) && (item.task_id === null || uuid(item.task_id)) && positive(item.revision) && timestamp(item.created_at) && timestamp(item.updated_at) && instant(item.updated_at) >= instant(item.created_at) && record(item.metadata))) throw new ApiError('invalid_response')
  const items = value as RoutineOccurrence[]
  if (new Set(items.map((item) => item.id)).size !== items.length || items.some((item, index) => index > 0 && instant(item.scheduled_at) > instant(items[index - 1].scheduled_at))) throw new ApiError('invalid_response')
  return items
}
export async function routineRevisions(agentId: string, routineId: string, offset = 0, signal?: AbortSignal): Promise<RoutineRevision[]> {
  if (!uuid(routineId)) throw new ApiError('invalid_request')
  const value = await request(`${base(agentId)}/${routineId}/revisions${page(offset)}`, { authenticated: true, signal })
  if (!Array.isArray(value) || value.length > 50 || !value.every((item) => exact(item, ['routine_id', 'revision', 'definition', 'origin', 'created_at']) && item.routine_id === routineId && positive(item.revision) && isRoutineDefinition(item.definition) && item.origin === 'human' && timestamp(item.created_at))
    || value.some((item, index) => index > 0 && item.revision >= value[index - 1].revision)) throw new ApiError('invalid_response')
  return value as RoutineRevision[]
}
export function definitionIntent(agentId: string, definition: RoutineDefinition, previous?: Routine, proposalSource?: RoutineProposalSource): RoutineIntent {
  if (!isRoutineDefinition(definition) || previous && previous.agent_id !== agentId || proposalSource && (previous !== undefined || !isRoutineProposalSource(proposalSource))) throw new ApiError('invalid_request')
  base(agentId)
  return { agentId, ...(previous ? { routineId: previous.id } : {}), action: previous ? 'revise' : 'create', body: { client_request_id: crypto.randomUUID(), definition, ...(previous ? { expected_revision: previous.revision } : {}), ...(proposalSource ? { proposal_source: proposalSource } : {}) } }
}
export function routineStatusIntent(routine: Routine, value: RoutineStatus): RoutineIntent {
  if (!status(value) || routine.origin !== 'human' || routine.status === 'cancelled') throw new ApiError('invalid_request')
  return { agentId: routine.agent_id, routineId: routine.id, action: 'status', body: { client_request_id: crypto.randomUUID(), expected_revision: routine.revision, status: value } }
}
export function checkedRoutineIntent(value: unknown, agentId: string): RoutineIntent {
  if (!record(value) || value.agentId !== agentId || !uuid(agentId) || !['create', 'revise', 'status'].includes(String(value.action)) || !record(value.body) || !commandId(value.body.client_request_id)) throw new ApiError('invalid_request')
  if (value.action === 'create') {
    const hasSource = Object.hasOwn(value.body, 'proposal_source')
    const keys = hasSource ? ['client_request_id', 'definition', 'proposal_source'] : ['client_request_id', 'definition']
    if (!exact(value, ['agentId', 'action', 'body']) || !exact(value.body, keys) || hasSource && !isRoutineProposalSource(value.body.proposal_source) || !isRoutineDefinition(value.body.definition)) throw new ApiError('invalid_request')
  } else if (!exact(value, ['agentId', 'routineId', 'action', 'body']) || !uuid(value.routineId) || !positive(value.body.expected_revision)
    || (value.action === 'status' ? !exact(value.body, ['client_request_id', 'expected_revision', 'status']) || !status(value.body.status) : !exact(value.body, ['client_request_id', 'expected_revision', 'definition']) || !isRoutineDefinition(value.body.definition))) throw new ApiError('invalid_request')
  return value as unknown as RoutineIntent
}
export async function sendRoutine(raw: RoutineIntent, signal?: AbortSignal) {
  const intent = checkedRoutineIntent(raw, raw.agentId)
  const path = intent.action === 'create' ? base(intent.agentId) : `${base(intent.agentId)}/${intent.routineId}/${intent.action === 'status' ? 'status' : 'revisions'}`
  const value = checkedRoutine(await request(path, { body: intent.body, ...(intent.action === 'status' ? { method: 'PUT' as const } : {}), authenticated: true, signal }), intent.agentId, intent.routineId)
  if (value.origin !== 'human' || value.revision !== (intent.body.expected_revision ?? 0) + 1 || value.status !== (intent.body.status ?? 'paused')) throw new ApiError('invalid_response')
  if (intent.body.proposal_source) {
    const source = value.metadata.proposal_source, expected = intent.body.proposal_source
    if (!isRoutineProposalSource(source) || source.conversation_id !== expected.conversation_id || source.message_id !== expected.message_id || source.content_hash !== expected.content_hash) throw new ApiError('invalid_response')
  }
  return value
}

export async function routineRuntime(agentId: string, signal?: AbortSignal, mode: 'scheduled' | 'agent' = 'scheduled'): Promise<{available: boolean; reason: string | null; manual_available: boolean}> {
  if (!['scheduled', 'agent'].includes(mode)) throw new ApiError('invalid_request')
  const value = await request(`${base(agentId)}/runtime${mode === 'agent' ? '?execution_mode=agent' : ''}`, { authenticated: true, signal })
  if (!(exact(value, ['available', 'reason', 'version', 'max_calls', 'max_active_seconds']) || exact(value, ['available', 'reason', 'version', 'max_calls', 'max_active_seconds', 'manual_available'])) || typeof value.available !== 'boolean' || value.available !== (value.reason === null) || !(value.reason === null || ['worker_unavailable', 'resource_unavailable', 'tool_unavailable'].includes(String(value.reason))) || value.version !== 1 || value.max_calls !== 4 || value.max_active_seconds !== 120 || !(value.manual_available === undefined || typeof value.manual_available === 'boolean' && (!value.manual_available || value.available))) throw new ApiError('invalid_response')
  return {available: value.available, reason: value.reason as string | null, manual_available: value.manual_available === true}
}
