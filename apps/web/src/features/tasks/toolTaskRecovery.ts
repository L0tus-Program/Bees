import { checkedToolTaskIntent } from '../../api/toolTasks'
import type { ToolTaskIntent } from '../../api/toolTasks'
const key = (agentId: string) => `bees.tool-task.pending.v1.${agentId}`
export function restoreToolTask(agentId: string): { intent: ToolTaskIntent | null; corrupt: boolean } {
  try {
    const raw = sessionStorage.getItem(key(agentId))
    if (!raw) return { intent: null, corrupt: false }
    try { return { intent: checkedToolTaskIntent(JSON.parse(raw), agentId), corrupt: false } }
    catch { return { intent: null, corrupt: true } }
  } catch { return { intent: null, corrupt: false } }
}
export function preserveToolTask(intent: ToolTaskIntent) { sessionStorage.setItem(key(intent.agentId), JSON.stringify(checkedToolTaskIntent(intent, intent.agentId))) }
export function clearToolTask(agentId: string) { sessionStorage.removeItem(key(agentId)) }
