import { createHash } from 'node:crypto'
import { skill } from './fixtures'
import { toolTaskReceipt } from '../tasks/toolTaskFixtures'
import type { SkillTaskBody, SkillTaskIntent } from '../../api/skillTasks'

export const runnableSkill = { ...skill, enabled_version: 1, current: { ...skill.current, definition: { ...skill.current.definition, inputs: [{ name: 'texto', description: 'Texto próprio', required: true }], resources: { environment: 'own' as const, tools: ['text.normalize'] } } } }
export const skillTaskIntent: SkillTaskIntent = { agentId: skill.agent_id, body: { client_request_id: '55555555-5555-4555-8555-555555555555', skill_id: skill.id, skill_version: 1, inputs: { texto: 'Olá' }, max_calls: 4, max_active_seconds: 120 } }
function canonical(value: unknown): string {
  if (Array.isArray(value)) return '[' + value.map(canonical).join(',') + ']'
  if (typeof value === 'object' && value !== null) return '{' + Object.entries(value).sort(([a], [b]) => a < b ? -1 : 1).map(([key, item]) => JSON.stringify(key) + ':' + canonical(item)).join(',') + '}'
  return JSON.stringify(value)
}
export function skillTaskReceipt(body: SkillTaskBody = skillTaskIntent.body) {
  const base = toolTaskReceipt()
  return { task: { ...base.task, agent_id: skill.agent_id, title: runnableSkill.current.definition.name, skill_execution: { skill_id: body.skill_id, version: body.skill_version, snapshot_hash: 'a'.repeat(64) } }, client_request_id: body.client_request_id, request_hash: createHash('sha256').update(canonical(body)).digest('hex') }
}

export const agentRunnableSkill = { ...runnableSkill, current: { ...runnableSkill.current, definition: { ...runnableSkill.current.definition, resources: { environment: 'own' as const, tools: ['computer.files.read'] } } } }
export const agentSkillTaskIntent = { kind: 'agent' as const, agentId: skillTaskIntent.agentId, body: { ...skillTaskIntent.body, inputs: { ...skillTaskIntent.body.inputs }, max_calls: 20, max_active_seconds: 900 } }
export function agentSkillTaskReceipt(body = agentSkillTaskIntent.body) {
  const base = skillTaskReceipt()
  return { ...base, client_request_id: body.client_request_id, request_hash: createHash('sha256').update(canonical(body)).digest('hex'), task: { ...base.task, max_calls: body.max_calls, max_active_seconds: body.max_active_seconds, execution_kind: 'agent_task', agent_approvals_available: true, tool_approvals_available: false, skill_execution: { ...base.task.skill_execution, skill_id: body.skill_id, version: body.skill_version } } }
}
