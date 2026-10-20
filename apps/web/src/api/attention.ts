import { ApiError, record, request } from './client'

export const attentionKinds = ['task_completed', 'task_failed', 'approval_required', 'outcome_unknown'] as const
export type AttentionKind = typeof attentionKinds[number]
export interface AttentionItem {
  id: string; agent_id: string; task_id: string; task_title: string; approval_id: string | null
  kind: AttentionKind; created_at: string; revision: number; read: boolean; requires_attention: boolean; muted: boolean
}
export interface AttentionPage { items: AttentionItem[]; next_before: number | null; unread_count: number; conversation_id?: string }
export interface AttentionConversation { id: string; title: string; created_at: string }
export interface AttentionConversations { agent_id: string; items: AttentionConversation[]; next_offset: number | null }
export interface AttentionPreference { kind: AttentionKind; enabled: boolean; revision: number }
export interface ReadCommand { client_request_id: string; expected_revision: number; read: boolean }
export interface PreferenceCommand { client_request_id: string; expected_revision: number; enabled: boolean }

const exact = (value: Record<string, unknown>, keys: string[]) => Object.keys(value).sort().join() === [...keys].sort().join()
const count = (value: unknown): value is number => Number.isSafeInteger(value) && (value as number) >= 0
const uuid = (value: unknown): value is string => typeof value === 'string' && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(value)
const kind = (value: unknown): value is AttentionKind => attentionKinds.includes(value as AttentionKind)
export function isAttentionItem(value: unknown): value is AttentionItem {
  return record(value) && exact(value, ['id', 'agent_id', 'task_id', 'task_title', 'approval_id', 'kind', 'created_at', 'revision', 'read', 'requires_attention', 'muted'])
    && uuid(value.id) && uuid(value.agent_id) && uuid(value.task_id) && kind(value.kind)
    && typeof value.task_title === 'string' && value.task_title.length > 0 && Array.from(value.task_title).length <= 200
    && (value.kind === 'approval_required' ? uuid(value.approval_id) : value.approval_id === null)
    && typeof value.created_at === 'string' && /(?:Z|[+-]\d{2}:\d{2})$/.test(value.created_at) && !Number.isNaN(Date.parse(value.created_at))
    && count(value.revision) && value.revision > 0 && ['read', 'requires_attention', 'muted'].every((key) => typeof value[key] === 'boolean')
}
function isPreference(value: unknown): value is AttentionPreference {
  return record(value) && exact(value, ['kind', 'enabled', 'revision']) && kind(value.kind) && typeof value.enabled === 'boolean' && count(value.revision)
}
const path = (agentId: string) => `/agents/${encodeURIComponent(agentId)}/attention`

function scopeQuery(conversationId?: string) {
  if (conversationId === undefined) return ''
  if (!uuid(conversationId) || conversationId === '00000000-0000-0000-0000-000000000000') throw new ApiError('invalid_request')
  return `conversation_id=${conversationId}`
}
export async function getAttentionConversations(agentId: string, signal?: AbortSignal, offset = 0): Promise<AttentionConversations> {
  if (!count(offset)) throw new ApiError('invalid_request')
  const value = await request(`${path(agentId)}/conversations?limit=50&offset=${offset}`, { authenticated: true, signal })
  if (!record(value) || !exact(value, ['agent_id', 'items', 'next_offset']) || value.agent_id !== agentId || !Array.isArray(value.items) || value.items.length > 50
    || !value.items.every((item) => record(item) && exact(item, ['id', 'title', 'created_at']) && uuid(item.id) && item.id !== '00000000-0000-0000-0000-000000000000' && typeof item.title === 'string' && Array.from(item.title).length <= 200 && typeof item.created_at === 'string' && /(?:Z|[+-]\d{2}:\d{2})$/.test(item.created_at) && !Number.isNaN(Date.parse(item.created_at)))
    || new Set(value.items.map((item: AttentionConversation) => item.id)).size !== value.items.length
    || value.next_offset !== null && (value.items.length !== 50 || value.next_offset !== offset + 50 || !count(value.next_offset))) throw new ApiError('invalid_response')
  return value as unknown as AttentionConversations
}
export async function getAttention(agentId: string, signal?: AbortSignal, before?: number, conversationId?: string): Promise<AttentionPage> {
  const scope = scopeQuery(conversationId)
  const value = await request(`${path(agentId)}?limit=50${before === undefined ? '' : `&before=${before}`}${scope ? `&${scope}` : ''}`, { authenticated: true, signal })
  if (!record(value) || !exact(value, ['items', 'next_before', 'unread_count', ...(conversationId ? ['conversation_id'] : [])]) || conversationId !== undefined && value.conversation_id !== conversationId || !Array.isArray(value.items) || value.items.length > 50
    || !value.items.every((item) => isAttentionItem(item) && item.agent_id === agentId) || !count(value.unread_count)
    || (value.next_before !== null && (!count(value.next_before) || value.next_before === 0 || value.items.length === 0 || (before !== undefined && value.next_before >= before)))
    || new Set(value.items.map((item: AttentionItem) => item.id)).size !== value.items.length) throw new ApiError('invalid_response')
  return value as unknown as AttentionPage
}
export async function getAttentionPreferences(agentId: string, signal?: AbortSignal): Promise<AttentionPreference[]> {
  const value = await request(`${path(agentId)}/preferences`, { authenticated: true, signal })
  if (!record(value) || !exact(value, ['agent_id', 'items']) || value.agent_id !== agentId || !Array.isArray(value.items) || value.items.length !== attentionKinds.length
    || !value.items.every(isPreference) || new Set(value.items.map((item: AttentionPreference) => item.kind)).size !== attentionKinds.length) throw new ApiError('invalid_response')
  return value.items
}
export async function setAttentionRead(agentId: string, snapshot: AttentionItem, body: ReadCommand, signal?: AbortSignal, conversationId?: string): Promise<AttentionItem> {
  const scope = scopeQuery(conversationId)
  const value = await request(`${path(agentId)}/${encodeURIComponent(snapshot.id)}/read${scope ? `?${scope}` : ''}`, { method: 'PUT', body, authenticated: true, signal })
  if (!isAttentionItem(value) || value.agent_id !== agentId || value.id !== snapshot.id || value.task_id !== snapshot.task_id
    || value.kind !== snapshot.kind || value.approval_id !== snapshot.approval_id || value.created_at !== snapshot.created_at
    || value.revision <= body.expected_revision) throw new ApiError('invalid_response')
  if (value.read !== body.read) throw new ApiError('attention_conflict', 409)
  return value
}
export async function setAttentionPreference(agentId: string, target: AttentionKind, body: PreferenceCommand, signal?: AbortSignal): Promise<AttentionPreference> {
  const value = await request(`${path(agentId)}/preferences/${target}`, { method: 'PUT', body, authenticated: true, signal })
  if (!isPreference(value) || value.kind !== target || value.revision <= body.expected_revision) throw new ApiError('invalid_response')
  if (value.enabled !== body.enabled) throw new ApiError('attention_conflict', 409)
  return value
}
