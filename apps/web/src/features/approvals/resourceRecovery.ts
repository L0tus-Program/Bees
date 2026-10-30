import { checkedResourceIntent } from '../../api/resourceTools'
import type { ResourceIntent } from '../../api/resourceTools'
import { uuid } from '../../api/activity'

const prefix = (agentId: string, taskId: string) => `bees.resource-tool.pending.v1.${agentId}.${taskId}.`
const key = (agentId: string, taskId: string, approvalId: string) => prefix(agentId, taskId) + approvalId
export function restoreResourceIntent(agentId: string, taskId: string, approvalId: string) {
  let raw: string | null
  try { raw = sessionStorage.getItem(key(agentId, taskId, approvalId)) } catch { return { intent: null, corrupt: false } }
  try { return { intent: raw ? checkedResourceIntent(JSON.parse(raw), agentId, taskId, approvalId) : null, corrupt: false } }
  catch { return { intent: null, corrupt: true } }
}
export function preserveResourceIntent(intent: ResourceIntent) {
  sessionStorage.setItem(key(intent.agentId, intent.taskId, intent.approvalId), JSON.stringify(checkedResourceIntent(intent, intent.agentId, intent.taskId, intent.approvalId)))
}
export function clearResourceIntent(agentId: string, taskId: string, approvalId: string) {
  sessionStorage.removeItem(key(agentId, taskId, approvalId))
}
export function pendingResourceIds(agentId: string, taskId: string): string[] {
  try {
    const result = new Set<string>()
    for (let i = 0; i < sessionStorage.length; i++) {
      const name = sessionStorage.key(i)
      if (name?.startsWith(prefix(agentId, taskId))) {
        const id = name.slice(prefix(agentId, taskId).length)
        if (uuid(id) && result.size < 32) result.add(id)
      }
    }
    return [...result]
  } catch { return [] }
}
