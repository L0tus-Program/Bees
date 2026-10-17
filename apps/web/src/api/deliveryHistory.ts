import { ApiError, request } from './client'
import { count, exact, timestamp, uuid } from './activity'
import { checkedArtifact } from '../features/artifacts/api'
import type { ArtifactRecord } from '../features/artifacts/api'

export interface DeliveryItem {
  kind: 'result' | 'artifact'; id: string; created_at: string; agent_id: string; conversation_id: string | null
  task_id: string | null; title: string; run_id: string | null; version: number; revision: number
  status: string; partial: boolean; summary: string
}
export interface DeliveryScope { query: string; conversation_id: string | null; task_id: string | null }
export interface DeliveryPage { agent_id: string; scope: DeliveryScope; items: DeliveryItem[]; next_cursor: string | null; limit: number; until: string }
export type DeliveryContent = { kind: 'result'; item: DeliveryItem; content: string } | { kind: 'artifact'; artifact: ArtifactRecord }
const keys = ['kind', 'id', 'created_at', 'agent_id', 'conversation_id', 'task_id', 'title', 'run_id', 'version', 'revision', 'status', 'partial', 'summary']
const nullableUuid = (value: unknown) => value === null || uuid(value)
export function checkedDelivery(value: unknown, agentId: string): DeliveryItem {
  if (!exact(value, keys) || !['result', 'artifact'].includes(String(value.kind)) || !uuid(value.id) || value.agent_id !== agentId
    || !timestamp(value.created_at) || !nullableUuid(value.conversation_id) || !nullableUuid(value.task_id) || !nullableUuid(value.run_id)
    || typeof value.title !== 'string' || Array.from(value.title).length > 200 || !value.title.trim()
    || !count(value.version) || value.version < 1 || !count(value.revision) || value.revision < 1
    || typeof value.partial !== 'boolean' || typeof value.summary !== 'string' || Array.from(value.summary).length > 300
    || (value.kind === 'result' ? value.status !== 'completed' || value.task_id === null || value.run_id === null || value.conversation_id === null
      : !['draft', 'ready', 'failed', 'deleted'].includes(String(value.status)) || value.task_id === null && value.conversation_id === null)) throw new ApiError('invalid_response')
  return value as unknown as DeliveryItem
}
export async function getDeliveries(agentId: string, scope: DeliveryScope, cursor: string | null = null, signal?: AbortSignal): Promise<DeliveryPage> {
  if (!uuid(agentId) || !nullableUuid(scope.task_id) || !nullableUuid(scope.conversation_id) || scope.query.length > 200 || scope.query.includes('\0') || cursor !== null && (typeof cursor !== 'string' || cursor.length > 2048)) throw new ApiError('invalid_request')
  const parameters = new URLSearchParams({ q: scope.query.trim(), limit: '50' })
  if (scope.conversation_id) parameters.set('conversation_id', scope.conversation_id)
  if (scope.task_id) parameters.set('task_id', scope.task_id)
  if (cursor) parameters.set('cursor', cursor)
  const value = await request(`/agents/${agentId}/deliveries?${parameters}`, { authenticated: true, signal })
  if (!exact(value, ['agent_id', 'scope', 'items', 'next_cursor', 'limit', 'until']) || value.agent_id !== agentId
    || !exact(value.scope, ['query', 'conversation_id', 'task_id']) || value.scope.query !== scope.query.trim() || value.scope.conversation_id !== scope.conversation_id || value.scope.task_id !== scope.task_id
    || !Array.isArray(value.items) || value.items.length > 50 || value.limit !== 50 || !timestamp(value.until)
    || !(value.next_cursor === null || typeof value.next_cursor === 'string' && value.next_cursor.length > 0 && value.next_cursor.length <= 2048 && value.items.length === 50)
    || cursor !== null && value.next_cursor === cursor) throw new ApiError('invalid_response')
  const items = value.items.map((item) => checkedDelivery(item, agentId))
  if (new Set(items.map((item) => `${item.kind}:${item.id}`)).size !== items.length || items.some((item) => scope.task_id !== null && item.task_id !== scope.task_id || scope.conversation_id !== null && item.conversation_id !== scope.conversation_id)) throw new ApiError('invalid_response')
  return { ...value, items } as DeliveryPage
}
export async function getDelivery(agentId: string, expected: DeliveryItem, signal?: AbortSignal): Promise<DeliveryContent> {
  if (!uuid(agentId) || expected.agent_id !== agentId || !uuid(expected.id)) throw new ApiError('invalid_request')
  if (expected.kind === 'artifact') {
    const artifact = checkedArtifact(await request(`/agents/${agentId}/deliveries/artifacts/${expected.id}`, { authenticated: true, signal }), agentId)
    if (artifact.id !== expected.id || artifact.version !== expected.version || artifact.task_id !== expected.task_id || artifact.conversation_id !== expected.conversation_id || artifact.run_id !== expected.run_id || artifact.revision < expected.revision) throw new ApiError('invalid_response')
    return { kind: 'artifact', artifact }
  }
  if (!uuid(expected.run_id)) throw new ApiError('invalid_request')
  const value = await request(`/agents/${agentId}/deliveries/results/${expected.id}?run_id=${expected.run_id}`, { authenticated: true, signal })
  if (!exact(value, ['item', 'content']) || typeof value.content !== 'string') throw new ApiError('invalid_response')
  const item = checkedDelivery(value.item, agentId)
  if (item.kind !== 'result' || item.id !== expected.id || item.run_id !== expected.run_id || item.task_id !== expected.task_id || item.conversation_id !== expected.conversation_id || item.version !== expected.version || item.revision !== expected.revision) throw new ApiError('invalid_response')
  return { kind: 'result', item, content: value.content }
}
/** Explicit identifiers for a human reply; preparing a reference performs no mutation. */
export function deliveryReference(item: DeliveryItem) {
  return `bees:delivery/${item.kind}/${item.id}?agent=${item.agent_id}${item.task_id ? `&task=${item.task_id}` : ''}${item.run_id ? `&run=${item.run_id}` : ''}${item.conversation_id ? `&conversation=${item.conversation_id}` : ''}&version=${item.version}`
}
