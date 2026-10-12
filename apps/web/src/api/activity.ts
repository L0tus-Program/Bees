import { ApiError, record, request } from './client'

export interface Observation { event_seq: number; observed_at: string }
export interface Interval { since: string; until: string }
export interface ActivityScope { task_id: string | null; run_id: string | null }
export interface ActivityUsage {
  counted_tokens: number; reported_tokens: number; estimated_tokens: number; reserved_tokens: number; unknown_tokens: number
  released_entries: number; entries: number; open_reservations: number; unknown_entries: number; estimate_methods: string[]
}
export interface ActivityOperations { model_calls_in_flight: number; tool_actions_in_flight: number; chat_reservations: number; unknown_model_calls: number; unknown_tool_actions: number; waiting_approval: number; waiting_resource: number }
export interface ActivityItem {
  id: string; seq: number; occurred_at: string; entity_type: 'task' | 'run' | 'model_call' | 'action' | 'approval' | 'task_command' | 'usage_entry'
  entity_id: string; task_id: string | null; task_title: string | null; run_id: string | null; action_id: string | null
  status: string; revision: number; error_code: string | null; environment: { kind: 'textual' | 'unassociated'; environment_id: null }
  usage: { status: 'reserved' | 'confirmed' | 'unknown' | 'released'; source: 'chat' | 'task'; usage_kind: 'reported' | 'estimated' | 'unknown' | null; reserved_tokens: number; counted_tokens: number; input_tokens: number | null; output_tokens: number | null; total_tokens: number | null; estimate_method: string } | null
}
export interface ActivityPage { agent_id: string; snapshot: Observation; scope: ActivityScope; interval: Interval; unit: 'tokens'; usage: ActivityUsage; operations: ActivityOperations; items: ActivityItem[]; next_before: number | null }
export interface ActivityQuery { task_id?: string; run_id?: string; since?: string; until?: string; before?: number; snapshot_seq?: number; window_seconds?: number }

export const uuid = (value: unknown): value is string => typeof value === 'string' && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(value)
export const count = (value: unknown): value is number => Number.isSafeInteger(value) && (value as number) >= 0
export const timestamp = (value: unknown): value is string => typeof value === 'string' && /^\d{4}-\d{2}-\d{2}T.*(?:Z|[+-]\d{2}:\d{2})$/.test(value) && Number.isFinite(Date.parse(value))
export const exact = (value: unknown, keys: string[]): value is Record<string, unknown> => record(value) && Object.keys(value).sort().join() === [...keys].sort().join()
export function isObservation(value: unknown): value is Observation { return exact(value, ['event_seq', 'observed_at']) && count(value.event_seq) && timestamp(value.observed_at) }
export function instant(value: string) {
  // SQLite/Python timestamps preserve microseconds; Date.parse alone drops that precision.
  const fraction = value.match(/\.(\d+)(?:Z|[+-]\d{2}:\d{2})$/)?.[1] ?? ''
  return BigInt(Date.parse(value)) * 1_000_000n + BigInt(fraction.slice(3, 9).padEnd(6, '0'))
}
export function isInterval(value: unknown): value is Interval { return exact(value, ['since', 'until']) && timestamp(value.since) && timestamp(value.until) && instant(value.since) < instant(value.until) }
export function checkedObservation(incoming: Observation, previous: Observation | null) {
  if (previous && (incoming.event_seq < previous.event_seq || instant(incoming.observed_at) < instant(previous.observed_at))) throw new ApiError('invalid_response')
}
const word = (value: unknown): value is string => typeof value === 'string' && /^[a-z][a-z0-9_]{0,63}$/.test(value)
const nullableUuid = (value: unknown) => value === null || uuid(value)
const nullableCount = (value: unknown) => value === null || count(value)
const usageKeys = ['counted_tokens', 'reported_tokens', 'estimated_tokens', 'reserved_tokens', 'unknown_tokens', 'released_entries', 'entries', 'open_reservations', 'unknown_entries', 'estimate_methods']
const operationKeys = ['model_calls_in_flight', 'tool_actions_in_flight', 'chat_reservations', 'unknown_model_calls', 'unknown_tool_actions', 'waiting_approval', 'waiting_resource']
const itemKeys = ['id', 'seq', 'occurred_at', 'entity_type', 'entity_id', 'task_id', 'task_title', 'run_id', 'action_id', 'status', 'revision', 'error_code', 'environment', 'usage']
const taskStatuses = ['queued', 'running', 'paused', 'waiting_resource', 'waiting_approval', 'completed', 'failed', 'cancelled']
const statuses: Record<ActivityItem['entity_type'], string[]> = {
  task: taskStatuses, run: taskStatuses,
  model_call: ['prepared', 'dispatch_started', 'confirmed', 'failed_no_effect', 'outcome_unknown', 'cancelled'],
  action: ['prepared', 'awaiting_approval', 'ready', 'dispatch_started', 'confirmed', 'failed_no_effect', 'outcome_unknown', 'cancelled'],
  approval: ['pending', 'approved', 'denied', 'revoked'], task_command: ['pause', 'resume', 'cancel', 'redirect'], usage_entry: ['reserved', 'confirmed', 'unknown', 'released'],
}
function isItem(value: unknown): value is ActivityItem {
  if (!exact(value, itemKeys) || !uuid(value.id) || !count(value.seq) || value.seq < 1 || !timestamp(value.occurred_at)
    || !['task', 'run', 'model_call', 'action', 'approval', 'task_command', 'usage_entry'].includes(String(value.entity_type))
    || !uuid(value.entity_id) || !nullableUuid(value.task_id) || !nullableUuid(value.run_id) || !nullableUuid(value.action_id)
    || !(value.task_title === null || typeof value.task_title === 'string' && Array.from(value.task_title).length <= 200)
    || (value.task_id === null) !== (value.task_title === null) || value.run_id !== null && value.task_id === null
    || !word(value.status) || !statuses[value.entity_type as ActivityItem['entity_type']].includes(value.status) || !count(value.revision) || value.revision < 1 || !(value.error_code === null || word(value.error_code))
    || value.entity_type === 'task' && value.task_id !== value.entity_id || value.entity_type === 'run' && value.run_id !== value.entity_id
    || value.entity_type === 'action' && value.action_id !== value.entity_id || value.entity_type === 'model_call' && value.run_id === null
    || !exact(value.environment, ['kind', 'environment_id']) || !['textual', 'unassociated'].includes(String(value.environment.kind)) || value.environment.environment_id !== null) return false
  if (value.usage === null) return value.entity_type !== 'usage_entry'
  const usage = value.usage
  return value.entity_type === 'usage_entry' && exact(usage, ['status', 'source', 'usage_kind', 'reserved_tokens', 'counted_tokens', 'input_tokens', 'output_tokens', 'total_tokens', 'estimate_method'])
    && ['reserved', 'confirmed', 'unknown', 'released'].includes(String(usage.status)) && ['chat', 'task'].includes(String(usage.source))
    && (usage.usage_kind === null || ['reported', 'estimated', 'unknown'].includes(String(usage.usage_kind)))
    && count(usage.reserved_tokens) && count(usage.counted_tokens) && nullableCount(usage.input_tokens) && nullableCount(usage.output_tokens) && nullableCount(usage.total_tokens) && ['chars_div_3_v1', 'unknown'].includes(String(usage.estimate_method))
    && usage.status === value.status && (usage.source === 'task' ? value.task_id !== null && value.run_id !== null : value.task_id === null && value.run_id === null)
    && (usage.status === 'confirmed' ? usage.usage_kind !== null : usage.usage_kind === null && usage.input_tokens === null && usage.output_tokens === null && usage.total_tokens === null)
    && (usage.status === 'released' ? usage.counted_tokens === 0 : usage.status === 'reserved' || usage.status === 'unknown' ? usage.counted_tokens === usage.reserved_tokens : true)
}
export async function getActivity(agentId: string, query: ActivityQuery = {}, signal?: AbortSignal): Promise<ActivityPage> {
  if (!uuid(agentId) || query.task_id !== undefined && !uuid(query.task_id) || query.run_id !== undefined && (!uuid(query.run_id) || !query.task_id)
    || Object.keys(query).some((key) => !['task_id', 'run_id', 'since', 'until', 'before', 'snapshot_seq', 'window_seconds'].includes(key))
    || query.window_seconds !== undefined && (![86400, 604800, 2592000].includes(query.window_seconds) || query.since !== undefined || query.until !== undefined || query.before !== undefined)
    || query.before !== undefined && (!count(query.before) || query.before < 1) || query.since !== undefined && !timestamp(query.since)
    || query.before !== undefined && (!count(query.snapshot_seq) || !query.since || !query.until) || query.snapshot_seq !== undefined && query.before === undefined
    || query.until !== undefined && !timestamp(query.until) || query.since && query.until && instant(query.since) >= instant(query.until)) throw new ApiError('invalid_request')
  const params = new URLSearchParams({ limit: '50' })
  Object.entries(query).forEach(([key, value]) => { if (value !== undefined) params.set(key, String(value)) })
  const value = await request(`/agents/${encodeURIComponent(agentId)}/activity?${params}`, { authenticated: true, signal })
  if (!exact(value, ['agent_id', 'snapshot', 'scope', 'interval', 'unit', 'usage', 'operations', 'items', 'next_before']) || value.agent_id !== agentId
    || !isObservation(value.snapshot) || !isInterval(value.interval) || value.unit !== 'tokens' || !exact(value.scope, ['task_id', 'run_id'])
    || value.scope.task_id !== (query.task_id ?? null) || value.scope.run_id !== (query.run_id ?? null)
    || query.since && instant(value.interval.since) !== instant(query.since) || query.until && instant(value.interval.until) !== instant(query.until)
    || query.snapshot_seq !== undefined && value.snapshot.event_seq !== query.snapshot_seq
    || query.window_seconds !== undefined && instant(value.interval.until) - instant(value.interval.since) !== BigInt(query.window_seconds) * 1_000_000_000n
    || !exact(value.usage, usageKeys) || !usageKeys.filter((key) => key !== 'estimate_methods').every((key) => count((value.usage as Record<string, unknown>)[key]))
    || !Array.isArray(value.usage.estimate_methods) || value.usage.estimate_methods.length > 2 || !value.usage.estimate_methods.every((method) => ['chars_div_3_v1', 'unknown'].includes(String(method))) || new Set(value.usage.estimate_methods).size !== value.usage.estimate_methods.length
    || !exact(value.operations, operationKeys) || !operationKeys.every((key) => count((value.operations as Record<string, unknown>)[key]))
    || !Array.isArray(value.items) || value.items.length > 50 || !value.items.every(isItem) || !(value.next_before === null || count(value.next_before) && value.next_before > 0)) throw new ApiError('invalid_response')
  const items = value.items as ActivityItem[]
  if (new Set(items.map((item) => item.id)).size !== items.length || items.some((item, index) => item.seq > (value.snapshot as Observation).event_seq
    || query.before !== undefined && item.seq >= query.before || index > 0 && item.seq >= items[index - 1].seq
    || query.task_id && item.task_id !== query.task_id || query.run_id && item.run_id !== query.run_id
    || instant(item.occurred_at) < instant((value.interval as Interval).since) || instant(item.occurred_at) > instant((value.interval as Interval).until))
    || value.next_before !== null && (items.length === 0 || value.next_before !== items.at(-1)?.seq)) throw new ApiError('invalid_response')
  return value as unknown as ActivityPage
}
