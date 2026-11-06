import { ApiError } from './client'
import { checkedSkillIntent } from './skills'
import type { SkillIntent } from './skills'

const key = (agentId: string) => `bees.skill-library.pending.v1.${agentId}`

function checked(value: unknown, agentId: string): SkillIntent {
  const intent = checkedSkillIntent(value, agentId)
  // Process conversion has its own recovery with human source review.
  if (intent.path.endsWith('/from-process')) throw new ApiError('invalid_request')
  return intent
}

export function restoreSkillMutation(agentId: string): { intent: SkillIntent | null; corrupt: boolean } {
  try {
    const raw = sessionStorage.getItem(key(agentId))
    return { intent: raw === null ? null : checked(JSON.parse(raw), agentId), corrupt: false }
  } catch { return { intent: null, corrupt: true } }
}

export function preserveSkillMutation(intent: SkillIntent): SkillIntent {
  const snapshot = checked(intent, intent.agentId)
  sessionStorage.setItem(key(snapshot.agentId), JSON.stringify(snapshot))
  return snapshot
}

export function clearSkillMutation(agentId: string) { sessionStorage.removeItem(key(agentId)) }
