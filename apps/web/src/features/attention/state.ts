import type { AttentionItem } from '../../api/attention'
import { ApiError } from '../../api/client'

// A revisão humana só cresce; vínculo e evento de origem são imutáveis.
export function mergeAttention(previous: AttentionItem[], incoming: AttentionItem[], append = false): AttentionItem[] {
  const stored = new Map(previous.map((item) => [item.id, item]))
  const next = incoming.map((item) => {
    const old = stored.get(item.id)
    if (!old) return item
    if (old.agent_id !== item.agent_id || old.task_id !== item.task_id || old.approval_id !== item.approval_id || old.kind !== item.kind || old.created_at !== item.created_at
      || item.revision < old.revision || (item.revision === old.revision && item.read !== old.read)) throw new ApiError('invalid_response')
    return item
  })
  return append ? [...previous.filter((item) => !incoming.some((value) => value.id === item.id)), ...next] : next
}
