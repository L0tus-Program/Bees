import { ApiError, record, request } from './client'
import { exact, uuid } from './activity'
import { digest } from './toolTasks'
import { isTask } from './tasks'
import type { TaskRecord } from './tasks'
import { isDefinition } from './skills'
import type { Skill, SkillDefinition } from './skills'

export function executableSkill(skill: Skill) {
  const resource = skill.current.definition.resources
  return skill.enabled_version === skill.current_version && ((resource.environment === 'none' && resource.tools.length === 0) || (resource.environment === 'own' && resource.tools.length === 1 && resource.tools[0] === 'text.normalize'))
}

export interface SkillTaskBody { client_request_id: string; skill_id: string; skill_version: number; inputs: Record<string, string>; max_calls: 4; max_active_seconds: 120 }
export interface SkillTaskIntent { agentId: string; body: SkillTaskBody }
const identity = (value: unknown): value is string => uuid(value) && value === value.toLowerCase() && value !== '00000000-0000-0000-0000-000000000000'
export function checkedSkillTaskIntent(value: unknown, agentId: string): SkillTaskIntent {
  if (!identity(agentId) || !exact(value, ['agentId', 'body']) || value.agentId !== agentId
    || !exact(value.body, ['client_request_id', 'skill_id', 'skill_version', 'inputs', 'max_calls', 'max_active_seconds'])
    || !identity(value.body.client_request_id) || !identity(value.body.skill_id) || !Number.isSafeInteger(value.body.skill_version) || Number(value.body.skill_version) < 1
    || !record(value.body.inputs) || Object.keys(value.body.inputs).length > 20 || !Object.entries(value.body.inputs).every(([key, input]) => /^[a-z][a-z0-9_.-]{0,63}$/.test(key) && typeof input === 'string' && input.trim().length > 0 && Array.from(input).length <= 2048 && !Array.from(input).some((char) => char.charCodeAt(0) < 32 && !['\n', '\r', '\t'].includes(char)))
    || value.body.max_calls !== 4 || value.body.max_active_seconds !== 120 || new TextEncoder().encode(JSON.stringify(value.body)).length > 8192) throw new ApiError('invalid_request')
  return value as unknown as SkillTaskIntent
}
export async function createSkillTask(intent: SkillTaskIntent, signal?: AbortSignal): Promise<TaskRecord> {
  checkedSkillTaskIntent(intent, intent.agentId)
  const result = await request(`/agents/${intent.agentId}/skill-tasks`, { body: intent.body, authenticated: true, signal })
  if (!exact(result, ['task', 'client_request_id', 'request_hash']) || result.client_request_id !== intent.body.client_request_id || result.request_hash !== await digest(intent.body)
    || !isTask(result.task) || result.task.agent_id !== intent.agentId || !identity(result.task.id) || !identity(result.task.conversation_id) || !identity(result.task.active_run_id)
    || result.task.max_calls !== 4 || result.task.max_active_seconds !== 120 || result.task.model_approvals_available !== true || result.task.tool_approvals_available !== true
    || !exact(result.task.skill_execution, ['skill_id', 'version', 'snapshot_hash']) || result.task.skill_execution.skill_id !== intent.body.skill_id || result.task.skill_execution.version !== intent.body.skill_version
    || typeof result.task.skill_execution.snapshot_hash !== 'string' || !/^[0-9a-f]{64}$/.test(result.task.skill_execution.snapshot_hash)) throw new ApiError('invalid_response')
  return result.task
}
const key = (agentId: string) => `bees.skill-task.pending.v1.${agentId}`
export function restoreSkillTask(agentId: string): { intent: SkillTaskIntent | null; corrupt: boolean } {
  try {
    const raw = sessionStorage.getItem(key(agentId))
    if (!raw) return { intent: null, corrupt: false }
    try { return { intent: checkedSkillTaskIntent(JSON.parse(raw), agentId), corrupt: false } }
    catch { return { intent: null, corrupt: true } }
  } catch { return { intent: null, corrupt: false } }
}
export function preserveSkillTask(intent: SkillTaskIntent) { sessionStorage.setItem(key(intent.agentId), JSON.stringify(checkedSkillTaskIntent(intent, intent.agentId))) }
export function clearSkillTask(agentId: string) { sessionStorage.removeItem(key(agentId)) }

export async function getSkillContext(agentId: string, taskId: string, binding: NonNullable<TaskRecord['skill_execution']>, signal?: AbortSignal): Promise<{ definition: SkillDefinition; inputs: Record<string, string>; version: number }> {
  if (!identity(agentId) || !identity(taskId)) throw new ApiError('invalid_request')
  const result = await request(`/agents/${agentId}/tasks/${taskId}/skill-context`, { authenticated: true, signal })
  if (!exact(result, ['snapshot', 'snapshot_hash']) || result.snapshot_hash !== binding.snapshot_hash
    || !exact(result.snapshot, ['skill_id', 'version', 'definition', 'definition_hash', 'inputs']) || result.snapshot.skill_id !== binding.skill_id || result.snapshot.version !== binding.version
    || !isDefinition(result.snapshot.definition) || result.snapshot.definition_hash !== await digest(result.snapshot.definition) || await digest(result.snapshot) !== result.snapshot_hash || !record(result.snapshot.inputs)
    || !Object.values(result.snapshot.inputs).every((value) => typeof value === 'string')) throw new ApiError('invalid_response')
  return { definition: result.snapshot.definition, inputs: result.snapshot.inputs as Record<string, string>, version: binding.version }
}

// Skill com ferramentas do registro (pesquisa, computador, máquina pessoal) roda no agente.
export function agentSkill(skill: Skill) {
  const tools = skill.current.definition.resources.tools
  return skill.enabled_version === skill.current_version && tools.length > 0 && !tools.includes('text.normalize')
}
export interface AgentSkillBody { client_request_id: string; skill_id: string; skill_version: number; inputs: Record<string, string>; max_calls?: number; max_active_seconds?: number }
export async function createAgentSkillTask(agentId: string, body: AgentSkillBody, signal?: AbortSignal): Promise<TaskRecord> {
  const result = await request(`/agents/${encodeURIComponent(agentId)}/agent-skill-tasks`, { body, authenticated: true, signal, timeoutMs: 20000 })
  if (!record(result) || !isTask(result.task) || result.task.agent_id !== agentId || result.client_request_id !== body.client_request_id) throw new ApiError('invalid_response')
  return result.task
}
