import { checkedRoutineIntent } from '../../api/routines'
import type { RoutineIntent } from '../../api/routines'

const key = (agentId: string) => `bees.routine-pending.v1.${agentId}`
export function readRoutineRecovery(agentId: string): { intent: RoutineIntent | null; corrupt: boolean } {
  try {
    const value = sessionStorage.getItem(key(agentId))
    if (!value) return { intent: null, corrupt: false }
    try { return { intent: checkedRoutineIntent(JSON.parse(value), agentId), corrupt: false } } catch { return { intent: null, corrupt: true } }
  } catch { return { intent: null, corrupt: false } }
}
export function restoreRoutineIntent(agentId: string): RoutineIntent | null { return readRoutineRecovery(agentId).intent }
export function preserveRoutineIntent(intent: RoutineIntent): boolean {
  try { sessionStorage.setItem(key(intent.agentId), JSON.stringify(checkedRoutineIntent(intent, intent.agentId))); return true } catch { return false }
}
export function clearRoutineIntent(agentId: string) {
  try { sessionStorage.removeItem(key(agentId)) } catch { /* A pending in-memory intent remains usable when storage is unavailable. */ }
}
