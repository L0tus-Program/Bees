import { checkedRoutineIntent } from '../../api/routines'
import type { RoutineIntent } from '../../api/routines'

const key = (agentId: string) => `bees.routine-pending.v1.${agentId}`
export function restoreRoutineIntent(agentId: string): RoutineIntent | null {
  try {
    const value = sessionStorage.getItem(key(agentId))
    return value ? checkedRoutineIntent(JSON.parse(value), agentId) : null
  } catch { return null }
}
export function preserveRoutineIntent(intent: RoutineIntent): boolean {
  try { sessionStorage.setItem(key(intent.agentId), JSON.stringify(checkedRoutineIntent(intent, intent.agentId))); return true } catch { return false }
}
export function clearRoutineIntent(agentId: string) {
  try { sessionStorage.removeItem(key(agentId)) } catch { /* A pending in-memory intent remains usable when storage is unavailable. */ }
}
