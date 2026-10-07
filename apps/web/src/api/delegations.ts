import { ApiError, record, request } from './client'
import { isTask } from './tasks'
import type { TaskRecord } from './tasks'

export interface Delegation { id: string; source_message_id: string; response_message_id: string; task: TaskRecord }
export interface DelegationCollection { delegations: Delegation[]; has_more: boolean; next_offset: number | null }

export async function getDelegations(agentId: string, conversationId: string, signal?: AbortSignal): Promise<DelegationCollection> {
  const value = await request(`/agents/${encodeURIComponent(agentId)}/delegations?conversation_id=${encodeURIComponent(conversationId)}&offset=0&limit=100`, { authenticated: true, signal })
  if (!record(value) || !Array.isArray(value.delegations) || value.delegations.length > 100 || typeof value.has_more !== 'boolean'
    || !(value.next_offset === null || Number.isSafeInteger(value.next_offset) && (value.next_offset as number) > 0)
    || value.has_more !== (value.next_offset !== null)) throw new ApiError('invalid_response')
  const delegations = value.delegations.map((item) => {
    if (!record(item) || !['id', 'source_message_id', 'response_message_id'].every((key) => typeof item[key] === 'string' && item[key].length > 0)
      || !isTask(item.task) || item.task.agent_id !== agentId || item.id !== item.task.id) throw new ApiError('invalid_response')
    return { id: item.id, source_message_id: item.source_message_id, response_message_id: item.response_message_id, task: item.task } as Delegation
  })
  if (new Set(delegations.map((item) => item.id)).size !== delegations.length) throw new ApiError('invalid_response')
  return { delegations, has_more: value.has_more, next_offset: value.next_offset as number | null }
}
